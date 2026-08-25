// test_throttle_clock.js — the shared broker-throttle clock (v4.68).
//
// WHY THIS EXISTS
// The broker throttles the CONNECTION, not one request. Three components
// (the symbol load, the Best Setup card, the sell board) all discover the
// same throttle within milliseconds of each other. v4.67 gave each of them
// its own backoff, which meant three independent waves of retries aimed at
// something that had just asked for fewer — the failure mode where a few
// seconds of throttling becomes a minute of it.
//
// The clock is now shared, which makes it real state with real edge cases,
// so this tests BEHAVIOUR rather than matching the source text: does a
// second caller join the first one's wait instead of starting another, does
// the escalation get counted once rather than three times, does the budget
// actually run out, and does a quiet stretch let it start over.
//
// Time is injected, so none of this sleeps.
//
// Run from the repo dir:  node test_throttle_clock.js

const fs = require("fs");
const path = require("path");
const vm = require("vm");

let pass = 0, fail = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — " + extra : "")); }
}
const eq = (name, got, want) =>
  ok(name, JSON.stringify(got) === JSON.stringify(want),
     `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);

// ── load just the clock out of app-lib.jsx ────────────────────────────────
// Pulling the real source keeps this honest: if the constants or the logic
// move, the extraction fails loudly rather than testing a stale copy.
const SRC = fs.readFileSync(path.join(__dirname, "app-lib.jsx"), "utf8");
const start = SRC.indexOf("const THROTTLE_STEPS");
const end = SRC.indexOf("// Shared US date format");
if (start < 0 || end < 0 || end <= start) {
  console.error("could not find the throttle clock in app-lib.jsx — "
                + "if it moved, update this test rather than deleting it");
  process.exit(1);
}
const CLOCK = SRC.slice(start, end);

/** A fresh clock whose notion of "now" this test controls. */
function mkClock() {
  const ctx = { now: 0, Date: { now: () => ctx.now }, Math };
  vm.createContext(ctx);
  vm.runInContext(CLOCK + "\n;this.hit = throttleHit;"
                  + "this.waiting = throttleWaiting;"
                  + "this.clear = throttleClear;"
                  + "this.STEPS = THROTTLE_STEPS;"
                  + "this.FORGET = THROTTLE_FORGET_MS;", ctx);
  return ctx;
}

// ── the escalation is counted once, not once per caller ───────────────────
{
  const c = mkClock();
  eq("the first caller gets the first step", c.hit(), c.STEPS[0]);
  // Two more components discover the SAME throttle a few ms later.
  c.now += 5;
  const second = c.hit();
  c.now += 5;
  const third = c.hit();
  ok("a second caller joins the wait already running",
     second > 0 && second <= c.STEPS[0], `got ${second}`);
  ok("a third caller joins it too", third > 0 && third <= c.STEPS[0], `got ${third}`);
  // If each had escalated, the next real throttle would be at step 3.
  c.now += c.STEPS[0] * 1000 + 1;
  eq("the next throttle is the SECOND step, not the fourth", c.hit(), c.STEPS[1]);
}

// ── the budget runs out, so an outage cannot become a retry loop ──────────
{
  const c = mkClock();
  const seen = [];
  for (let i = 0; i < c.STEPS.length; i++) {
    seen.push(c.hit());
    c.now += c.STEPS[i] * 1000 + 1;
  }
  eq("each step is served in order", seen, c.STEPS);
  eq("the budget then refuses instead of retrying forever", c.hit(), null);
  c.now += 1000;
  eq("and it stays refused while the trouble continues", c.hit(), null);
}

// ── waits get longer, never shorter ───────────────────────────────────────
{
  const c = mkClock();
  let prev = 0, monotonic = true;
  for (let i = 0; i < c.STEPS.length; i++) {
    const w = c.hit();
    if (w <= prev) monotonic = false;
    prev = w;
    c.now += w * 1000 + 1;
  }
  ok("each wait is longer than the one before it", monotonic);
}

// ── a quiet stretch earns a clean slate ───────────────────────────────────
{
  const c = mkClock();
  c.hit(); c.now += c.STEPS[0] * 1000 + 1;
  c.hit(); c.now += c.STEPS[1] * 1000 + 1;
  c.now += c.FORGET + 1;                       // nothing goes wrong for a while
  eq("an unrelated throttle later starts from the short wait", c.hit(), c.STEPS[0]);
}

// ── the budget does NOT reset just because the wait elapsed ───────────────
{
  const c = mkClock();
  c.hit();
  c.now += c.STEPS[0] * 1000 + 1;              // wait served, but only seconds
  ok("a throttle right after the wait escalates rather than repeating",
     c.hit() === c.STEPS[1]);
}

// ── waiting() reports the window, and only the window ─────────────────────
{
  const c = mkClock();
  ok("nothing is waiting before the first throttle", c.waiting() === false);
  const w = c.hit();
  ok("it reports waiting during the wait", c.waiting() === true);
  c.now += w * 1000 - 1;
  ok("still waiting one millisecond before the end", c.waiting() === true);
  c.now += 2;
  ok("and stops the moment the wait is over", c.waiting() === false);
}

// ── clear() lets a manual Retry cut the wait short ────────────────────────
{
  const c = mkClock();
  c.hit();
  ok("a wait is running", c.waiting() === true);
  c.clear();
  ok("clearing ends the wait", c.waiting() === false);
  eq("and hands the next throttle the short wait again", c.hit(), c.STEPS[0]);
}

// ── the wait a joiner is told is never longer than the real remainder ─────
// A card that reported a longer wait than the clock actually holds would
// retry after the throttle had already lifted, which is the bug this whole
// change exists to avoid.
{
  const c = mkClock();
  const first = c.hit();
  c.now += 1500;
  const joined = c.hit();
  ok("a joiner's countdown never overruns the shared wait",
     joined <= first - 1, `first ${first}s, joiner told ${joined}s`);
  ok("and is never zero or negative", joined >= 1, `got ${joined}`);
}

console.log(`\n${pass}/${pass + fail} passed`
            + (fail ? ` — FAILED: ${fails.join(", ")}` : ""));
process.exit(fail ? 1 : 0);
