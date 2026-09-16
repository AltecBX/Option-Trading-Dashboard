// test_stretch_ui.js (v5.16) — source guards for AT THE LINE.
//
// This card sends real-money alerts to a phone and prices strikes on stocks
// that have just moved, so the guards are weighted toward disclosure:
//
//   1. It is registered as a lazy chunk, mounted at the top of the Trade
//      tab, and its Analyze companion sits above the weekly charts.
//   2. Every column carries a tooltip; the risk column is visible without
//      scrolling; the phone shows the deciding fields; rows key on
//      symbol + horizon + side + expiry.
//   3. The anchor is named on the row (last week's close / yesterday's
//      close); the line is the median, and the record is called context,
//      not a ceiling.
//   4. Delta is shown as the market's price beside the measured number,
//      never in place of it; finished-through and touched are distinguished;
//      pooled evidence is labelled; the crossing bar is charged in full and
//      the same-day upper bound is stated.
//   5. READY needs a contract; CROSSED says why; the closed-back rate is
//      shown as measured, near a coin flip, not assumed.
//   6. Alerts are once per symbol/side/expiry, remembered across restarts,
//      and the push link opens Analyze on the name.
//
// Run from the repo dir:  node test_stretch_ui.js
const fs = require("fs");
const path = require("path");

let passed = 0, failed = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — got: " + extra : "")); }
}
const read = (f) => fs.readFileSync(path.join(__dirname, f), "utf8");

const src = read("tab-stretch.jsx");
const appSrc = read("app.jsx");
const css = read("styles.css");
const build = read("build_frontend.js");
const verify = read("verify_frontend.js");
const html = read("index.html");
const scan = read("stretch_scan.py");
const ev = read("stretch_evidence.py");
const dash = read("options_dashboard.py");
const thresholds = JSON.parse(read("thresholds.json"));

