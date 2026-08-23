// test_swing_reversal_ui.js (v4.51) — guards the Swing Reversal block, which
// replaced the score-derived "three paths" card (and its guard,
// test_swing_paths.js). What a source-level guard can honestly enforce:
//
//   1. The dead card stays dead. computeSwingPrediction manufactured path
//      "probabilities" out of continuation/exhaustion scores — the exact
//      confusion (completion is not a probability) the reversal block
//      exists to remove. It must not quietly come back half-alive.
//   2. Every REV_TIP key the JSX references exists — an element whose
//      tooltip key is missing renders with an undefined title.
//   3. New-surface dates are spelled out (fmtLongDate), never ISO.
//   4. The forbidden equivalence never renders: no source line may present
//      a completion percentage as a probability of reversing.
//   5. (v4.51) The reading order Jerry actually uses: projection, chart,
//      what-if, history — with the older analytical layer collapsed rather
//      than deleted, and the what-if prefills tied to the SETUP so one
//      ticker's numbers cannot survive into another's.
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
const appSrc = fs.readFileSync(path.join(__dirname, "app.jsx"), "utf8");

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
["status", "extreme", "maturity", "paired", "horizon", "gap", "earnA", "typical"]
  .forEach(k => ok(`the v4.51 surface has a tooltip: ${k}`, defined.has(k)));

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
// The fixed-horizon rates are conditional on the turn having happened, and
// the tooltip has to say so or they read as the chance OF a turn.
const horizonTip = tipBlock.match(/horizon:\s*"([^"]*)"/);
ok("the horizon tooltip refuses to be read as the chance of a turn",
   !!horizonTip && /NOT AS THE CHANCE OF A TURN/i.test(horizonTip[1]));
// The retired lifetime ladder must not come back with it.
ok("the circular lifetime touch ladder is gone",
   revBlock.indexOf("nx.touch") === -1 && revBlock.indexOf("touch_floor_note") === -1);

