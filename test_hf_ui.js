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
// Pull one tooltip's text out of the HF_TIP table so a guard can assert on it.
const HF_TIPS_OF = (s, key) => {
  const m = new RegExp(`\\n  ${key}: "((?:[^"\\\\]|\\\\.)*)"`).exec(s);
  return m ? m[1] : "";
};

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
const pulse = read("hf_pulse.py");
const scan = read("hf_scan.py");

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

// ── 6. the Pulse: the aggregate layer (v4.86) ───────────────────────────
ok("the two layers are switchable and named", /The Pulse<\/button>/.test(src) && /Named Funds<\/button>/.test(src)
   && /setView\("pulse"\)/.test(src) && /setView\("funds"\)/.test(src));
ok("the Pulse panel exists and has its own component", /function PulsePanel/.test(src));
ok("each weekly question renders verdict, confidence, streak and persistence",
   /function HfQuestion/.test(src) && /q\.verdict/.test(src) && /HfConfidence/.test(src)
   && /HF_TIP\.pulse_streak/.test(src) && /HF_TIP\.pulse_persist/.test(src));
ok("the four windows 2/4/8/12 are on screen", /\["2", "4", "8", "12"\]\.map/.test(src));
ok("every input behind a verdict can be opened and shows its class and date",
   /function HfInputs/.test(src) && /HfTag cls=\{i\.class\}/.test(src) && /hfDate\(i\.as_of\)/.test(src));
ok("conflicts are shown, never averaged",
   /Disagreeing:/.test(src) && /def conflicts/.test(pulse) && /never averaged/.test(HF_TIPS_OF(src, "pulse_conflict")));
ok("missing sources are named", /No answer from:/.test(src) && /Sources that had nothing this week/.test(src));
ok("what changed since last week is a section", /What changed since last week/.test(src)
   && /def changed_since/.test(pulse) && /That is itself a finding/.test(src));
ok("the weekly history is listed", /Every week read so far/.test(src) && /def history/.test(scan));
ok("sectors rank in their own terms, and that is explained on screen",
   /sec\.ranked_by/.test(src) && /move_size_text/.test(src) && /in its own terms/i.test(pulse));
ok("the four sectors with no futures contract are called out",
   /flows only/.test(src) && /CFTC_SECTORS_MISSING/.test(sources) && /no_futures/.test(src));
ok("crowding is lean AND size, never one fact twice",
   /never one counted twice/.test(pulse) && /hf-crowd-crowded/.test(css));
ok("crowded single names are filtered for liquidity", /DTC_CAP/.test(pulse) && /MIN_ADV/.test(pulse)
   && /Days to cover/.test(src));
ok("a flat history has no percentile", /max\(xs\) == min\(xs\)/.test(pulse));
ok("the change is the signal, not the level — said in code and on the card",
   /structurally net short/.test(pulse) && /HF_TIP\.pulse_unusual/.test(src));
ok("leverage reads gross, and Form PF is context with its own date",
   /lev_gross/.test(pulse) && /five months behind/.test(pulse) && /HF_TIP\.pulse_ofr/.test(src));
ok("confidence counts independent classes and cannot be created by a quote",
   /independent evidence class/.test(pulse) && /cannot create one on their own/.test(pulse)
   && /can never create a\s*\n?\s*verdict alone/.test(pulse));
