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

// ── 8. the outcome grader (v4.88) ───────────────────────────────────────
const grade = read("hf_grade.py");

ok("the grader is a panel with a tooltip on every column",
   /function HfGrades/.test(src) && /<HfGrades apiFetch/.test(src)
   && ["grade","grade_reversal","grade_base","grade_lift","grade_interval","grade_episodes",
       "grade_horizon","grade_source","grade_verdicts","grade_not_graded","grade_proxy"]
      .every((k) => HF_TIPS_OF(src, k).length > 40));
ok("a verdict is never scored right or wrong",
   /not a forecast of returns/.test(grade) && /never a hit rate/.test(grade));
ok("the base rate is the same market, so a falling year is not a finding",
   /base rate is every week of the same market/.test(grade) && /grade_base/.test(src));
ok("every share carries its sample size and interval",
   /def wilson/.test(grade) && /95% interval/.test(src));
ok("clustered episodes are counted and the optimism disclosed",
   /def episodes/.test(grade) && /OPTIMISTIC/.test(grade) && /grade_episodes/.test(src));
ok("crowding is graded point in time, with no lookahead",
   /Point in time by construction/.test(scan) && /as_of` has to move with the window/.test(scan));
ok("each market is reconstructed as deep as its own series",
   /each as deep as its OWN series allows/.test(scan));
ok("a market with no honest proxy is not graded",
   /NOT_GRADED/.test(grade) && /roll a futures\s*\n?\s*curve/.test(grade) && /grade_not_graded/.test(src));
ok("the grader is pure — no I/O, no clock",
   !/datetime\.now|urllib|json\.load|[^.\w]open\(/.test(grade));
ok("/api/hf/grades routes exist", /section == "grades"/.test(dash)
   && /section == "grades\/status"/.test(dash) && /section == "grades\/build"/.test(dash));
ok("the grade routes are in the HTTP smoke", /"\/api\/hf\/grades"/.test(smoke));
ok("the grade cadence is published in thresholds",
   /"grade"/.test(read("thresholds.json")) && /min_history_weeks/.test(read("thresholds.json")));

// The X channel is optional and must be a no-op without a token.
ok("X statements are gated on a bearer token",
   /def x_available/.test(sources) && /S\.x_available\(\)/.test(watch));
ok("no token means an empty answer, never an error",
   /Returns an empty list on every failure path/.test(sources));
ok("a post is a statement, never a position",
   /never a position/.test(sources) && /own\.append/.test(watch));
ok("the config route reports whether the channel is live",
   /"x_statements": _hfsrc\.x_available\(\)/.test(dash));


ok("an ungraded market never inflates the episode count",
   /Episodes are counted per horizon/.test(grade) && /per_horizon/.test(grade));
ok("a failed close fetch is not cached as a finished grade",
   /Storing this and\s*\n?\s*# stamping it fresh/.test(scan)
   && /No weekly closes were returned/.test(scan) && /it will try again/.test(scan));
ok("the verdict distributions are rendered, not only computed",
   /What followed each weekly answer/.test(src) && /verdicts \|\| \{\}/.test(src)
   && /by_question/.test(src) && /v\.base/.test(src));
ok("at least one manager can actually use the X channel",
   JSON.parse(read("hf_watchlist.json")).managers.some((m) => m.x_handle));
ok("a handle is never derived from a name",
   /guessed handle would put words in a manager's mouth/.test(read("hf_watchlist.json"))
   && /nothing is derived from the name/.test(src));
ok("the editor can add a handle, with its own tooltip",
   /X handle \(optional\)/.test(src) && HF_TIPS_OF(src, "x_handle").length > 40);

// The wiring, not the wording: the stamp is written, staleness reads it, the
// status route hands it to the panel, and the wait is a knob rather than a
// number buried in the code. An earlier version of this guard pinned a
// sentence in a comment and failed when the comment was rewritten.
ok("a failed grade waits instead of looping",
   /_STATE\["grades_retry_at"\] = /.test(scan) && /retry_at = _STATE\.get\("grades_retry_at"\)/.test(scan)
   && /"retry_after": _STATE\.get\("grades_retry_at"\)/.test(scan)
   && /_grade_knob\("retry_hours"\)/.test(scan) && /"retry_hours"/.test(read("thresholds.json")));
ok("an X handle cannot smuggle a second account into a manager's own words",
   /X_HANDLE_RE/.test(sources) && /def x_query_for/.test(sources)
   && /cannot survive a user-supplied query string/.test(sources));
ok("the watchlist stores a handle, never a query",
   !/x_query/.test(read("hf_watchlist.json").replace(/"_doc":[^\n]*/, ""))
   && /x_query is not accepted/.test(registry));
ok("the editor refuses a handle that is not one",
   /\^\[A-Za-z0-9_\]\{1,15\}\$/.test(src) && /not valid/.test(src));

// ── Phase 5A: the four weekly answers, recomputed for past weeks ──────────
const replay = read("hf_replay.py");

ok("the replay calls the board's own verdict functions instead of copying them",
   /P\.exposure\(/.test(replay) && /P\.leverage\(/.test(replay)
   && /P\.longs_and_shorts\(/.test(replay)
   && !/def build_verdict/.test(replay));
ok("a verdict is only recomputed when every input that decides it is there",
   /REQUIRES/.test(replay) && /"exposure": \("etf_flows",\)/.test(replay)
   && /"shorts": \("short_interest", "short_volume"\)/.test(replay)
   && /"leverage": \(\)/.test(replay));
ok("short interest is keyed on when it was published, not when it settled",
   /public_on/.test(replay) && /never on the settlement date/.test(replay));
ok("a half-filled window is refused rather than averaged",
   /len\(window\) < days/.test(replay) && /len\(window\) < sessions/.test(replay));
ok("a reading is graded under the week it describes",
   /def data_week/.test(scan) && /cftc_as_of/.test(scan)
   && /def stored_readings/.test(scan) && /def replayed_readings/.test(scan));
ok("a stored week is never graded twice",
   /have = \{r\["week"\] for r in stored_readings\(\)\}/.test(scan));
ok("the daily short-volume cache keeps numbers, not files",
   /def load_shvol/.test(scan) && /def gather_shvol_history/.test(scan)
   && /shvol_budget_days/.test(read("thresholds.json")));
ok("a partial ETF universe is refused, not summed",
   /would not be the total the board adds up/.test(scan));
ok("the panel says where its graded weeks came from",
   /readings_from/.test(src) && /recomputed from data as it stood then/.test(src)
   && /replay_coverage/.test(src));
ok("the panel no longer claims the four answers cannot be reconstructed",
   !/cannot be reconstructed from history/.test(src));
ok("each question shows how far back it reaches, and why it stops",
   /Reaches back to/.test(src) && /Why it stops there/.test(src)
   && /why_skipped/.test(src));
// This used to pin the label's exact wording, and broke the day the wording
// changed while the rule it guards was still kept. It now checks the WIRING:
// the label exists, and no raw week key reaches the screen anywhere. A
// browser check (test_hf_render.py) asserts the same rule on the drawn page.
ok("an ISO week is never shown to the reader",
   /const hfWeekLabel/.test(src) && /monday\.toLocaleDateString/.test(src)
   && !/>\{(d|h|w|cmp\.older|cmp\.newer)\.week\}/.test(src)
   && !/<option key=\{w\} value=\{w\}>\{w\}</.test(src)
   && (src.match(/hfWeekLabel\(/g) || []).length >= 12);
ok("the four questions are spelled out, never shown as keys",
   /HF_QUESTION_NAME/.test(src) && /Adding shorts, or covering/.test(src)
   && /HF_QUESTION_NAME\[r\.q\]/.test(src));
ok("every new tooltip is written and long enough to say something",
   ["grade_replay", "grade_recorded", "grade_replayed", "grade_replay_span",
    "grade_replay_skipped"].every((k) => HF_TIPS_OF(src, k).length > 60));
ok("the replay routes are served",
   /section == "replay"/.test(dash) && /section == "replay\/status"/.test(dash)
   && /section == "replay\/build"/.test(dash) && /"replay": _hfreplay/.test(dash));

// ── Phase 5B: the names behind the crowd ──────────────────────────────────
const namesPy = read("hf_names.py");

ok("an opaque book is never counted as a view",
   /!= READABLE/.test(namesPy) && /def eligible/.test(namesPy)
   && /why_opaque/.test(namesPy) && /hedging and index exposure/.test(namesPy));
ok("a put is never counted as ownership",
   /def _is_put/.test(namesPy) && /if _is_put\(row\):\n                continue/.test(namesPy)
   && /report the position exactly\n    backwards/.test(namesPy));
ok("puts are reported separately rather than dropped",
   /def _puts/.test(namesPy) && /Held as put options/.test(src));
ok("it never claims most-owned from ten positions",
   /not how many own it/.test(namesPy) && /eleventh place/.test(namesPy)
   && /never "most owned", which would need whole books/.test(namesPy)
   && /Held among the ten largest/.test(src)
   && !/Most owned/.test(src));
ok("a 13F carries no shorts and the card says so",
   /short sales are not reported in/.test(namesPy));
ok("the denominator travels with the count",
   /n_counted/.test(namesPy) && /watched managers counted/.test(src));
ok("rows are VERIFIED, which is what lets them name a fund",
   /"class": S\.VERIFIED/.test(namesPy) && /HfTag cls="VERIFIED FUND ACTIVITY"/.test(src));
ok("an unmapped CUSIP is shown by issuer, not dropped",
   /def _unmapped/.test(namesPy) && /had no ticker in the map/.test(src));
ok("mixed quarters are shown as a span, never one date",
   /as_of_first/.test(namesPy) && /as_of_last/.test(namesPy)
   && /r\.as_of_first === r\.as_of_last/.test(src));
ok("the position rows are kept out of the browser payload",
   /def positions\(\)/.test(watch) && /would be by\n    far the largest thing on it/.test(watch)
   && /positions_fn/.test(scan));
ok("hf_scan still never imports hf_watch",
   !/^import hf_watch/m.test(scan) && /_POSITIONS_FN/.test(scan));
ok("the names section is mounted with its own tooltips",
   /<HfNames apiFetch/.test(src) && /function HfNames/.test(src)
   && ["names", "names_opaque", "names_puts", "names_period", "names_who"]
        .every((k) => HF_TIPS_OF(src, k).length > 60));
ok("the names route is served",
   /section == "names"/.test(dash) && /"names": _hfnames/.test(dash)
   && /positions_fn=lambda/.test(dash));

// ── Phase 6: alerts ───────────────────────────────────────────────────────
const alertPy = read("hf_alert.py");

ok("an alert fires on a change, never on a state",
   /def crowding_changes/.test(alertPy) && /was == state/.test(alertPy)
   && /is not news on any of them/.test(alertPy));
ok("a wobble that never touches the crowded state is not an event",
   /if CROWDED not in \(state, was\)/.test(alertPy));
ok("the first run primes and sends nothing",
   /the first run records what it finds and sends nothing/.test(alertPy)
   && /primed/.test(scan) && /store\.get\("primed"\)/.test(scan));
ok("everything seen is remembered, sent or not",
   /"remember": \[r\["key"\] for r in \(rows or \(\)\)\]|"remember": \[r\["key"\] for r in \(rows or \[\]\)\]/.test(alertPy)
   && /must never arrive later dressed as new/.test(scan));
ok("nothing is ever sent twice",
   /already sent/.test(alertPy) && /sent_keys/.test(alertPy));
ok("a crowding alert can never name a fund",
   /def attribution_ok/.test(alertPy) && /cannot click through to check/.test(alertPy)
   && /an alert carried a fund name on anonymous evidence/.test(scan));
ok("a 13G is never an alert",
   /form\.startswith\("SC 13G"\)/.test(alertPy));
ok("a push never shows an ISO date",
   /def long_date/.test(alertPy) && /%B %-d, %Y/.test(alertPy)
   && /never an ISO string, on a phone least of all/.test(alertPy));
ok("the report push names the week it covers",
   /def week_start/.test(alertPy) && /week the report is not about/.test(alertPy));
ok("a backlog does not empty onto a lock screen",
   /cap/.test(alertPy) && /cap_per_run/.test(read("thresholds.json")));
ok("building a board is never a way to send a push",
   /building a board must never be a way to\n            # send somebody a push/.test(scan));
ok("hf_scan is told how to send, never how push works",
   /_ALERT_FN/.test(scan) && !/pushover|ntfy/i.test(scan)
   && /alert_fn=lambda title/.test(dash));
ok("the panel shows what was NOT sent and why",
   /Seen, and deliberately not sent/.test(src) && /would_send/.test(src)
   && /Would go out on the next check/.test(src));
ok("a quiet week is shown as correct, not as a failure",
   /that is the correct answer, not a failure/.test(src));
ok("every alert tooltip is written and long enough to say something",
   ["alerts", "alerts_can_send", "alerts_primed", "alerts_would", "alerts_sent",
    "alerts_kind", "alerts_held", "alerts_cap"].every((k) => HF_TIPS_OF(src, k).length > 60));
ok("the alerts route is served and the config reports push",
   /section == "alerts"/.test(dash) && /"alerts": _hfalert/.test(dash)
   && /"push": _push_configured\(\)/.test(dash));

ok("the version was bumped", /const APP_VERSION = "4\.91"/.test(appSrc));

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
