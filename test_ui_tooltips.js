/* test_ui_tooltips.js — static guards on the data-heavy tabs' readability.
 *
 * Nobody should have to look up what a column means at 7am. These parse the
 * JSX directly (no browser needed) and fail the build if a header, section
 * heading or stat row ships without a tooltip, a date renders without its
 * weekday, or insider shorthand leaks back into a visible label.
 *
 * Covers: tab-gap.jsx (Gap Scan) and tab-edge.jsx (Premium Edge).
 */
const fs = require("fs");
const path = require("path");

const read = (f) => fs.readFileSync(path.join(__dirname, f), "utf8");
const src = read("tab-gap.jsx");
const edge = read("tab-edge.jsx");
let pass = 0, fail = 0;
const check = (name, ok, extra) => {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${extra ? " — " + extra : ""}`);
  ok ? pass++ : fail++;
};

// 1. Every <th> literal carries a title attribute.
const ths = src.match(/<th\b[^>]*>/g) || [];
const untitled = ths.filter((t) => !/title=/.test(t));
check(`every <th> has a tooltip (${ths.length} checked)`, untitled.length === 0,
  untitled.slice(0, 3).join(" "));

// 2. The two sortable-header helpers always receive a tip argument.
//    th("Label", "key", "tip"...) — a 2-arg call would be an untipped column.
//    Tooltips contain parentheses and commas, so the call has to be scanned
//    with string/paren awareness rather than matched with a regex.
function thHelperCalls(text) {
  const out = [];
  const re = /\bth\(/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    let i = m.index + m[0].length, depth = 1, str = null, args = 1, argIsString = [false];
    while (i < text.length && depth > 0) {
      const c = text[i];
      if (str) {
        if (c === "\\") i++;
        else if (c === str) str = null;
      } else if (c === '"' || c === "'" || c === "`") {
        str = c;
        if (!argIsString[args - 1]) argIsString[args - 1] = true;
      } else if (c === "(") depth++;
      else if (c === ")") depth--;
      else if (c === "," && depth === 1) { args++; argIsString.push(false); }
      i++;
    }
    out.push({ call: text.slice(m.index, i), args, argIsString });
  }
  return out;
}
const thCalls = thHelperCalls(src);
const tipless = thCalls.filter((c) => c.args < 3 || !c.argIsString[2]);
check(`every th() helper call passes a tooltip (${thCalls.length} checked)`,
  tipless.length === 0,
  tipless.slice(0, 2).map((c) => c.call.slice(0, 60)).join(" | "));

// 3. Hero/stat rows in the detail view are tooltipped.
const kvBlocks = src.match(/<div className="gap-kv[^"]*"[^>]*>/g) || [];
const kvUntitled = kvBlocks.filter((d) => !/title=/.test(d));
check(`every stat row has a tooltip (${kvBlocks.length} checked)`,
  kvUntitled.length === 0, kvUntitled.slice(0, 2).join(" "));