ok("the pulse is pure — no I/O, no clock", !/datetime\.now|urllib|json\.load|open\(/.test(pulse));
ok("the aggregate layer never names a fund",
   /cannot be attributed to a fund/.test(sources) && /refusing to store a pulse reading/.test(scan));
ok("the fund cards receive the trend injected, not imported",
   /trend_fn/.test(watch) && !/import hf_scan/.test(watch) && /trend_fn=lambda: _hfscan\.headline\(\)/.test(dash));
ok("the broader block on a fund card carries the Pulse's date and class",
   /bt\.as_of_text/.test(src) && /HfTag cls=\{\(bt && bt\.class\)/.test(src));
ok("/api/hf/pulse routes exist", /section == "pulse"/.test(dash) && /section == "pulse\/history"/.test(dash)
   && /section == "pulse\/week"/.test(dash));
ok("the pulse routes are in the HTTP smoke", /"\/api\/hf\/pulse"/.test(smoke));
ok("the pulse cadence is published in thresholds", /"pulse"/.test(read("thresholds.json")));

// ── 7. the weekly report and the prime-broker channel (v4.87) ───────────
const press = read("hf_press.py");
const report = read("hf_report.py");

ok("the Weekly Report is a third panel with its own view",
   /setView\("report"\)/.test(src) && /Weekly Report/.test(src) && /<ReportPanel/.test(src));
ok("the report panel reads /api/hf/report", /\/api\/hf\/report/.test(src));
ok("every report section carries a tooltip",
   ["report", "report_week", "report_revision", "report_summary", "report_conclusion",
    "report_trend", "report_filings", "report_activity", "report_watchlist",
    "report_conflicts", "report_changed", "report_history", "report_compare",
    "report_limits", "press", "press_quote", "press_carried", "press_period",
    "press_captured", "press_outlet"].every((k) => HF_TIPS_OF(src, k).length > 40));
ok("the conflicts section is never collapsed by default",
   /never collapsed/.test(src) && !/useState\(false\)[\s\S]{0,200}HfReportConflicts/.test(src));
ok("dates in the report are spelled out, never ISO",
   /long_date/.test(report) && /%B/.test(report) && /as_of_text/.test(src));

// The press channel refuses more than it accepts, and each refusal is a
// real headline from the captured corpus.
ok("a bank must be cited as the source, not merely mentioned",
   /_ATTRIBUTION/.test(press) && /Mention is not attribution/.test(press));
ok("returns are not positioning", /_PERFORMANCE/.test(press) && /about returns, not positioning/.test(press));
ok("a stated non-US market is captured and not counted",
   /_NON_US/.test(press) && /a stated non-US market/.test(press));
ok("an ambiguous direction produces no evidence",
   /no unambiguous direction/.test(press) && /does not say which/.test(press));
ok("the period is never invented — only the publication date is claimed",
   /"as_of": None/.test(press) && /period in words rather than dates/.test(press));
ok("one note carried by five outlets counts once",
   /carried_by/.test(press) && /cannot vote five times/.test(press));
ok("only a wire or the bank itself counts as evidence",
   /WIRES/.test(press) && /FIRST_PARTY/.test(press) && /outlet is not a wire/.test(press));
ok("a prime-broker quote is supporting evidence at half weight",
   /weight=0\.5/.test(pulse) && /can only\s*\n?\s*ever corroborate/.test(pulse));
ok("the press channel is pure — no I/O, no clock",
   !/datetime\.now|urllib|json\.load|[^.\w]open\(/.test(press));
ok("the report is pure — no I/O, no clock",
   !/datetime\.now|urllib|json\.load|[^.\w]open\(/.test(report));

// The store: a report records a moment, so a rebuild adds to it.
ok("a rebuild appends a revision rather than overwriting",
   /revisions/.test(scan) && /Reports append, snapshots replace/.test(scan));
ok("a rebuild in the same week diffs against last week, not itself",
   /must diff against last week/.test(scan));
ok("the report store refuses a smuggled attribution",
   /refusing to store a report that attributes anonymous data to a fund/.test(scan));
ok("the two layers meet by injection in both directions — no cross import",
   !/import hf_watch/.test(scan) && !/import hf_scan/.test(watch)
   && /funds_fn=lambda: _hfwatch\.snapshot\(\)/.test(dash));
ok("nothing to compare against is not the same as nothing changed",
   /first stored report/.test(report) && /comparable/.test(src));
ok("compare and what-changed use the same diff",
   /reuses `changes`/.test(report) || /It reuses `changes`/.test(report));

ok("/api/hf/report routes exist",
   /section == "report"/.test(dash) && /section == "report\/history"/.test(dash)
   && /section == "report\/compare"/.test(dash) && /section == "report\/build"/.test(dash)
   && /section == "press"/.test(dash));
ok("the report routes are in the HTTP smoke", /"\/api\/hf\/report"/.test(smoke)
   && /report\/compare/.test(smoke));
ok("the report cadence is published in thresholds",
   /"report"/.test(read("thresholds.json")) && /press_max_age_days/.test(read("thresholds.json")));
ok("the report panel has styles of its own", /\.hf-report/.test(css) && /\.hf-conflicts/.test(css));


// Five findings from the post-merge review, each verified against the code
// before it was fixed.
ok("a week rollover re-reads the pulse instead of refiling the old week",
   /board\.get\("week"\) != week_key\(_today\(\)\)/.test(scan)
   && /must belong to THIS week/.test(scan));
ok("revision numbers are numbered from builds, not from survivors",
   /Number from the builds that have HAPPENED/.test(scan));
ok("watchlist changes are derived from the two reports being compared",
   /Derived from both reports here/.test(report));
ok("a bank's name cannot be read as a place",
   /_without_banks/.test(press) && /contains America/.test(press));
ok("named activity is filtered to the report's own week",
   /does NOT mean the event happened this week/.test(report)
   && /n_filed_since/.test(report) && /n_filed_since/.test(src));
ok("the count still carrying a filing has its own tooltip",
   HF_TIPS_OF(src, "report_carrying").length > 40);


ok("the card renders the report's own activity sentence, not its own",
   /d\.funds \|\| \{\}\)\.sentence/.test(src) && /not re-derived here/.test(src)
   && /card renders this verbatim/.test(report));
ok("every activity state produces a true sentence",
   /def activity_sentence/.test(report) && /explicit ladder/.test(report));

ok("an older stored report still renders an activity line",
   /def normalize/.test(report) && /filled in on READ, never written/.test(report)
   && /sentence_filled_in/.test(src) && /RPT\.normalize/.test(scan));
ok("a legacy count is not relabelled as this week",
   /def legacy_activity_sentence/.test(report) && /predates the weekly filter/.test(report));
ok("the filled-in sentence says so, with a tooltip",
   HF_TIPS_OF(src, "report_filled_in").length > 40 && /written for this report on reading it/.test(src));

ok("the legacy caveat renders above the acted list, not only when it is empty",
   /sentence_filled_in && \(d\.funds \|\| \{\}\)\.n_acted/.test(src)
   && /belongs\s*\n?\s*above the list, not only in the empty branch/.test(src));

ok("the version was bumped", /const APP_VERSION = "4\.87"/.test(appSrc));

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
