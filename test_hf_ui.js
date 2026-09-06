// test_hf_ui.js (v4.85) — source guards for HEDGE FUNDS · NAMED FUND WATCH.
//
// This card exists to say what is verified and WHEN, and to say UNKNOWN in
// that word between filings. The guards are weighted accordingly:
//
//   1. Registered as a lazy chunk, in the tab list, mounted on its own tab.
//   2. Every figure and header carries a tooltip; dates are spelled out.
//   3. UNKNOWN is a rendered state; the broader trend is a separate block
//      in its own evidence class; an OPAQUE book is never read as a view.
//   4. The backend enforces the attribution rule, follows a 13F-NT to the
//      successor, and does not count a 13G as activity.
//   5. Routes exist, offline-safe, and the watchlist is editable.
//
// Run from the repo dir:  node test_hf_ui.js
const fs = require("fs");
const path = require("path");

let passed = 0, failed = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — got: " + extra : "")); }
}
const read = (f) => fs.readFileSync(path.join(__dirname, f), "utf8");

const src = read("tab-hedge.jsx");
const appSrc = read("app.jsx");
const lib = read("app-lib.jsx");
const css = read("styles.css");
const build = read("build_frontend.js");
const verify = read("verify_frontend.js");
const html = read("index.html");
const watch = read("hf_watch.py");
const sources = read("hf_sources.py");
const registry = read("hf_registry.py");
const dash = read("options_dashboard.py");
const smoke = read("test_http_smoke.py");
const seed = JSON.parse(read("hf_watchlist.json"));

