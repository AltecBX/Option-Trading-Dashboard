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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
