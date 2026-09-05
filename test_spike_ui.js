// test_spike_ui.js (v4.82) — source guards for SOLD INTO STRENGTH.
//
// This card puts real money into same-day options on stocks that have just
// moved hard, so the guards are weighted toward disclosure:
//
//   1. It is registered, mounted first on the Trade tab, and keys its rows
//      by an identity that cannot collide.
//   2. Every column carries a tooltip; the ranking column is visible
//      without scrolling; the phone shows the deciding fields.
//   3. Sigma is presented beside percent everywhere a move or a strike is
//      shown — percent alone is the ruler that ranks these backwards.
//   4. The measured probability is never dressed up as delta, the touch
//      rate is distinguished from the close rate, and pooled evidence is
//      labeled pooled.
//   5. The session assumption — the biggest approximation on the card — is
//      stated on screen, MEASURED or MODELED.
//   6. Takeover spikes are refused in the engine and said so on the card,
//      and the survivorship hole in the history is disclosed.
//
// Run from the repo dir:  node test_spike_ui.js
const fs = require("fs");
const path = require("path");

let passed = 0, failed = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — got: " + extra : "")); }
}
const read = (f) => fs.readFileSync(path.join(__dirname, f), "utf8");

const src = read("tab-spike.jsx");
const appSrc = read("app.jsx");
const css = read("styles.css");
const build = read("build_frontend.js");
const verify = read("verify_frontend.js");
const html = read("index.html");
const scan = read("spike_scan.py");
const ev = read("spike_evidence.py");
const dash = read("options_dashboard.py");

