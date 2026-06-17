// test_recommendation.js — verifies the Phase B (v1.11) rec engine
// mirror. Each case constructs a deliberate inputs object, runs both
// the CC and CSP variants through RecEngine.buildBoth, and asserts the
// expected verdict polarity. Run from the active dir:
//   node test_recommendation.js
//
// Pure node, no React, no DOM. Imports recommendation.js via require.

const path = require("path");
const RecEngine = require(path.join(__dirname, "recommendation.js"));

let passed = 0;
let failed = 0;
const fails = [];

function assert(name, cond, detail) {
  if (cond) {
    passed++;
    console.log("  PASS  " + name);
  } else {
    failed++;
    fails.push({ name: name, detail: detail || "" });
    console.log("  FAIL  " + name + (detail ? "  · " + detail : ""));
  }
}

function run(name, fn) {
  try {
    fn();
  } catch (e) {
    failed++;
    fails.push({ name: name, detail: "threw: " + e.message });
    console.log("  FAIL  " + name + "  · threw: " + e.message);
  }
}

// ── Scenario 1: clear bullish setup, no analyst overlay ─────────
// Stock has run above the typical weekly high. CC = favorable
// (sell into strength). CSP = wait (premium thin, bounce already
// used up).
run("clear_bullish_no_analyst", function () {
  const out = RecEngine.buildBoth({
    currReturn: 5.0, medianClose: 0.5, medianHigh: 2.0, medianLow: -2.0,
    analystData: null,
  });
  assert("bull · CC = success", out.cc.kind === "success", "got " + out.cc.kind);
  assert("bull · CSP = info (wait)", out.csp.kind === "info", "got " + out.csp.kind);
  assert("bull · CC title is Favorable", out.cc.title === "Favorable timing");
  assert("bull · CSP title is Wait on puts", out.csp.title === "Wait on puts");
});

// ── Scenario 2: clear bearish setup, no analyst overlay ─────────
// Stock weak vs typical Friday close. CC = wait, CSP = favorable.
run("clear_bearish_no_analyst", function () {
  const out = RecEngine.buildBoth({
    currReturn: -3.0, medianClose: 0.5, medianHigh: 2.0, medianLow: -2.0,
    analystData: null,
  });
  assert("bear · CC = info (wait)", out.cc.kind === "info", "got " + out.cc.kind);
  assert("bear · CSP = success", out.csp.kind === "success", "got " + out.csp.kind);
  assert("bear · CC title is Wait on calls", out.cc.title === "Wait on calls");
  assert("bear · CSP title is Favorable", out.csp.title === "Favorable timing");
});

// ── Scenario 3: in the zone, no analyst overlay ─────────────────
// Both should be warn ("Approaching the zone").
run("in_zone_no_analyst", function () {
  const out = RecEngine.buildBoth({
    currReturn: 1.0, medianClose: 0.5, medianHigh: 2.0, medianLow: -2.0,
    analystData: null,
  });
  assert("zone · CC = warn", out.cc.kind === "warn");
  assert("zone · CSP = warn", out.csp.kind === "warn");
});

// ── Scenario 4: bullish + fresh upgrade ─────────────────────────
// CC: fresh upgrade ESCALATES success → danger.
// CSP: fresh upgrade is BULLISH catalyst, does NOT escalate (kind
// stays info or softens danger). Polarity flip is the headline.
run("bullish_with_fresh_upgrade", function () {
  const out = RecEngine.buildBoth({
    currReturn: 5.0, medianClose: 0.5, medianHigh: 2.0, medianLow: -2.0,
    analystData: {
      data_available: true,
      verdict: { fresh_upgrade: true, fresh_downgrade: false },
      targets: {},
      consensus: { trend: "stable" },
    },
  });
  assert("upgrade · CC = danger", out.cc.kind === "danger", "got " + out.cc.kind);
  // CSP starts at info (wait on puts because price > medianHigh).
  // Fresh upgrade should NOT escalate. Adds positive note only.
  assert("upgrade · CSP NOT danger", out.csp.kind !== "danger", "got " + out.csp.kind);
  assert("upgrade · CSP body mentions catalyst",
    out.csp.body.indexOf("bullish catalyst") !== -1, "body: " + out.csp.body);
});

// ── Scenario 5: bearish + fresh downgrade ───────────────────────
// CC: existing logic does not escalate on fresh_downgrade alone
// (it only adds a notes entry, no kind change). Sanity check that.
// CSP: fresh downgrade ESCALATES to danger.
run("bearish_with_fresh_downgrade", function () {
  const out = RecEngine.buildBoth({
    currReturn: -3.0, medianClose: 0.5, medianHigh: 2.0, medianLow: -2.0,
    analystData: {
      data_available: true,
      verdict: { fresh_upgrade: false, fresh_downgrade: true },
      targets: {},
      consensus: { trend: "stable" },
    },
  });
  assert("downgrade · CC unchanged kind (info)", out.cc.kind === "info", "got " + out.cc.kind);
  assert("downgrade · CSP = danger", out.csp.kind === "danger", "got " + out.csp.kind);
  assert("downgrade · CSP title is Caution",
    out.csp.title.indexOf("Caution") === 0, "title: " + out.csp.title);
});

