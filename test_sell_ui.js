// test_sell_ui.js (v4.80) — source-level guards for BEST SALES TODAY.
//
// This card tells a premium seller which option to sell with real money, so
// the guards are weighted toward disclosure:
//
//   1. It sits at the top of the Trade tab and is a registered lazy chunk.
//   2. Every column the user asked for is present, sortable, and carries a
//      tooltip; every cell carries one too.
//   3. The three probabilities are separated on screen (model / measured /
//      conservative) and delta is described as risk-neutral, never as P0.
//   4. NO TRADE renders as a finding; WHY #1 and WHY OTHERS FAILED exist.
//   5. Dates are spelled out; credit is the bid; the calibration panel says
//      MEASURED / MODELED / UNAVAILABLE / ACCRUING in words.
//   6. The backend contract the card reads is the one the scanner writes.
//
// Run from the repo dir:  node test_sell_ui.js
const fs = require("fs");
const path = require("path");

let passed = 0, failed = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — got: " + extra : "")); }
}
const read = (f) => fs.readFileSync(path.join(__dirname, f), "utf8");

const src = read("tab-sell.jsx");
const appSrc = read("app.jsx");
const css = read("styles.css");
const build = read("build_frontend.js");
const verify = read("verify_frontend.js");
const html = read("index.html");
const scan = read("sell_scan.py");
const engine = read("sp_engine.py");
const forward = read("sp_forward.py");
const dash = read("options_dashboard.py");

