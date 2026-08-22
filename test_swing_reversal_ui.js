// test_swing_reversal_ui.js (v4.50) — guards the Swing Reversal block, which
// replaced the score-derived "three paths" card (and its guard,
// test_swing_paths.js). What a source-level guard can honestly enforce:
//
//   1. The dead card stays dead. computeSwingPrediction manufactured path
//      "probabilities" out of continuation/exhaustion scores — the exact
//      confusion (§ completion is not a probability) the reversal block
//      exists to remove. It must not quietly come back half-alive.
//   2. Every REV_TIP key the JSX references exists — an element whose
//      tooltip key is missing renders with an undefined title.
//   3. New-surface dates are spelled out (fmtLongDate), never ISO.
//   4. The forbidden equivalence never renders: no source line may present
//      a completion percentage as a probability of reversing.
//
// Run from the repo dir:  node test_swing_reversal_ui.js
const fs = require("fs");
const path = require("path");

let passed = 0, failed = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — got: " + extra : "")); }
}

const src = fs.readFileSync(path.join(__dirname, "app-cards.jsx"), "utf8");

// 1 — the score-derived paths card stays removed.
ok("computeSwingPrediction is gone", src.indexOf("computeSwingPrediction") === -1);
ok("SwingPrediction is gone", src.indexOf("function SwingPrediction") === -1
   && src.indexOf("<SwingPrediction") === -1);
ok("no probability-weighted path card", src.indexOf("probability-weighted") === -1);

// 2 — tooltip coverage: every REV_TIP.<key> reference resolves.
const tipBlock = src.slice(src.indexOf("const REV_TIP = {"),
                           src.indexOf("};", src.indexOf("const REV_TIP = {")));
const defined = new Set();
tipBlock.replace(/^\s{2}(\w+):/gm, (m, k) => { defined.add(k); return m; });
const used = new Set();
src.replace(/REV_TIP\.(\w+)/g, (m, k) => { used.add(k); return m; });
const missing = [...used].filter(k => !defined.has(k));
ok("every referenced REV_TIP key is defined", missing.length === 0, missing.join(","));
ok("a real tooltip vocabulary exists (10+ entries)", defined.size >= 10, String(defined.size));

// 3 — fmtLongDate spells months out; the reversal block never renders ISO.
const fnStart = src.indexOf("function fmtLongDate");
ok("fmtLongDate exists", fnStart >= 0);
if (fnStart >= 0) {
  const fnEnd = src.indexOf("\n}", fnStart) + 2;
  const fmtLongDate = new Function(src.slice(fnStart, fnEnd) + "\nreturn fmtLongDate;")();
  ok("fmtLongDate spells the month", fmtLongDate("2026-08-20") === "August 20, 2026",
     fmtLongDate("2026-08-20"));
  ok("fmtLongDate passes junk through", fmtLongDate("") === "—" || typeof fmtLongDate("") === "string");
}
const revBlock = src.slice(src.indexOf("function SwingReversalBlock"),
                           src.indexOf("function SwingReversalScan"));
ok("the reversal block renders dates through fmtLongDate",
   revBlock.indexOf("fmtLongDate(") >= 0);

// 4 — the forbidden equivalence: completion is never labeled a probability.
const compTip = tipBlock.match(/completion:\s*"([^"]*)"/);
ok("the completion tooltip says it is NOT a probability",
   !!compTip && /does not mean .*chance of reversing/i.test(compTip[1]),
   compTip ? compTip[1].slice(0, 90) : "no completion tip");
ok("no line labels completion as a probability",
   !/completion[^\n]{0,60}probabilit/i.test(revBlock));

// 5 — the scan section exists with both modes and sortable headers.
const scanBlock = src.slice(src.indexOf("function SwingReversalScan"),
                            src.indexOf("function SwingPatternCard"));
ok("bounce and pullback modes exist",
   scanBlock.indexOf("Bounce candidates") >= 0 && scanBlock.indexOf("Pullback candidates") >= 0);
ok("scan headers sort on click", scanBlock.indexOf("setSortK") >= 0);
ok("scan columns disclose the sample size", /rz_n/.test(scanBlock));

console.log(`\n${passed}/${passed + failed} passed` + (failed ? ` — FAILED: ${fails.join(", ")}` : ""));
process.exit(failed ? 1 : 0);