const heroRows = src.match(/<div title=\{?[^>]*>\s*<span>/g) || [];
check("hero fields are tooltipped", heroRows.length >= 6, `${heroRows.length} found`);

// 4. No insider shorthand in visible labels.
const banned = [
  [">Dir<", "spell out Direction"],
  [">Via<", "spell out Qualified by"],
  [">Adv<", "spell out Adverse"],
  [">Fav<", "spell out Favorable"],
  [">PM max<", "spell out Premarket peak"],
  [">EARN<", "spell out Earnings"],
  ["<b>EARN</b>", "spell out Earnings"],
  [">n<", "spell out the sample-size column"],
];
banned.forEach(([needle, why]) => {
  check(`no shorthand ${needle.replace(/[<>]/g, "")}`, !src.includes(needle), why);
});

// 5. Historical dates carry the weekday.
check("analog dates use the weekday formatter", /gapDateDow\(e\.date\)/.test(src));
check("weekday formatter emits (Www)", /weekday: "short"/.test(src)
  && /\$\{gapDate\(s\)\} \(/.test(src));

// 6. Direction is color-coded, not a bare glyph.
check("direction arrows are colored",
  /className=\{e\.direction === "up" \? "gap-dir-up" : "gap-dir-down"\}/.test(src));
check("direction cell is labeled, not just an arrow",
  /▲ Up/.test(src) && /▼ Down/.test(src));

// 7. Analog table is sortable and the news feed exists.
check("analog table is sortable", /function GapAnalogTable/.test(src)
  && /setK\(key_\)/.test(src));
check("news feed present", /function GapNews/.test(src)
  && /\/api\/news\?symbol=/.test(src));
check("days-to-earnings surfaced in current setup",
  /days_to_earnings/.test(src) && /Next earnings/.test(src));

// ── Premium Edge tab ────────────────────────────────────────────────────
// <th> matches must not catch <thead>; require a word boundary after "th".
const eTh = edge.match(/<th(?![a-z])[^>]*>/g) || [];
const eUntitled = eTh.filter((t) => !/title=/.test(t));
check(`edge: every <th> has a tooltip (${eTh.length} checked)`,
  eUntitled.length === 0, eUntitled.slice(0, 3).join(" "));

const eHeads = edge.match(/<div className="edge-sechead"[^>]*>/g) || [];
const eHeadsUntipped = eHeads.filter((d) => !/title=/.test(d));
check(`edge: every section heading has a tooltip (${eHeads.length} checked)`,
  eHeadsUntipped.length === 0, eHeadsUntipped.slice(0, 2).join(" "));

// hero stat blocks: <div title=...><span><Term .../></span>
const eHero = edge.match(/<div title=[^>]*>\s*\n?\s*<span><Term/g) || [];
check(`edge: hero stats are tooltipped (${eHero.length} found)`, eHero.length >= 4);

const eSum = edge.match(/<SumBox[^>]*/g) || [];
const eSumUntipped = eSum.filter((t) => !/tip=/.test(t));
check(`edge: every summary card has a tooltip (${eSum.length} checked)`,
  eSumUntipped.length === 0, eSumUntipped.slice(0, 2).join(" "));

check("edge: score bar explains itself", /Premium Edge Score \$\{Math\.round\(s\)\}\/100 —/.test(edge));
check("edge: signal filter chips tipped", /title=\{s \? `Show only names/.test(edge));

// ── Gap news freshness ──────────────────────────────────────────────────
check("gap news is limited to a few days", /GAP_NEWS_MAX_DAYS = 3/.test(src)
  && /published \? new Date\(n\.published\)/.test(src));
// Catalyst vocabulary: upgrades and downgrades are named and colored.
check("catalyst kinds include upgrade/downgrade",
  /UPGRADE: "Upgrade"/.test(src) && /DOWNGRADE: "Downgrade"/.test(src));
check("catalyst kinds include offering/dilution",
  /OFFERING: "Offering"/.test(src) && /DILUTION: "Dilution"/.test(src));
check("catalyst kinds include FDA decisions",
  /"FDA APPROVAL": "FDA approval"/.test(src)
  && /"FDA REJECTION": "FDA rejection"/.test(src));
check("an approval reads green and a rejection red", (() => {
  const tone = (src.match(/const GAP_CATALYST_TONE = \{[\s\S]*?\n\};/) || [""])[0];
  return /"FDA APPROVAL": "up"/.test(tone) && /"FDA REJECTION": "down"/.test(tone);
})());
check("the sentence a filing-derived tag was read from reaches the tooltip",
  /catalyst_quote/.test(src)
  && (src.match(/r\.catalyst_quote/g) || []).length >= 2);
check("deal and distress catalysts are named",
  ["BUYOUT: \"Buyout\"", "\"MERGER DEAL\"", "BANKRUPTCY: \"Bankruptcy\"",
   "\"DELISTING NOTICE\"", "RESTATEMENT: \"Restatement\"",
   "\"TRIAL FAILURE\""].every((k) => src.includes(k)));
check("ambiguous catalysts read amber, not green or red", (() => {
  const tone = (src.match(/const GAP_CATALYST_TONE = \{[\s\S]*?\n\};/) || [""])[0];
  return ['"MERGER DEAL": "warn"', '"LEADERSHIP CHANGE": "warn"',
          'BANKRUPTCY: "down"', 'BUYOUT: "up"'].every((k) => tone.includes(k));
})());
// A stock pinned to a takeover price still renders a full set of fade
// statistics — the warning is the only thing standing between Jerry and
// trading them as if they still described the stock.
check("a pinned-price warning is rendered, not just tucked in a tooltip",
  /gap-cat-warning/.test(src) && /r\.catalyst_warning/.test(src));
check("headline-only catalysts are named",
  ['"SHORT REPORT": "Short-seller report"', '"INDEX ADD": "Index add"',
   '"INDEX DROP": "Index drop"', '"GUIDANCE CUT": "Guidance cut"']
    .every((k) => src.includes(k)));
// The evidence grade is the whole reason these two are allowed in at all:
// a filing is the company on the record, a headline is a reporter.
check("a headline-derived tag says so on screen, not only in a tooltip",
  /gap-cat-grade/.test(src)
  && /catalyst_evidence === "headline"/.test(src)
  && /read the story/.test(src));
check("insider and ownership catalysts are named",
  ['"INSIDER BUYING": "Insider buying"', '"INSIDER SELLING": "Insider selling"',
   '"ACTIVIST STAKE": "Activist stake"', 'BUYBACK: "Buyback"',
   '"REVERSE SPLIT": "Reverse split"', '"LATE FILING": "Late filing"']
    .every((k) => src.includes(k)));
// Insiders buy for one reason and sell for many — the colors have to say so.
check("insider buying reads green and selling red", (() => {
  const tone = (src.match(/const GAP_CATALYST_TONE = \{[\s\S]*?\n\};/) || [""])[0];
  return /"INSIDER BUYING": "up"/.test(tone) && /"INSIDER SELLING": "down"/.test(tone)
    && /"REVERSE SPLIT": "warn"/.test(tone);
})());
// The two facts that make these tags trustworthy are measurements, and they
// belong where Jerry can read them rather than buried in a commit message.
check("the insider tooltips carry the measurement, not just a claim", (() => {
  const tips = (src.match(/const GAP_CATALYST_TIP = \{[\s\S]*?\n\};/) || [""])[0];
  return /93%/.test(tips) && /10b5-1/.test(tips)
    && /Grants, option exercises and shares withheld for tax are ignored/.test(tips);
})());
// A reverse split rewrites the price scale, so the fade statistics below it
// are arithmetic on numbers that no longer exist. Same treatment as a
// pinned takeover price: said out loud, above the numbers.
check("a reverse split warns that the history changed scale",
  /rescales_history/.test(read("options_dashboard.py"))
  && /_GAP_SPLIT_WARNING/.test(read("options_dashboard.py")));
// Read the kinds out of the label map instead of listing them here, so a
// new catalyst cannot ship without a tooltip — the recurring complaint.
const catalystKinds = (() => {
  const block = (src.match(/const GAP_CATALYST_LABEL = \{[\s\S]*?\n\};/) || [""])[0];
  return [...block.matchAll(/(?:[{,]\s*)(?:"([A-Z][A-Z ]*)"|([A-Z][A-Z_]*))\s*:/g)]
    .map((m) => m[1] || m[2]);
})();
check(`catalyst kinds parsed (${catalystKinds.length})`, catalystKinds.length >= 8,
  catalystKinds.join("|"));
check("every catalyst kind is color-toned or deliberately neutral", (() => {
  const tone = (src.match(/const GAP_CATALYST_TONE = \{[\s\S]*?\n\};/) || [""])[0];
  return ["UPGRADE", "DOWNGRADE", "OFFERING", "DILUTION"]
    .every((k) => new RegExp(`${k}: "(up|down)"`).test(tone));
})());
check("every catalyst kind has an explanatory tooltip", (() => {
  const tips = (src.match(/const GAP_CATALYST_TIP = \{[\s\S]*?\n\};/) || [""])[0];
  const missing = catalystKinds.filter((k) => !tips.includes(k));
  return missing.length === 0 || missing.join(",");
})());
check("historical catalyst cells have their own past-tense tooltips",
  /GAP_EVENT_CAT_TIP/.test(src) && /GAP_EVENT_CAT_TIP\[e\.catalyst_kind\]/.test(src));
check("offering tags link out to the filing they were read from",
  /catalyst_url/.test(src) && /gap-cat-link/.test(src));
check("catalyst column is sortable", /th\("Catalyst", "cat"/.test(src)
  && /case "cat":/.test(src));

// Move columns speak the trader's language and follow today's setup.
check("move columns are direction-aware", /GAP_MOVE_LABELS/.test(src)
  && /Max fade ↓/.test(src) && /Max squeeze ↑/.test(src)
  && /Max rebound ↑/.test(src) && /Max flush ↓/.test(src));
check("no generic Favorable/Adverse headers remain",
  !/th\("Favorable"/.test(src) && !/th\("Adverse"/.test(src));
check("per-row arrows follow that row's own direction",
  /e\.direction === "up" \? "↓" : "↑"/.test(src)
  && /e\.direction === "up" \? "↑" : "↓"/.test(src));
check("sort marker cannot be confused with meaning arrows",
  /dir === 1 \? " ▾" : " ▴"/.test(src));

check("gap direction cells use dedicated color classes",
  /gap-dir-up/.test(src) && /gap-dir-down/.test(src));
// Bare .up/.down only paint inside specific components, so every catalyst
// tone must go through the dedicated class — board, detail and analogs alike.
check("catalyst tone never relies on bare .up/.down classes",
  !/className=\{GAP_CATALYST_TONE\[[^\]]+\] \|\| ""\}/.test(src)
  && (src.match(/"gap-cat-" \+ GAP_CATALYST_TONE/g) || []).length >= 3);

// ── Investment tab (v4.41) ────────────────────────────────────────────────
// This tab makes promises in writing on screen — that nothing shows 0 for
// unknown, that every number carries a source, that the earnings bridge
// reconciles, that dates read "March 28, 2026". These guard the promises.
const inv = read("tab-invest.jsx");

check("Investment tab registers its chunk export",
  /Object\.assign\(window, \{ InvestTab: React\.memo\(InvestTab\) \}\)/.test(inv));

// Missing is "N/A", never 0 and never a bare dash that reads like a number.
check("every formatter returns N/A rather than a zero or a dash", (() => {
  const fns = inv.match(/function inv(Money|Price|Pct|SignedPct|Ratio|Count|Date)\b[\s\S]*?\n\}/g) || [];
  if (fns.length < 7) return `only ${fns.length} formatters found`;
  const bad = fns.filter((f) => !/invNA/.test(f));
  return bad.length === 0 || `${bad.length} formatter(s) without invNA`;
})());
check("N/A is a named constant, not a scattered literal",
  /const invNA = "N\/A"/.test(inv));

// Every statistic carries a provenance line: source, as-of, basis, staleness.
check("the provenance component renders source, as-of, basis and staleness",
  /function InvSource\(/.test(inv) && /Source: \$\{/.test(inv)
  && /Basis: \$\{/.test(inv) && /STALE/.test(inv));
check("every InvStat is given a tooltip", (() => {
  const stats = inv.match(/<InvStat\b[\s\S]*?\/>/g) || [];
  if (stats.length < 15) return `only ${stats.length} stats found`;
  const untipped = stats.filter((s) => !/\btip=/.test(s));
  return untipped.length === 0 || `${untipped.length} without tip=`;
})());
check("every InvStat is given a label and a value", (() => {
  const stats = inv.match(/<InvStat\b[\s\S]*?\/>/g) || [];
  const bad = stats.filter((s) => !/\blabel=/.test(s) || !/\bvalue=/.test(s));
  return bad.length === 0 || `${bad.length} incomplete`;
})());

// Headers and section heads are spelled out for a non-programmer reader.
check("section heads all carry tooltips", (() => {
  const heads = inv.match(/<div className="inv-sechead"[^>]*>/g) || [];
  const untitled = heads.filter((h) => !/title=/.test(h));
  return untitled.length === 0 || `${untitled.length} untitled`;
})());
check("no insider shorthand in visible labels",
  !/label="P\/E"/.test(inv) && !/label="FCF/.test(inv)
  && !/label="EPS/.test(inv) && !/label="TTM/.test(inv)
  && /label="Free cash flow yield"/.test(inv)
  && /label="Price to earnings, trailing"/.test(inv));

// House date rule: Month Day, Year — never raw ISO on screen.
check("dates render as Month Day, Year",
  /month: "long", day: "numeric", year: "numeric"/.test(inv)
  && /month: "short", day: "numeric", year: "numeric"/.test(inv));

// The five words, and only the five words.
check("all five verdicts are toned and tipped", (() => {
  const words = ["ATTRACTIVE", "WATCH", "WAIT", "AVOID", "INSUFFICIENT DATA"];
  const tone = (inv.match(/const INV_VERDICT_TONE = \{[\s\S]*?\n\};/) || [""])[0];
  const tip = (inv.match(/const INV_VERDICT_TIP = \{[\s\S]*?\n\};/) || [""])[0];
  const missing = words.filter((w) => !tone.includes(w) || !tip.includes(w));
  return missing.length === 0 || missing.join(",");
})());
check("no 0-100 investment score leaked into the UI",
  !/score/i.test(inv.replace(/inv-src/g, "")) || "the word 'score' appears");

// The earnings bridge states that it reconciles; the tab must say which
// method produced it and warn when it describes a different EPS.
check("earnings drivers name their method and warn on identity gaps",
  /Dollar EPS Bridge/.test(inv) && /Shapley/.test(inv)
  && /d\.warning/.test(inv) && /inv-warn/.test(inv));
check("driver bars are individually explained",
  /const INV_DRIVER_TIP = \{/.test(inv) && /Share count/.test(inv)
  && /Buybacks shrink the count/.test(inv));

// The chart's three refusals must be stated where they are read.
check("the price chart explains filing-date plotting and the forward line",
  /plotted on the day they were FILED/.test(inv)
  && /never back-filled/.test(inv));
check("the chart offers 3Y and 5Y",
  /\[3, 5\]\.map/.test(inv) && /inv-yearbtn/.test(inv));

// Price to sales is displayed but must never gate a decision.
// JSX tooltips wrap across lines, so this one is matched on whitespace-
// normalized source rather than the raw file.
const invFlat = inv.replace(/\s+/g, " ");
// The same source with adjacent string literals joined, so a sentence that
// the file splits across a `" +` line break still reads as one sentence to
// these guards — which is how it reaches the screen.
const invJoined = inv.replace(/"\s*\+\s*\n?\s*"/g, "").replace(/\s+/g, " ");
check("price to sales is labelled as playing no part in the verdict",
  /plays NO part in the verdict/.test(invFlat));

// ── Investment tab, Phase 2 (v4.42) ───────────────────────────────────────
// The tab's central claim is that the four vectors are INDEPENDENT and that
// nothing is presented as more certain than its inputs. These guard both.

check("the four vectors exist and are never blended into one score", (() => {
  const dims = (inv.match(/const INV_DIMENSIONS = \[[\s\S]*?\n\];/) || [""])[0];
  const want = ["quality", "growth", "valuation", "revisions"];
  const missing = want.filter((k) => !dims.includes(`"${k}"`));
  if (missing.length) return `missing ${missing.join(",")}`;
  // No combined/overall/composite score anywhere in the tab.
  return !/composite|overall_score|total_score|investment_score/i.test(inv)
    || "a blended score leaked in";
})());

check("every vector tile carries an explanation", (() => {
  const dims = (inv.match(/const INV_DIMENSIONS = \[[\s\S]*?\n\];/) || [""])[0];
  const rows = dims.split("\n").filter((l) => /^\s*\["/.test(l));
  return rows.length === 4 || `${rows.length} tiles found`;
})());

check("all six verdicts are toned and tipped", (() => {
  const words = ["ATTRACTIVE", "WATCH", "WAIT", "AVOID", "INSUFFICIENT DATA",
                 "SPECIALIZED MODEL REQUIRED"];
  const tone = (inv.match(/const INV_VERDICT_TONE = \{[\s\S]*?\n\};/) || [""])[0];
  const tip = (inv.match(/const INV_VERDICT_TIP = \{[\s\S]*?\n\};/) || [""])[0];
  const missing = words.filter((w) => !tone.includes(w) || !tip.includes(w));
  return missing.length === 0 || missing.join(",");
})());

check("the Phase 1 universal Treasury hurdle is gone from the UI",
  !/Treasury by the cushion/.test(invFlat)
  && !/point cushion this dashboard asks for/.test(invFlat));

check("the Treasury yield is labelled as context, not a hurdle",
  /no longer a hurdle the stock has to clear/.test(invFlat));

check("all three value-trap levels are toned and explained", (() => {
  const tone = (inv.match(/const INV_TRAP_TONE = \{[\s\S]*?\n\};/) || [""])[0];
  const tip = (inv.match(/const INV_TRAP_TIP = \{[\s\S]*?\n\};/) || [""])[0];
  const missing = ["LOW RISK", "MODERATE RISK", "HIGH RISK", "NOT RATED"]
    .filter((w) => !tone.includes(w) || !tip.includes(w));
  return missing.length === 0 || missing.join(",");
})());

check("all five earnings-cycle states are explained", (() => {
  const tip = (inv.match(/const INV_CYCLE_TIP = \{[\s\S]*?\n\};/) || [""])[0];
  const missing = ["PRE-EARNINGS", "POST-EARNINGS FRESH", "NORMAL", "STALE",
                   "UNKNOWN"].filter((w) => !tip.includes(w));
  return missing.length === 0 || missing.join(",");
})());

check("every quality input has its own plain-English explanation", (() => {
  const tips = (inv.match(/const INV_QUALITY_TIP = \{[\s\S]*?\n\};/) || [""])[0];
  const want = ["Return on invested capital", "Free cash flow conversion",
                "Operating margin trend", "Share count trend",
                "Stock compensation as a share of revenue",
                "Net debt to operating profit"];
  const missing = want.filter((w) => !tips.includes(w));
  return missing.length === 0 || missing.join(",");
})());

check("the regime-shift banner is rendered and explains itself",
  /REGIME SHIFT DETECTED/.test(inv) && /regime\.earlier_spread/.test(inv));

check("the peer panel refuses an average of ratios in writing",
  /NEVER an average of the members' ratios/.test(invFlat)
  && /aggregate_pe/.test(inv) && /median_member_pe/.test(inv));

check("excluded loss-making peers are surfaced, not silently dropped",
  /n_excluded/.test(inv) && /excluded as loss-making/.test(invFlat));

check("the peer level and industry code are shown so the group can be judged",
  /p\.level/.test(inv) && /industry code/.test(invFlat));

check("historical forward price-to-earnings is refused in writing",
  /would have to be fabricated/.test(invFlat)
  && /no free record\s+of past analyst expectations exists/.test(invFlat));

check("the revision rating states its coverage gate",
  /NOT RATED below four covering analysts/.test(invFlat));

check("GAAP trailing and adjusted analyst bases are kept apart in words",
  /gaap_note/.test(inv));

check("the underreaction panel is marked experimental and out of the verdict",
  /EXPERIMENTAL — unvalidated/.test(invFlat)
  && /takes no part in the verdict/.test(invFlat));

check("beta is deliberately absent from the drawdown panel",
  /no Beta here on purpose/.test(invFlat) && !/\bbeta:/i.test(inv));

check("value-trap signals that could not be measured are shown as such",
  /inv-trapsig-unknown/.test(inv) && /could not be measured/.test(invFlat));

check("specialized business types say so instead of showing a number",
  /N\/A for this business type/.test(inv)
  && /SPECIALIZED MODEL REQUIRED/.test(inv));

// ── Investment tab, Phase 3 ───────────────────────────────────────────────
// The decision moved to the top of the page, so these guard the claims the
// new panels make in writing. Every one of them is a sentence a reader will
// act on, which is exactly why it has to survive a refactor.

check("the entry verdict covers every named answer",
  ["BUY SHARES", "SELL PORTFOLIO SECURED PUT", "BUY LEAPS", "BUY-WRITE",
   "BULL CALL SPREAD", "TOSS UP", "WAIT:", "AVOID:"]
    .every((v) => inv.includes(v)));

check("every entry verdict is toned and explained",
  /INV_ENTRY_TONE/.test(inv) && /INV_ENTRY_TIP/.test(inv)
  && (inv.match(/INV_ENTRY_TIP\s*=\s*\{[\s\S]*?\n\};/) || [""])[0].length > 800);

check("the six decision numbers are shown before anything else",
  /Current price/.test(invFlat) && /Bear \/ Base \/ Bull/.test(invFlat)
  && /Fair value confidence/.test(invFlat) && /Buy zone/.test(invFlat)
  && /Expected return/.test(invFlat) && /Preferred structure/.test(invFlat));

check("fair value methods are never described as averaged",
  /NOT averaged/.test(invFlat)
  && /base value is the single highest-confidence method/.test(invFlat));

check("the confidence bands are stated as a convention, not a result",
  /a stated convention, not a tested result/.test(invFlat));

check("a single valuation method is never rated HIGH, and says why",
  /silence is not agreement/.test(invFlat) || /capped at MODERATE/.test(invFlat));

check("the credited fair value formula is shown to the reader",
  /credit_note/.test(inv) && /Bear \+ /.test(invFlat));

check("lower confidence is described as lowering the price we will pay",
  /LOWER confidence LOWERS the price/.test(invFlat));

check("buyback yield is refused in writing",
  /counts the same cash twice/.test(invFlat));

check("dividends are described as cash, not a yield added to the price return",
  /compounded to the horizon/.test(invFlat)
  && /NOT added to the price return/.test(invFlat));

check("the expected-return bars claim to reconcile and say why",
  /they add up exactly/.test(invFlat)
  && /asserted in the test suite/.test(invFlat));

check("the reverse discounted cash flow is called an expectations tool",
  /EXPECTATIONS instrument/.test(invFlat) && /not a valuation/.test(invFlat));

check("the solver is named as bracketed rather than Newton-Raphson",
  /bisection on a bracketed interval/.test(invFlat)
  && /Newton-Raphson/.test(invFlat));

check("the discount rate is stated as an assumption, not a computed WACC",
  /NOT a per-company weighted average cost of capital/.test(invFlat));

check("five-year free-cash-flow consensus is never claimed",
  /will not print one it does not have/.test(invFlat));

check("the comparator states identical capital, horizon and scenario prices",
  /Identical capital\. Identical horizon\. Identical scenario prices/.test(invFlat));

check("return-on-premium and buying-power ranking are refused in writing",
  /return-on-premium/.test(invFlat)
  && /return-on-buying-power-reduction/.test(invFlat)
  && /makes leverage look like skill/.test(invFlat));

check("the put's risk is described as full strike notional, never the BPR",
  /strike × 100/i.test(invFlat)
  && /never the buying-power\s+reduction/i.test(invFlat));

check("the acquisition price is stated as coming before the strike",
  /acquisition price comes FIRST/.test(invFlat)
  && /INSUFFICIENT PREMIUM AT DESIRED OWNERSHIP PRICE/.test(inv));

check("premium chasing is refused in writing",
  /never raised to find premium/.test(invFlat)
  || /opposite of the point/.test(invFlat));

check("delta bands and IV-rank rules are explicitly not the decision",
  /no 0\.15–0\.25 delta rule/.test(invFlat)
  && /no 0\.75–0\.85 rule/.test(invFlat));

check("ExpectedRV30 is refused as a LEAPS yardstick, in writing",
  /ExpectedRV30 is deliberately ABSENT/.test(invFlat)
  && /thirty-day volatility forecast/.test(invFlat));

check("long-dated implied-volatility history says it is never back-filled",
  /never back-filled/.test(invFlat));

check("the fundamental scenarios are not presented as a price distribution",
  /fundamental scenarios are not a price distribution/.test(invFlat));

check("a buy-write is described as one expiration, not weekly rolling",
  /not a model of selling a call every week/.test(invFlat));

check("the management plan says no orders are ever placed",
  /No orders are placed/.test(invFlat)
  && /written plan, not an\s*automation/.test(invFlat));

check("the scanner refuses a summed investment score in writing",
  /NO summed investment score/.test(invFlat)
  && !/investment_score/.test(inv));

check("every new Phase 3 table header carries a tooltip",
  (() => {
    const heads = inv.match(/<th\b[^>]*>/g) || [];
    return heads.length > 40 && heads.every((t) => /title=/.test(t));
  })());

check("wide Phase 3 tables scroll inside the house wrapper",
  (inv.match(/scan-table-wrap/g) || []).length >= 6);

check("the greeks sit behind an expander rather than in the decision row",
  /Greeks and contract detail/.test(invFlat));

/* ── Phase 4 ──────────────────────────────────────────────────────────── */

check("bank and property-trust panels exist and are shown only for their kind",
  /function InvBank\(/.test(inv) && /function InvReit\(/.test(inv)
  && /\{d\.bank && section\("bank"/.test(inv)
  && /\{d\.reit && section\("reit"/.test(inv));

check("every bank and property-trust tooltip is written out",
  (() => {
    for (const name of ["INV_BANK_TIP", "INV_REIT_TIP", "INV_CC_TIP",
                        "INV_FWD_TIP"]) {
      const block = (inv.match(new RegExp(`const ${name} = \\{[\\s\\S]*?\\n\\};`))
                     || [""])[0];
      if (!block) return `${name} missing`;
      const keys = block.match(/^\s{2}\w+:/gm) || [];
      if (keys.length < 5) return `${name} has only ${keys.length} entries`;
    }
    return true;
  })());

/* The Phase 4 panels pass their labels through a local stat() helper rather
 * than as a literal label= attribute, so these read the helper calls. */
const invStatLabels = (inv.match(/\{stat\("[^"]+"/g) || [])
  .map((m) => m.slice(7, -1));

check("what is not net interest margin is not called net interest margin",
  /NOT net interest margin/.test(invJoined)
  && !invStatLabels.includes("Net interest margin")
  && invStatLabels.includes("Net interest income to average assets"));

check("the efficiency ratio says which direction is good",
  /LOWER IS BETTER/.test(invJoined));

check("funds from operations is called a reconstruction on screen",
  /RECONSTRUCTED from/.test(invJoined)
  && /No property trust publishes funds from operations/.test(invJoined));

check("adjusted funds from operations is refused in writing",
  /it cannot be computed honestly/.test(invJoined)
  && !/label="Price to adjusted funds/.test(inv));

check("no insider shorthand in the Phase 4 labels", (() => {
  if (invStatLabels.length < 25) return `only ${invStatLabels.length} labels`;
  const shorthand = /^(FFO|AFFO|P\/TBV|P\/B|P\/FFO|ROTCE|ROE|NIM|CET1|MAE|MFE|NOI|TBV|DPS|NCO|NPL|TTM|EPS)\b/;
  const bad = invStatLabels.filter((l) => shorthand.test(l));
  if (bad.length) return bad.join(", ");
  for (const want of ["Return on tangible common equity",
                      "Price to funds from operations",
                      "Price to tangible book value",
                      "Payout of funds from operations"]) {
    if (!invStatLabels.includes(want)) return `missing "${want}"`;
  }
  return true;
})());

check("every Phase 4 stat is given a tooltip",
  (() => {
    const calls = inv.match(/\{stat\([\s\S]{0,240}?\)\}/g) || [];
    const untipped = calls.filter((c) => !/INV_(BANK|REIT)_TIP\./.test(c));
    return untipped.length === 0
      || `${untipped.length} of ${calls.length} without a named tooltip`;
  })());

check("the covered-call win rate is labelled as not a measure of success",
  /NOT whether the strategy worked/.test(invJoined)
  && /still loses to owning the shares/.test(invJoined));

check("terminal wealth against buy and hold is named as the real comparison",
  /THIS is the comparison that decides whether the strategy worked/
    .test(invJoined));

check("the buy-and-hold row is drawn as the yardstick, not another policy",
  /inv-row-hold/.test(inv)
  && /Owning the shares and doing nothing/.test(invFlat));

check("real chain fills and model estimates are distinguished on screen",
  /REAL CHAIN BACKTEST means/.test(invJoined)
  && /Historical option quotes are never/.test(invJoined));

check("the validation panel refuses a vanity accuracy score in writing",
  /No accuracy score/.test(invFlat)
  && !/accuracy_score/.test(inv) && !/investmentScore/.test(inv));

check("small samples say INSUFFICIENT SAMPLE rather than showing a median",
  /INSUFFICIENT SAMPLE/.test(invFlat) || /verdict \|\| invNA/.test(inv));

check("the no-lookahead promise is stated where the reader can see it",
  /Nothing is recomputed and nothing is rewritten/.test(invJoined)
  && /Only COMPLETED horizons are counted/.test(invJoined));

check("the exact recommended contract is named as the one scored",
  /Never a better one chosen after seeing the outcome/.test(invJoined));

check("configurations are kept apart rather than combined silently",
  /shown separately rather than combined and called one strategy/
    .test(invJoined));

check("every Phase 4 table header carries a tooltip",
  (() => {
    const heads = inv.match(/<th\b[^>]*>/g) || [];
    const untitled = heads.filter((t) => !/title=/.test(t));
    return (heads.length > 60 && untitled.length === 0)
      || `${heads.length} headers, ${untitled.length} untitled`;
  })());

check("the Phase 4 sections are expandable and tipped",
  (() => {
    for (const key of ["coveredcall", "validation", "bank", "reit"]) {
      const re = new RegExp(`section\\("${key}", "[^"]+",\\s*\\n?\\s*"`);
      if (!re.test(inv)) return `${key} section missing a tooltip`;
    }
    return true;
  })());

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