// ── 1. placement and registration ───────────────────────────────────────
ok("the card is a registered lazy chunk in the build", /"tab-sell\.jsx"/.test(build) && /"tab-sell\.js"/.test(build));
ok("the chunk is in the verifier's load order", /"tab-sell\.js"/.test(verify));
ok("the verifier expects the component", /"SellBestCard"/.test(verify));
ok("index.html carries the chunk's version stamp", /"tab-sell":"[0-9a-f]{8}"/.test(html));
ok("the component is published on window", /Object\.assign\(window, \{ SellBestCard/.test(src));
const mountAt = appSrc.indexOf('component="SellBestCard"');
const setupAt = appSrc.indexOf('component="BestSetupCard"');
ok("the card is mounted on the Trade tab", mountAt > 0 && /chunk="tab-sell"/.test(appSrc));
ok("the card sits ABOVE the Best Setup card", mountAt > 0 && setupAt > 0 && mountAt < setupAt);
ok("clicking a symbol loads it through switchTicker (not a bypass)",
   /component="SellBestCard"[\s\S]{0,300}onPickTicker=\{\(t\) => \{ switchTicker\(t\)/.test(appSrc));

// ── 2. the columns the seller asked for, sortable, every one with a tooltip
const cols = [];
src.replace(/^\s{2}\["([^"]+)", "(\w+)", "(\w+)"/gm, (m, label, key, tip) => { cols.push({ label, key, tip }); return m; });
ok("the column table has 30+ columns", cols.length >= 30, String(cols.length));
["rank", "symbol", "strategy", "side", "expiration", "dte", "short_strike", "width", "dist_pct", "k_sigma",
 "delta", "credit", "net_credit", "spread_pct", "oi", "volume", "p0_model", "p0_conservative", "p0_measured",
 "p_touch", "p_touch_measured", "p_profit", "p_hit_50", "ev_per_contract", "es95_per_share",
 "max_loss_per_share", "capital", "roc_pct", "annualized_roc_pct", "ev_per_tail", "iv", "vrp_ratio",
 "earnings_in_days", "sell_quality", "confidence", "data_source"]
  .forEach((k) => ok(`column present: ${k}`, cols.some((c) => c.key === k)));
const tipBlock = src.slice(src.indexOf("const SL_TIP = {"), src.indexOf("};", src.indexOf("const SL_TIP = {")));
const defined = new Set();
tipBlock.replace(/^\s{2}(\w+):/gm, (m, k) => { defined.add(k); return m; });
const missingCol = cols.filter((c) => !defined.has(c.tip)).map((c) => c.key);
ok("every column's tooltip key is defined", missingCol.length === 0, missingCol.join(","));
const used = new Set();
src.replace(/SL_TIP\.(\w+)/g, (m, k) => { used.add(k); return m; });
const missingUsed = [...used].filter((k) => !defined.has(k));
ok("every referenced tooltip key is defined", missingUsed.length === 0, missingUsed.join(","));
ok("a real tooltip vocabulary exists (50+)", defined.size >= 50, String(defined.size));
ok("headers are sortable and carry the tooltip", /<th key=\{k\}[^>]*title=\{SL_TIP\[tipKey\]\}[\s\S]{0,200}onClick/.test(src));
ok("cells carry the column tooltip", /<td key=\{ck\}[^>]*title=\{SL_TIP\[tipKey\]\}/.test(src));
ok("cells carry a data-label for the mobile stack", /data-label=\{label\}/.test(src) && /className="scan-table mtable sl-table"/.test(src));
ok("headers spell words out — no bare abbreviations",
   !cols.some((c) => /^(DTE|OI|Vol|EV|ES|ROC|IV|SQ|P\(T\))$/.test(c.label)), cols.map((c) => c.label).join("|"));

// ── 3. the probabilities are separated and delta is not P0 ─────────────
ok("P0 model, P0 conservative and P0 measured are three columns",
   cols.some((c) => c.key === "p0_model") && cols.some((c) => c.key === "p0_conservative") && cols.some((c) => c.key === "p0_measured"));
ok("touch and finish are separated", cols.some((c) => c.key === "p_touch") && /can touch and still expire worthless/.test(src));
ok("delta is described as the risk-neutral probability, not the real-world one",
   /RISK-NEUTRAL probability the market charges, not the real-world chance/.test(src));
ok("the conservative bound names the Wilson lower bound", /Wilson lower bound/.test(src));
ok("the measured column says overlapping windows are not independent", /Overlapping windows are not independent trials/.test(src));
ok("the tail correction is disclosed on the model P0", /measured tail correction/.test(src));

// ── 4. NO TRADE, WHY #1, WHY OTHERS FAILED, risk pathway, modes ─────────
ok("NO TRADE renders as a finding", /NO TRADE in \{modeLabel\} mode/.test(src) && /sl-notrade/.test(src) && /\.sl-notrade/.test(css));
ok("the scanner has a NO TRADE answer with a reason", /"no_trade": len\(top\) == 0/.test(scan) && /NO TRADE is the answer/.test(scan));
ok("WHY IS NUMBER 1 NUMBER 1 renders in the asked order",
   /Why is number 1 number 1\?/.test(src)
   && /Why this stock[\s\S]*Why this expiration[\s\S]*Why this strike[\s\S]*Why this side[\s\S]*The evidence[\s\S]*What the market is overpaying for[\s\S]*Comparable breaches[\s\S]*What could make this wrong[\s\S]*The worst comparable outcome[\s\S]*What would reject it[\s\S]*Why #2 is below #1/.test(src));
ok("WHY OTHER STOCKS FAILED is expandable and grouped by gate", /why other stocks failed/.test(src) && /g\.gate/.test(src) && /g\.symbols/.test(src));
ok("the risk pathway renders every stage",
   ["Entry", "Management", "Danger", "Exit", "Roll", "Assignment", "Position size"].every((s) => new RegExp(`sec\\("${s}"`).test(src)));
ok("RESIDUAL REWARD versus RESIDUAL RISK is the exit's framing", /RESIDUAL REWARD versus RESIDUAL RISK/.test(src));
ok("four modes are offered including EVENT PREMIUM", /"conservative"[\s\S]*"balanced"[\s\S]*"income"[\s\S]*"event"/.test(src) && /EVENT PREMIUM mode/.test(src));
ok("the Sell Quality breakdown renders the eight components", /Sell Quality \{slNum\(sq\.score, 1\)\} — the breakdown/.test(src) && /Safety, Edge, Income efficiency, Liquidity, Tail, Event, Data confidence and Calibration/.test(src));
ok("the portfolio concentration note renders", /Portfolio note/.test(src) && /portfolio\.flags/.test(src));
ok("no naked calls are promised", /No naked calls are ever listed/.test(src));

// ── 5. dates, credit basis, provenance, calibration labels ──────────────
ok("dates are spelled out (Month Day, Year)", /month: "long", day: "numeric", year: "numeric"/.test(src));
ok("no ISO date reaches the screen from the explanation", /def long_date/.test(engine) && /long_date\(top\['expiration'\]\)/.test(engine));
ok("credit is described as the bid, never the mid", /credit at the BID/.test(src) && !/at the mid/.test(src));
ok("the data column shows source, greeks provenance and quote age", /r\.greeks/.test(src) && /r\.quote_age_s/.test(src) && /r\.data_source/.test(src));
ok("the evaluation timestamp and age are shown", /Evaluated \$\{slDate\(data\.as_of\)\} at \$\{slTime\(data\.as_of\)\}/.test(src) && /slAge\(data\.as_of\)/.test(src));
ok("a stale board says so", /This board is from/.test(src) && /20 \* 3600 \* 1000/.test(src));
ok("the calibration panel says MEASURED / MODELED / UNAVAILABLE / ACCRUING in words",
   /Finish and touch are MEASURED/.test(src) && /profit is MODELED/.test(src) && /early-profit targets are UNAVAILABLE/.test(src) && /ACCRUING/.test(src));
ok("the learning loop is reported, never applied", /Reported only; never applied by the app on its own/.test(src) && /never rewrites the engine/.test(forward));
ok("the board does not poll when idle", /if \(!\(data && data\.scanning\)\) return;/.test(src));

// ── 5b. row identity: the v4.81 duplicate-rows defect ───────────────────
ok("rows are keyed by the backend's contract identity, not the short strike",
   /const slRowKey = \(r\) => \(r\.row_id/.test(src)
   && !/const slRowKey = \(r\) => `\$\{r\.symbol\}\|\$\{r\.strategy\}\|\$\{r\.expiration\}\|\$\{r\.short_strike\}`/.test(src));
ok("the fallback identity includes every leg", /r\.long_strike, r\.short_call, r\.long_call/.test(src));
ok("the pathway lookup uses the same identity as the row key", /const slPathKey = slRowKey;/.test(src));
ok("the backend stamps that identity on every row", /"row_id": E\.contract_id\(c\)/.test(scan));
ok("the engine defines one contract identity", /def contract_id/.test(engine)
   && /"short_strike", "long_strike", "short_call", "long_call"/.test(engine));
ok("an age is never rendered as a negative number", /if \(m < 1\) return "just now";/.test(src));
ok("names dropped as stale are stated, not silently missing",
   /data\.stale_dropped/.test(src) && /dropped as stale/.test(src)
   && /stale_dropped:/.test(tipBlock));
ok("the scanner drops a board from an earlier session", /def _fresh_only/.test(scan)
   && /max_board_age_hours/.test(scan));
ok("a contract at or past its expiration is not offered", /def _expired/.test(scan));
ok("timestamps carry a timezone", /def _stamp/.test(scan) && /astimezone\(\)/.test(scan));

// ── 6. the backend contract ─────────────────────────────────────────────
ok("/api/sell routes exist", /parsed\.path == "\/api\/sell"/.test(dash) && /section == "calibration"/.test(dash) && /section == "detail"/.test(dash));
ok("the sell scan rides the Premium Edge chain pass (one fetch)", /register_chain_consumer\(on_chain\)/.test(scan) && /_CHAIN_CONSUMERS/.test(read("edge_scan.py")));
ok("every recommendation shown is recorded for the forward test", /_record_predictions\(top, mode, cfg_hash\)/.test(scan));
ok("row fields the card reads are written by the scanner",
   ["p0_model", "p0_conservative", "p0_measured", "p_touch", "p_touch_measured", "p_profit", "p_hit_50", "days_to_50",
    "ev_per_contract", "es95_per_share", "max_loss_per_share", "capital", "roc_pct", "annualized_roc_pct", "ev_per_tail",
    "sell_quality", "confidence", "data_source", "greeks", "quote_age_s", "data_ts", "earnings_date", "vrp_ratio"]
     .every((k) => new RegExp(`"${k}":`).test(scan)));
ok("the forward grader labels its outcomes", /"finish": "MEASURED"/.test(forward) && /"pnl": "MODELED/.test(forward) && /"early_profit_targets": "UNAVAILABLE/.test(forward));
ok("the app version was bumped", /const APP_VERSION = "4\.82"/.test(appSrc));

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