// ── Scenario 6: above average target, mild ──────────────────────
// upside_pct = -3 → stock is 3% above average target.
// CC: fires (any amount above target), bumps success → warn.
// CSP: does NOT fire (threshold is 10%+ above for puts).
run("above_avg_target_mild", function () {
  const out = RecEngine.buildBoth({
    currReturn: 5.0, medianClose: 0.5, medianHigh: 2.0, medianLow: -2.0,
    analystData: {
      data_available: true,
      verdict: { fresh_upgrade: false, fresh_downgrade: false },
      targets: { upside_pct: -3.0, upside_to_high_pct: 5.0 },
      consensus: { trend: "stable" },
    },
  });
  // CC base is success, bumped to warn by the 3%-above-target rule.
  assert("mild above target · CC = warn", out.cc.kind === "warn", "got " + out.cc.kind);
  // CSP base is info (price > medianHigh = wait on puts). Mild above
  // target should NOT fire on the CSP side. Kind stays info.
  assert("mild above target · CSP = info (unchanged)", out.csp.kind === "info", "got " + out.csp.kind);
  assert("mild above target · CSP body has no mean reversion note",
    out.csp.body.indexOf("mean reversion could drag") === -1);
});

// ── Scenario 7: far above highest target ────────────────────────
// upside_to_high_pct = -8 → stock is 8% above HIGHEST target.
// CC: escalates to danger (mean reversion good for CC sellers, but
// "overextended" is still bad timing for new short calls per spec).
// CSP: escalates to danger too (mean reversion drops into put strike).
run("far_above_highest_target", function () {
  const out = RecEngine.buildBoth({
    currReturn: 8.0, medianClose: 0.5, medianHigh: 2.0, medianLow: -2.0,
    analystData: {
      data_available: true,
      verdict: { fresh_upgrade: false, fresh_downgrade: false },
      targets: { upside_pct: -10.0, upside_to_high_pct: -8.0 },
      consensus: { trend: "stable" },
    },
  });
  assert("far above high · CC = danger", out.cc.kind === "danger", "got " + out.cc.kind);
  assert("far above high · CSP = danger", out.csp.kind === "danger", "got " + out.csp.kind);
  assert("far above high · CSP body mentions put strike",
    out.csp.body.indexOf("put strike") !== -1, "body: " + out.csp.body);
});

// ── Scenario 8: more_bearish trend, no fresh signals ────────────
// Both sides should pick up a body addendum but kind stays the same.
// CC adds "Analyst sentiment has turned more bearish".
// CSP adds "raises assignment risk on a short put".
run("more_bearish_trend", function () {
  const out = RecEngine.buildBoth({
    currReturn: -3.0, medianClose: 0.5, medianHigh: 2.0, medianLow: -2.0,
    analystData: {
      data_available: true,
      verdict: { fresh_upgrade: false, fresh_downgrade: false },
      targets: {},
      consensus: { trend: "more_bearish" },
    },
  });
  assert("trend bearish · CC body mentions sentiment",
    out.cc.body.indexOf("more bearish") !== -1, "body: " + out.cc.body);
  assert("trend bearish · CSP body mentions assignment risk",
    out.csp.body.indexOf("assignment risk") !== -1, "body: " + out.csp.body);
});

// ── Scenario 9: backward compat — buildBoth returns objects with
//    kind/title/body fields on each side ─────────────────────────
run("shape_backward_compat", function () {
  const out = RecEngine.buildBoth({
    currReturn: 0, medianClose: 0, medianHigh: 1, medianLow: -1,
    analystData: null,
  });
  assert("shape · cc has kind", typeof out.cc.kind === "string");
  assert("shape · cc has title", typeof out.cc.title === "string");
  assert("shape · cc has body", typeof out.cc.body === "string");
  assert("shape · csp has kind", typeof out.csp.kind === "string");
  assert("shape · csp has title", typeof out.csp.title === "string");
  assert("shape · csp has body", typeof out.csp.body === "string");
});

console.log("");
console.log(passed + "/" + (passed + failed) + " passed, " + failed + " failed");
if (failed > 0) {
  console.log("");
  console.log("Failures:");
  fails.forEach(function (f) {
    console.log("  · " + f.name + (f.detail ? "  " + f.detail : ""));
  });
  process.exit(1);
}