// 5 — the reversal status drives both the block and the scan.
ok("the five reversal states are declared once", /REV_STATUS_TONE = \{/.test(src));
["BOUNCING OFF ZONE", "FADING OFF ZONE", "IN ZONE", "APPROACHING",
 "BEYOND TYPICAL ZONE"].forEach(s =>
  ok(`the UI knows the state: ${s}`, src.indexOf(`"${s}"`) >= 0));
ok("the block renders the status banner", /rev-status-code/.test(revBlock));
ok("the block separates the running extreme from the price",
   /off_extreme_pct/.test(revBlock) && /extreme_price/.test(revBlock));
ok("the block shows the unconditional band the status is measured against",
   /typical_zone/.test(src) && /usually end/.test(revBlock));
ok("the block shows the paired target band", /target_is_paired/.test(revBlock));
ok("the block shows fixed-horizon rates with their baseline",
   /horizon_touch/.test(revBlock) && /baseline_pct/.test(revBlock));

// 6 — the scan is organised by status, and keeps its two modes.
const scanBlock = src.slice(src.indexOf("function SwingReversalScan"),
                            src.indexOf("function SwingPatternCard"));
ok("bounce and pullback modes exist",
   scanBlock.indexOf("Bounce candidates") >= 0 && scanBlock.indexOf("Pullback candidates") >= 0);
ok("scan headers sort on click", scanBlock.indexOf("setSortK") >= 0);
ok("scan columns disclose the sample size", /rz_n/.test(scanBlock));
ok("the scan ranks on status by default", /useState\("status"\)/.test(scanBlock));
ok("the scan ordering is a comparator chain over named fields, not a score",
   /STATUS_RANK/.test(scanBlock) && /const byStatus/.test(scanBlock)
   && !/\bscore\s*[+*]?=/.test(scanBlock));
ok("the scan surfaces the reaction states",
   /BOUNCING OFF ZONE/.test(scanBlock) && /FADING OFF ZONE/.test(scanBlock));
ok("the scan reads the running extreme, not only the close",
   /rz_extreme_price/.test(scanBlock) && /rz_off_extreme_pct/.test(scanBlock)
   && /rz_zone_touched/.test(scanBlock));
["rz_status", "rz_more_move_pct", "rz_days_median", "rz_next_target",
 "rz_median_price", "rz_penetration", "rz_stage"].forEach(k =>
  ok(`the scan renders ${k}`, scanBlock.indexOf(k) >= 0));

// 6b — (v4.52) how deep into the band the extreme went is VISIBLE and
//      sortable, the stage filter exists, and neither touched the default
//      order: reordering on either was tested and rejected.
ok("the scan shows how far into the band the extreme went",
   /Into zone/.test(scanBlock) && /REV_TIP\.penetration/.test(scanBlock));
ok("that column is sortable", /k="pen"/.test(scanBlock) && /pen: r\.rz_penetration/.test(scanBlock));
ok("the stage filter exists and is off by default",
   /useState\("all"\)/.test(scanBlock) && /Normal size or beyond/.test(scanBlock));
ok("the stage filter FILTERS rather than reorders",
   /stageF === "all" \|\| r\.rz_stage !== "EARLY IN THE MOVE"/.test(scanBlock));
ok("the default order still leads on status",
   /useState\("status"\)/.test(scanBlock));
ok("penetration is NOT part of the default comparator", (() => {
  const by = scanBlock.slice(scanBlock.indexOf("const byStatus"),
                             scanBlock.indexOf("const sorted"));
  return by.indexOf("pen") === -1 && by.indexOf("rz_penetration") === -1;
})());
ok("the rejected ordering is documented where it would be repeated",
   /TESTED AND REJECTED/.test(tipBlock));

// 7 — the what-if prefills belong to the SETUP, not to whatever was typed
//     for the last ticker.
const wiBlock = src.slice(src.indexOf("function SwingWhatIf"),
                          src.indexOf("const REV_STATUS_TONE"));
ok("what-if prefills key off the setup identity", /setupKey/.test(wiBlock));
["ticker", "sens", "from_date", "extreme_abs_pct"].forEach(k =>
  ok(`the setup identity includes ${k}`, new RegExp(k).test(
    wiBlock.slice(wiBlock.indexOf("const setupKey"), wiBlock.indexOf("useEffect")))));
ok("the prefill effect RESETS rather than filling only when empty",
   /setTgt\(nxt && nxt\.pct_median/.test(wiBlock) && !/&& !tgt\)/.test(wiBlock));
ok("the what-if discloses gapped fills", /gapped_entries/.test(wiBlock));
ok("the what-if discloses dropped episodes", /excluded_contaminated/.test(wiBlock));

// 8 — the reading order: projection, chart, what-if, then the collapsed
//     legacy layer, then the historical swing tables.
const card = src.slice(src.indexOf("function SwingPatternCard"),
                       src.indexOf("function computeTicket"));
const at = (needle) => card.indexOf(needle);
ok("the projection leads", at("<SwingReversalBlock") > 0);
ok("the chart sits directly under the projection",
   at("<TVAdvancedChart") > at("<SwingReversalBlock"));
ok("the what-if follows the chart", at("<SwingWhatIf") > at("<TVAdvancedChart"));
ok("the legacy analytics come after the chart",
   at("Decision banner") > at("<TVAdvancedChart"));
ok("the legacy analytics are collapsed by default",
   /useState\(false\);\s*\/\/ legacy analytics/.test(card));
["Decision banner", "Live decision box", "Odds & risk", "Target ladder",
 "Trade plan"].forEach(sec => {
  const i = at(sec);
  ok(`${sec} is behind the More details toggle`,
     i > 0 && card.slice(Math.max(0, i - 200), i + 260).indexOf("{more &&") >= 0);
});
ok("the historical swing tables stay visible", at("History table (up / down toggle") > 0
   && card.slice(at("History table (up / down toggle"), at("History table (up / down toggle") + 300).indexOf("{more &&") === -1);
ok("the rejected context is kept, not deleted",
   /rev-context/.test(card) && /range_pos_20/.test(card) && /regime/.test(card));
ok("the scan closes the card", at("<SwingReversalScan") > at("History table (up / down toggle"));

// 9 — things that must not have changed.
ok("the card still reads ten years", /period=10y/.test(card));
ok("the chart keeps its Zones toggle", /\["zones", "Zones"\]/.test(src));
ok("the chart draws the running extreme", /running low/.test(src) && /running high/.test(src));
ok("Pattern Discovery is still mounted after the swing card",
   appSrc.indexOf('component="PatternDiscoveryCard"') > appSrc.indexOf("<SwingPatternCard"));

console.log(`\n${passed}/${passed + failed} passed` + (failed ? ` — FAILED: ${fails.join(", ")}` : ""));
process.exit(failed ? 1 : 0);