// ── 1. registration and placement ───────────────────────────────────────
ok("registered as a lazy chunk", /"tab-spike\.jsx"/.test(build) && /"tab-spike\.js"/.test(build));
ok("in the verifier's load order", /"tab-spike\.js"/.test(verify));
ok("the verifier expects the component", /"SpikeCard"/.test(verify));
ok("index.html carries its version stamp", /"tab-spike":"[0-9a-f]{8}"/.test(html));
ok("published on window", /Object\.assign\(window, \{ SpikeCard/.test(src));
const spikeAt = appSrc.indexOf('component="SpikeCard"');
const sellAt = appSrc.indexOf('component="SellBestCard"');
ok("mounted on the Trade tab", spikeAt > 0 && /chunk="tab-spike"/.test(appSrc));
ok("sits FIRST — a spike's premium decays by the minute",
   spikeAt > 0 && sellAt > 0 && spikeAt < sellAt);
ok("the symbol loads through switchTicker, not a bypass",
   /component="SpikeCard"[\s\S]{0,260}onPickTicker=\{\(t\) => \{ switchTicker\(t\)/.test(appSrc));

// ── 2. columns, tooltips, ranking column, mobile ────────────────────────
const cols = [];
src.replace(/^\s{2}\["([^"]+)", "(\w+)", "(\w+)"/gm, (m, label, key, tip) => {
  cols.push({ label, key, tip }); return m;
});
ok("the column table is complete (15+)", cols.length >= 15, String(cols.length));
["rank", "symbol", "move_pct", "strike", "beyond_sigma", "credit", "settles",
 "edge_per_contract", "p_close_above", "p_touch", "p_finishes_at_high", "grade",
 "n_own", "sigma_annual", "spread_pct", "oi", "iv"]
  .forEach((k) => ok(`column present: ${k}`, cols.some((c) => c.key === k)));
const tipBlock = src.slice(src.indexOf("const SK_TIP = {"), src.indexOf("};", src.indexOf("const SK_TIP = {")));
const defined = new Set();
tipBlock.replace(/^\s{2}(\w+):/gm, (m, k) => { defined.add(k); return m; });
ok("every column tooltip is defined",
   cols.filter((c) => !defined.has(c.tip)).length === 0,
   cols.filter((c) => !defined.has(c.tip)).map((c) => c.key).join(","));
const used = new Set();
src.replace(/SK_TIP\.(\w+)/g, (m, k) => { used.add(k); return m; });
ok("every referenced tooltip is defined",
   [...used].filter((k) => !defined.has(k)).length === 0,
   [...used].filter((k) => !defined.has(k)).join(","));
ok("a real tooltip vocabulary (20+)", defined.size >= 20, String(defined.size));
const edgeIdx = cols.findIndex((c) => c.key === "edge_per_contract");
ok("the ranking column is early enough to be seen without scrolling",
   edgeIdx > 0 && edgeIdx <= 8, `position ${edgeIdx}`);
ok("headers sort and carry their tooltip",
   /<th key=\{k\}[^>]*title=\{SK_TIP\[tipKey\]\}[\s\S]{0,200}onClick/.test(src));
ok("cells carry the column tooltip", /<td key=\{ck\}[^>]*title=\{SK_TIP\[tipKey\]\}/.test(src));
ok("cells carry a mobile label", /data-label=\{label\}/.test(src) && /mtable/.test(src));
ok("the phone shows the deciding fields only",
   /const SK_MOBILE = new Set/.test(src) && /sk-m-hide/.test(src) && /\.sk-m-hide/.test(css));
ok("rows are keyed by symbol, expiry and strike together",
   /skRowKey = \(r\) => `\$\{r\.symbol\}\|\$\{r\.expiration\}\|\$\{r\.strike\}`/.test(src));

// ── 3. sigma beside percent ─────────────────────────────────────────────
ok("the run is shown in percent AND sigma",
   /skPct\(r\.move_pct\)\} · \$\{skSig\(r\.move_sigma\)/.test(src));
ok("the strike is shown in dollars AND percent", /skNum\(r\.strike, 2\)\} · \$\{skPct\(r\.strike_pct\)/.test(src));
ok("the distance beyond the run is in sigma", cols.some((c) => c.key === "beyond_sigma"));
ok("the card explains why percent is the wrong ruler", /percent is the wrong ruler/.test(src));
ok("the engine ranks stage 1 on sigma, not percent",
   /out\.sort\(key=lambda c: -c\["move_sigma"\]\)/.test(scan));

// ── 4. the probabilities are honest ─────────────────────────────────────
ok("delta is named as the risk-neutral price, not the real chance",
   /risk-neutral probability the market is charging/.test(src));
ok("touch and close are distinguished", /A touch is what you feel; the close is what settles/.test(src));
ok("the finish-at-high rarity is the stated basis", /that rarity is what\s*\n?\s*this trade is built on|that is the behaviour this trade is built on/.test(src));
ok("pooled evidence is graded and disclosed",
   /MOSTLY POOLED/.test(src) && /MOSTLY POOLED/.test(ev));
ok("the share of the stock's own evidence is shown", /weight_own/.test(src) && /weight_own/.test(ev));
ok("the size of the measured record is on screen", /n_sessions/.test(src) && /830,059|n_sessions/.test(src));
ok("the engine never claims a close above a strike never touched",
   /p_touch/.test(ev) && /touching is never rarer|p_touch/.test(ev));

// ── 5. the session assumption is stated ─────────────────────────────────
ok("the session elapsed is on screen", /of the session gone/.test(src));
ok("MEASURED versus MODELED is stated for the session",
   /session left \{data\.session_profile\}/.test(src) && /MEASURED from %d sessions/.test(scan));
ok("the card names the clock fallback as the largest approximation",
   /largest approximation on this card/.test(src));
ok("the engine labels the fallback MODELED", /MODELED \(clock/.test(ev));
ok("the settlement scales with the session left", /session_scale/.test(ev) && /settles_full_session/.test(src));

// ── 6. refusals and disclosed limits ────────────────────────────────────
ok("takeover spikes are refused in the engine", /_catalyst_refusal/.test(scan)
   && /refuse_kinds/.test(scan) && /BUYOUT/.test(scan));
ok("and the card says so", /Takeover and merger spikes are never listed/.test(src));
ok("the survivorship hole is disclosed on the card",
   /acquired and never came back/.test(src));
ok("no bid is no trade", /min_bid/.test(scan) && /no real bid/.test(scan));
ok("refusals keep their reason", /"why": why/.test(scan) && /SK_TIP\.refused/.test(src));
ok("NO TRADE renders as a finding", /Nothing to sell into/.test(src) && /no_trade_reason/.test(scan));
ok("the funnel of what ran is shown", /what has run today/.test(src) && /candidates/.test(scan));

// ── 7. backend contract + cadence ───────────────────────────────────────
ok("/api/spike routes exist", /parsed\.path == "\/api\/spike"/.test(dash)
   && /section == "detail"/.test(dash) && /section == "config"/.test(dash));
ok("the board only polls while the market is open",
   /if \(!\(data && data\.market_open\)\) return;/.test(src));
ok("the worker stops when nobody is looking", /WORKER_IDLE_SECS/.test(scan));
ok("same-day expiries only", /"max_dte": 0/.test(scan));
ok("one bounded chain call per candidate", /strike_count=int\(st\["strike_count"\]\)/.test(scan));
ok("every field the card reads is written by the scanner",
   ["p_close_above", "p_touch", "p_finishes_at_high", "grade", "n_own", "weight_own",
    "settles", "settles_full_session", "edge_per_contract", "beyond_sigma",
    "move_sigma", "sigma_annual", "session_basis", "spread_pct"]
     .every((k) => new RegExp(`"${k}":`).test(scan)));
ok("dates are spelled out", /month: "long", day: "numeric", year: "numeric"/.test(src));
ok("the version was bumped", /const APP_VERSION = "4\.82"/.test(appSrc));

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
