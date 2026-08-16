// test_swing_paths.js (v4.12) — guards the Swing Prediction "three paths"
// card against target/clock mismatches: the continuation path must target
// the aggressive (p75) tier while it is still ahead — never the extreme
// outlier — and its time window must come from the stock's own historical
// speed, so a "+140% in 2 days" pairing can never render again (user-
// reported, 8-16-2026). computeSwingPrediction contains no JSX, so it is
// extracted from app-cards.jsx and run as plain JS.
// Run from the active dir:  node test_swing_paths.js
const fs = require("fs");
const path = require("path");

let passed = 0, failed = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — got: " + extra : "")); }
}

const src = fs.readFileSync(path.join(__dirname, "app-cards.jsx"), "utf8");
const start = src.indexOf("function computeSwingPrediction");
const end = src.indexOf("const SWING_DECISION_TONE");
ok("computeSwingPrediction extractable from app-cards.jsx", start >= 0 && end > start);
if (start < 0 || end <= start) { console.log("\n1 failed"); process.exit(1); }
const compute = new Function(src.slice(start, end) + "\nreturn computeSwingPrediction;")();

// SNDK-shaped fixture: mature +41% move in 6d; past up-swings ~40%/6d plus
// one +235%/28d monster. Aggressive tier is +7.3% ahead; extreme is +140%.
const mkUp = (pct, days, i) => ({
  pct_change: pct, trading_days: days,
  low_date: `2025-0${(i % 8) + 1}-01`, high_date: `2025-0${(i % 8) + 1}-15`,
});
const fixture = () => ({
  symbol: "SNDK",
  swings: [mkUp(46.52, 6, 0), mkUp(30.1, 6, 1), mkUp(53.92, 6, 2),
           mkUp(38.0, 5, 3), mkUp(42.0, 7, 4), mkUp(235.0, 28, 5)],
  down_swings: [
    { pct_change: -15.59, trading_days: 2, low_date: "2025-02-01", high_date: "2025-01-15" },
    { pct_change: -18.3, trading_days: 3, low_date: "2025-03-01", high_date: "2025-02-15" },
    { pct_change: -17.78, trading_days: 1, low_date: "2025-04-01", high_date: "2025-03-15" },
  ],
  analysis: {
    status: "ok", direction: "up", current_price: 1658.0,
    from_price: 1175.0, extreme_price: 1690.0,
    from_label: "swing low", from_date: "2026-08-06",
    current_move_pct: 41.1, days_active: 6, maturity: "mature",
    vs_history: { median_pct: 40.4, median_days: 6, pct_of_median_move: 102,
                  p25_days: 2, p75_days: 11 },
    continuation_score: 75, exhaustion_score: 46,
    key_levels: { next: { price: 1696.37, kind: "resistance" }, supports: [] },
    trade_plan: { invalidation: 1145.64 },
    targets: [
      { label: "conservative", price: 1488.76, from_here_pct: -10.2, reached: true,  matched: 5, confidence: "High" },
      { label: "median",       price: 1632.98, from_here_pct: -1.5,  reached: true,  matched: 3, confidence: "High" },
      { label: "aggressive",   price: 1760.92, from_here_pct: 7.3,   reached: false, matched: 1, confidence: "High" },
      { label: "extreme",      price: 3945.20, from_here_pct: 140.4, reached: false, matched: 0, confidence: "Low" },
    ],
    decision: { action: "Hold", drivers: [] }, signal_note: "",
  },
});

// 1 — continuation targets the aggressive tier, clocked by the stock's speed.
const p = compute(fixture());
const cont = p.paths[0];
ok("path[0] is Bullish continuation", cont.name === "Bullish continuation");
ok("continuation targets aggressive tier, not the extreme outlier",
   cont.target.includes("1760.92") && cont.target.includes("aggressive"), cont.target);
const m = cont.days.match(/^(\d+)–(\d+) days$/);
ok("time window renders as a day range", !!m, cont.days);
if (m) ok("window matches this stock's speed (+7.3% at ~6–8%/day ≈ 1–2 days)",
          +m[1] >= 1 && +m[2] <= 3, cont.days);

// 2 — aggressive already reached → extreme becomes the target, on its OWN clock.
const d2 = fixture();
d2.analysis.targets[2].reached = true; d2.analysis.targets[2].from_here_pct = -2.0;
const cont2 = compute(d2).paths[0];
ok("aggressive reached → extreme becomes the target", cont2.target.includes("3945.2") && cont2.target.includes("extreme"), cont2.target);
const m2 = cont2.days.match(/^(\d+)–(\d+) days$/);
ok("+140% target gets weeks, never the typical-swing 2–11d clock",
   !!m2 && +m2[1] >= 15, cont2.days);

// 3 — sparse history (<3 swings) falls back to the p25–p75 window, no crash.
const d3 = fixture();
d3.swings = d3.swings.slice(0, 2);
ok("sparse history falls back to p25–p75 window", compute(d3).paths[0].days === "2–11 days",
   compute(d3).paths[0].days);

// 4 — down direction mirrors: bearish continuation, aggressive tier, speed clock.
const d4 = fixture();
d4.analysis.direction = "down";
d4.analysis.targets = [
  { label: "conservative", price: 290.47, from_here_pct: -5.1,  reached: false, matched: 1, confidence: "Low" },
  { label: "median",       price: 290.47, from_here_pct: -5.1,  reached: false, matched: 1, confidence: "Low" },
  { label: "aggressive",   price: 261.87, from_here_pct: -14.4, reached: false, matched: 1, confidence: "Low" },
  { label: "extreme",      price: 233.27, from_here_pct: -23.7, reached: false, matched: 1, confidence: "Low" },
];
const cont4 = compute(d4).paths[0];
ok("bearish continuation targets aggressive tier",
   cont4.name === "Bearish continuation" && cont4.target.includes("261.87"), cont4.target);
const m4 = cont4.days.match(/^(\d+)–(\d+) days$/);
ok("bearish window is velocity-based (14.4% at down-speeds ≈ 1–2 days)",
   !!m4 && +m4[1] >= 1 && +m4[2] <= 3, cont4.days);

console.log(`\n${passed} passed, ${failed} failed` + (fails.length ? ` — ${fails.join("; ")}` : ""));
process.exit(failed ? 1 : 0);