// ── 1. registration and placement ───────────────────────────────────────
ok("registered as a lazy chunk", /"tab-hedge\.jsx"/.test(build) && /"tab-hedge\.js"/.test(build));
ok("in the verifier's load order", /"tab-hedge\.js"/.test(verify));
ok("the verifier expects the component", /"HedgeTab"/.test(verify));
ok("index.html carries its version stamp", /"tab-hedge":"[0-9a-f]{8}"/.test(html));
ok("published on window", /Object\.assign\(window, \{ HedgeTab/.test(src));
ok("has its own tab in the navigation", /\{ id: "hedge", label: "Hedge Funds" \}/.test(lib));
ok("mounted on that tab", /<TabPanel tab="hedge"[\s\S]{0,300}chunk="tab-hedge" component="HedgeTab"/.test(appSrc));
ok("a symbol opens through switchTicker, not a bypass",
   /component="HedgeTab"[\s\S]{0,260}onOpenTicker=\{\(sym\) => \{ switchTicker\(sym\)/.test(appSrc));

// ── 2. tooltips everywhere, dates spelled out ───────────────────────────
const tipBlock = src.slice(src.indexOf("const HF_TIP = {"), src.indexOf("};", src.indexOf("const HF_TIP = {")));
const defined = new Set();
tipBlock.replace(/^\s{2}(\w+):/gm, (m, k) => { defined.add(k); return m; });
const used = new Set();
src.replace(/HF_TIP\.(\w+)/g, (m, k) => { used.add(k); return m; });
ok("a real tooltip vocabulary (30+)", defined.size >= 30, String(defined.size));
ok("every referenced tooltip is defined",
   [...used].filter((k) => !defined.has(k)).length === 0, [...used].filter((k) => !defined.has(k)).join(","));
ok("every defined tooltip is used somewhere",
   [...defined].filter((k) => !used.has(k)).length === 0, [...defined].filter((k) => !used.has(k)).join(","));
ok("both dates are labelled and explained",
   /Positions true as of/.test(src) && /Public on/.test(src) && defined.has("as_of") && defined.has("public_on"));
ok("dates are spelled out, never ISO",
   /month: "long", day: "numeric", year: "numeric"/.test(src) && !/toISOString\(\)\.slice/.test(src));
ok("the backend spells dates the same way", /def long_date/.test(watch) && /strftime\('%B'\)/.test(watch));
ok("tables carry mobile labels", /data-label=/.test(src) && /mtable/.test(src));
ok("the grid collapses on a phone", /@media \(max-width: 640px\)[\s\S]{0,200}\.hf-grid \{ grid-template-columns: 1fr; \}/.test(css));

// ── 3. the rule ─────────────────────────────────────────────────────────
ok("UNKNOWN is a rendered state with its own style",
   /hf-activity-unknown/.test(css) && /Current activity/.test(src) && /activity\.state/.test(src));
ok("the days since the last verified positions are shown", /nothing verified for \{activity\.days_since\} days/.test(src));
ok("CEASED and FILED SINCE and NOT READ YET are states too",
   /hf-activity-ceased/.test(css) && /hf-activity-filed-since/.test(css) && /hf-activity-not-read-yet/.test(css));
ok("the broader trend is a separate block under its own heading",
   /function HfBroader/.test(src) && /Broader hedge fund trend/.test(src) && /not about any one fund/.test(src));
ok("and it is never rendered inside the fund's activity block",
   !/HfActivity[\s\S]{0,400}broader_trend/.test(src.slice(src.indexOf("function HfActivity"), src.indexOf("function HfDates"))));
ok("the broader block carries its evidence class", /HfTag cls=\{\(bt && bt\.class\) \|\| "MODEL INFERENCE"\}/.test(src));
ok("an OPAQUE book is refused as a view, on the card", /will not summarise it as one/.test(src) && /hf-refusal/.test(css));
ok("an OPAQUE book gets counts, not a decision list",
   /d\.change && opaque/.test(src) && /listing them as decisions would be fiction/.test(src));
ok("statements render as 'wrote', never as positions", /wrote: <a href=\{s\.link\}/.test(src) && /A statement is a claim, not a position/.test(src));
ok("press is labelled as the outlet's report", /Reported by the press/.test(src) && /sub="PRESS"/.test(src));
ok("evidence class tags exist for all five classes",
   ["VERIFIED", "PRIME BROKER", "REGULATORY", "FLOW PROXY", "INFERENCE"].every((s) => src.includes(`"${s}"`)));
ok("a 13G is shown as a passive notice, apart from events",
   /passive notice/.test(src) && /not evidence of activity since the last 13F/.test(src));
ok("a successor is explained when followed", /Book moved to \{m\.successor\.name\}/.test(src));

// ── 4. the backend keeps the promises ───────────────────────────────────
ok("anonymous evidence cannot name a fund (enforced in code)",
   /cannot be attributed to a fund/.test(sources) && /def attribution_ok/.test(sources));
ok("the store refuses a smuggled attribution", /refusing to store a record that attributes anonymous data/.test(watch));
ok("every evidence row carries as_of and public_on", /"as_of": as_of, "public_on": public_on/.test(sources));
ok("a 13F-NT is followed to the successor", /successor_from_notice/.test(watch) && /"NOTICE" not in/.test(sources));
ok("a 13G never counts as activity", /\.get\("kind"\) == "EVENT"/.test(watch.slice(watch.indexOf("def activity_state"))));
ok("a restatement replaces, new holdings append", /RESTATEMENT/.test(watch) && /NEW HOLDINGS/.test(watch));
ok("split lines are summed before the diff", /def _aggregate/.test(sources) && /HOWARD HUGHES|Howard Hughes/.test(sources));
ok("unmapped positions are reported, not filed under Other", /never dropped, never filed under a twelfth sector/.test(sources));
ok("the registry knows a manager is not a CIK", /def link_successor/.test(registry) && /def current_cik/.test(registry));
ok("READABLE vs OPAQUE is declared for every seed manager",
   seed.managers.every((m) => m.turnover === "READABLE" || m.turnover === "OPAQUE"));
ok("the brief's nine names are seeded",
   ["Citadel", "Millennium", "Bridgewater", "Two Sigma", "Renaissance", "Pershing", "Scion"].every((n) => seed.managers.some((m) => m.name.includes(n)))
   && seed.managers.some((m) => (m.people || []).includes("Bill Ackman")) && seed.managers.some((m) => (m.people || []).includes("Michael Burry")));
ok("and the list goes well beyond them", seed.managers.length >= 30, String(seed.managers.length));
ok("Scion is CEASED and Burry's own channel is on file",
   seed.managers.some((m) => m.key === "scion" && m.status === "CEASED" && (m.feeds || []).some((f) => /substack/.test(f.url))));
ok("the refresh cadences are published in thresholds", /"hedge"/.test(read("thresholds.json")) && /def config\(\)/.test(watch));
ok("the worker is lazy and offline-safe", /def _kick/.test(watch) && /if not S\.available\(\):\s*\n\s*return/.test(watch));
ok("a parsed filing is kept forever", /FOREVER/.test(sources) && /"doc": FOREVER/.test(sources));

// ── 5. routes ───────────────────────────────────────────────────────────
ok("/api/hf routes exist", /parsed\.path == "\/api\/hf"/.test(dash) && /section == "fund"/.test(dash)
   && /section == "watchlist"/.test(dash) && /section == "config"/.test(dash));
ok("the watchlist is writable", /parsed\.path == "\/api\/hf\/watchlist"/.test(dash) && /set_watchlist\(payload\)/.test(dash));
ok("the routes are in the HTTP smoke", /"\/api\/hf"/.test(smoke) && /"\/api\/hf\/fund\?key=pershing"/.test(smoke));
ok("EDGAR is the record; UW is a cross-check, not a source", /Unusual Whales is a cross-check/.test(dash) && /institution_holdings/.test(read("unusual_whales_client.py")));
ok("sector labels are folded onto the app's eleven sectors", /sector_norm=/.test(dash) && /SECTOR_BY_ETF/.test(dash));
ok("the version was bumped", /const APP_VERSION = "4\.85"/.test(appSrc));

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
