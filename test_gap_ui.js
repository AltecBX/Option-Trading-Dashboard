/* test_gap_ui.js — static guards on the Gap Scan tab's readability rules.
 *
 * Jerry reads this tab at 7am and should never have to look up what a column
 * means. These parse tab-gap.jsx directly (no browser needed) and fail the
 * build if a header ships without a tooltip, a date renders without its
 * weekday, or shorthand leaks back into the UI.
 */
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "tab-gap.jsx"), "utf8");
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
  /className=\{e\.direction === "up" \? "up" : "down"\}/.test(src));
check("direction cell is labeled, not just an arrow",
  /▲ Up/.test(src) && /▼ Down/.test(src));

// 7. Analog table is sortable and the news feed exists.
check("analog table is sortable", /function GapAnalogTable/.test(src)
  && /setK\(key_\)/.test(src));
check("news feed present", /function GapNews/.test(src)
  && /\/api\/news\?symbol=/.test(src));
check("days-to-earnings surfaced in current setup",
  /days_to_earnings/.test(src) && /Next earnings/.test(src));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