// ── 1. registration and placement ───────────────────────────────────────
ok("registered as a lazy chunk", /"tab-stretch\.jsx"/.test(build) && /"tab-stretch\.js"/.test(build));
ok("in the verifier's load order", /"tab-stretch\.js"/.test(verify));
ok("the verifier expects both components", /"StretchCard"/.test(verify) && /"StretchLineCard"/.test(verify));
ok("index.html carries its version stamp", /"tab-stretch":"[0-9a-f]{8}"/.test(html));
ok("published on window", /Object\.assign\(window, \{ StretchCard: React\.memo\(StretchCard\), StretchLineCard/.test(src));
const stretchAt = appSrc.indexOf('component="StretchCard"');
const spikeAt = appSrc.indexOf('component="SpikeCard"');
ok("mounted on the Trade tab", stretchAt > 0 && /chunk="tab-stretch"/.test(appSrc));
ok("sits at the TOP of Trade — it is the watchman", stretchAt > 0 && spikeAt > 0 && stretchAt < spikeAt);
ok("the symbol loads through switchTicker, not a bypass",
   /component="StretchCard"[\s\S]{0,260}onPickTicker=\{\(t\) => \{ switchTicker\(t\)/.test(appSrc));
const lineAt = appSrc.indexOf('component="StretchLineCard"');
const weeklyAt = appSrc.indexOf('<div className="kicker">Weekly returns history</div>');
ok("the Analyze companion sits above the weekly charts", lineAt > 0 && weeklyAt > 0 && lineAt < weeklyAt);
ok("the Analyze companion is fed the ticker", /component="StretchLineCard"[\s\S]{0,200}ticker=\{ticker\}/.test(appSrc));
ok("a push link opens Analyze on the name outside the embed",
   /if \(window\.__JT_EMBED\) return;[\s\S]{0,400}q\.get\("symbol"\)[\s\S]{0,300}changeTab\(tab\)/.test(appSrc));
ok("the version moved", /APP_VERSION = "5\.16"/.test(appSrc));

// ── 2. columns, tooltips, the risk column, mobile ────────────────────────
const cols = [];
src.replace(/^\s{2}\["([^"]+)", "(\w+)", "(\w+)"/gm, (m, label, key, tip) => {
  cols.push({ label, key, tip }); return m;
});
ok("the column table is complete (14)", cols.length >= 14, String(cols.length));
["state", "symbol", "side", "horizon", "expiration", "move_pct", "line_pct", "strike", "delta",
 "credit", "itm_pct", "touch_pct", "grade", "first_seen"]
  .forEach((k) => ok(`column present: ${k}`, cols.some((c) => c.key === k)));
const tipBlock = src.slice(src.indexOf("const ST_TIP = {"), src.indexOf("};", src.indexOf("const ST_TIP = {")));
const defined = new Set();
tipBlock.replace(/^\s{2}(\w+):/gm, (m, k) => { defined.add(k); return m; });
ok("every column tooltip is defined",
   cols.filter((c) => !defined.has(c.tip)).length === 0,
   cols.filter((c) => !defined.has(c.tip)).map((c) => c.key).join(","));
const used = new Set();
src.replace(/ST_TIP\.(\w+)/g, (m, k) => { used.add(k); return m; });
ok("every referenced tooltip is defined",
   [...used].filter((k) => !defined.has(k)).length === 0,
   [...used].filter((k) => !defined.has(k)).join(","));
ok("a real tooltip vocabulary (24+)", defined.size >= 24, String(defined.size));
const itmIdx = cols.findIndex((c) => c.key === "itm_pct");
ok("the risk column is early enough to be seen without scrolling", itmIdx > 0 && itmIdx <= 10, `position ${itmIdx}`);
ok("headers sort and carry their tooltip",
   /<th key=\{k\}[^>]*title=\{ST_TIP\[tipKey\]\}[\s\S]{0,200}onClick/.test(src));
ok("cells carry the column tooltip", /<td key=\{ck\}[^>]*title=\{ST_TIP\[tipKey\]\}/.test(src));
ok("cells carry a mobile label", /data-label=\{label\}/.test(src) && /mtable/.test(src));
ok("the phone shows the deciding fields only", /const ST_MOBILE = new Set/.test(src) && /sk-m-hide/.test(src));
ok("rows are keyed by symbol, horizon, side and expiry together",
   /stRowKey = \(r\) => r\.key \|\| `\$\{r\.symbol\}\|\$\{r\.horizon\}\|\$\{r\.side\}\|\$\{r\.expiration\}`/.test(src)
   && /r\["key"\] = f"\{sym\}\|\{r\['horizon'\]\}\|\{r\['side'\]\}\|\{r\['expiration'\]\}"/.test(scan));
ok("Calls/Puts/Both and week/day filters exist and persist",
   /\["call", "Calls"\], \["put", "Puts"\]/.test(src) && /\["week", "This week"\], \["day", "Today"\]/.test(src)
   && /localStorage\.setItem\(ST_FILTER_KEY/.test(src));
ok("order is stable while reading: state first, then the move", /if \(sortK === "state"\) return r\.state === "ready" \? 0 : 1/.test(src)
   && /\(b\.move_sigma \|\| 0\) - \(a\.move_sigma \|\| 0\)/.test(src));
ok("new setups are flagged", /is_new/.test(src) && /st-new-chip/.test(src) && /\.st-new-chip/.test(css));

// ── 3. the anchor and the line are named ────────────────────────────────
ok("the anchor is named on the row", /from \{r\.anchor_label\}/.test(src) && /anchor_label/.test(scan));
ok("the engine names both anchors", /the prior week's last close/.test(ev) && /the prior session's close/.test(ev));
ok("the line is the median, drawn on the Analyze chart", /the same dashed line the Analyze chart draws/.test(src));
ok("the record is context, not a ceiling", /context, not a ceiling/.test(src) && /never a ceiling/.test(ev));
ok("the move is shown in percent AND sigma", /stPct\(r\.move_pct\)\} · \$\{stSig\(r\.move_sigma\)/.test(src));
ok("the line quantile is a configurable choice", /line_quantile/.test(scan) && thresholds.stretch.select.line_quantile === 0.5);

// ── 4. the probabilities are honest ─────────────────────────────────────
ok("delta is the market's price, beside the measured number", /market's own price-based odds/.test(src) && /never in place of it/.test(src));
ok("finished-through and touched are distinguished", /A touch is what you feel mid-week; the close is what settles/.test(src));
ok("touch is never rarer than finish", /Always at least the finished-through rate/.test(src)
   && /touch_n \+= 1/.test(read("weekly_sell.py")));
ok("pooled evidence is graded and disclosed", /MOSTLY POOLED/.test(src) && /MOSTLY POOLED/.test(ev) && /"grade": ev\["grade"\]/.test(scan));
ok("thin evidence refuses rather than guesses", /"THIN"/.test(ev) && /comparable crossings on record/.test(scan));
ok("outcomes are measured from the crossing bar on", /from the crossing bar on/.test(src) && /days\[idx:\]/.test(ev));
ok("the crossing bar is charged in full — an upper bound", /charged in full/.test(src) && /upper bound/.test(ev));
ok("the same-day whole-day charge is stated", /whole remaining day is charged/.test(src) || /whole day is charged/.test(src));
ok("puts are measured on their own crossings", /not the call numbers with the sign flipped/.test(src)
   && /math\.log\(level \/ min\(d\["low"\] for d in after\)\)/.test(ev));
ok("the closed-back belief is measured and called a coin flip", /near a coin flip/.test(src) && /p_closed_back/.test(scan));
ok("the vs-Monday check says premium is not on the bars", /Premium is not on the bars/.test(src) && /not invented here/.test(ev));

// ── 5. READY needs a contract; CROSSED says why ─────────────────────────
ok("READY is defined as a cleared contract", /READY: the stock is past its line AND a listed contract cleared/.test(src)
   && /"state": "ready"/.test(scan));
ok("CROSSED carries its reason", /Not ready:<\/b> \{\(r\.why \|\| \[\]\)\.join/.test(src) && /"state": "crossed", "why"/.test(scan));
ok("no expiry is the calendar and stays off the board", /the calendar, not the scanner/.test(scan) && /if not r\.get\("expiration"\):\s*\n\s*continue/.test(scan));
ok("earnings inside the trade is refused", /_earnings_inside/.test(scan) && /inside the trade/.test(scan));
ok("takeover headlines are refused", /_catalyst_refusal/.test(scan) && /Takeover spikes are refused/.test(src));
ok("every strike in range is shown with its gate", /StLadder/.test(src) && /x\.gate \|\| "clears"/.test(src));
ok("the risk limit is shown in the footer and the detail", /limit \{Math\.round\(data\.limits\.max_itm \* 100\)\}% finished through/.test(src)
   && /the limit is \$\{Math\.round\(limits\.max_itm \* 100\)\}% finished through/.test(src));

// ── 6. alerts and the watchman ───────────────────────────────────────────
ok("alerts are once per symbol, side and expiry and remembered", /_ALERTS\[r\["key"\]\] = rec/.test(scan)
   && /_save_json\("stretch_alerts\.json", _ALERTS\)/.test(scan) && /remembered across restarts/.test(src));
ok("an alert is never sent below the credit floor", /min_credit/.test(scan) && /sent only when the credit clears the floor/.test(src));
ok("the push carries the link into Analyze", /\?symbol=\{r\['symbol'\]\}&tab=analyze/.test(scan));
ok("the push names ticker, side, expiry, contract, credit and risk",
   /Sell the \{exp_words\} \{r\['strike'\]:g\} \{side\}/.test(scan) && /Finished through a strike like this/.test(scan));
ok("the loop runs regardless of the tab", /def start_scheduler/.test(scan) && /_stretch\.start_scheduler\(\)/.test(dash)
   && /whether or not this tab is open/.test(src));
ok("the loop sleeps through a closed market", /if not _market_open\(\):\s*\n\s*time\.sleep\(IDLE_SECS\)/.test(scan));
ok("every READY is logged with its numbers", /stretch_alerts\.jsonl/.test(scan) && /def _log_alert/.test(scan));
ok("the routes are wired", /\/api\/stretch/.test(dash) && /_stretch\.profile_for\(sym\)/.test(dash) && /_stretch\.alerts_log/.test(dash));
ok("the alert channel is the app's own push", /notify_fn=lambda title, msg, priority=0: _push_notify/.test(dash));
ok("the board says when no push channel is configured", /no push channel configured/.test(src));

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) { console.log("FAILED:\n  " + fails.join("\n  ")); process.exit(1); }
