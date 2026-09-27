// test_dailyedge_ui.js (v5.35) — source guards for DAILY EDGE.
//
//   1. Registered as a lazy chunk and mounted FIRST on the Trade tab.
//   2. Every column header carries a tooltip.
//   3. The row shows the bid beside the record's loss and says whether the
//      bid pays for it — never a signal without its price check.
//   4. The covered-call caveat and the paper record are on the card.
//
// Run from the repo dir:  node test_dailyedge_ui.js
const fs = require("fs");
const path = require("path");
let passed = 0, failed = 0;
function ok(name, cond) { if (cond) { passed++; console.log("  PASS  " + name); } else { failed++; console.log("  FAIL  " + name); } }
const read = (f) => fs.readFileSync(path.join(__dirname, f), "utf8");
const src = read("tab-dailyedge.jsx");
const app = read("app.jsx");

ok("chunk is built", /"tab-dailyedge\.jsx"/.test(read("build_frontend.js")) && /"tab-dailyedge\.js"/.test(read("build_frontend.js")));
ok("chunk is verified", /"tab-dailyedge\.js"/.test(read("verify_frontend.js")));
ok("chunk registers DailyEdgeCard", /Object\.assign\(window,\s*\{\s*DailyEdgeCard/.test(src));
ok("mounted on Trade", /chunk="tab-dailyedge" component="DailyEdgeCard"/.test(app));
const iEdge = app.indexOf('chunk="tab-dailyedge"'), iLine = app.indexOf('chunk="tab-stretch" component="StretchCard"');
ok("mounted above At the line", iEdge > 0 && iLine > 0 && iEdge < iLine);
const ths = src.match(/<th\b[^>]*>/g) || [];
ok("every header has a tooltip", ths.length >= 9 && ths.every((t) => /title=/.test(t)));
ok("bid beside the record's loss", /DE_TIP\.bid/.test(src) && /DE_TIP\.loss/.test(src) && /r\.hist_loss/.test(src));
ok("says whether the bid pays", /r\.pays/.test(src) && /r\.edge/.test(src));
ok("covered-call caveat", /100 shares/.test(src));
ok("paper record shown", /Paper record/.test(src) && /forward/.test(src));
ok("reads the daily-edge endpoint", /\/api\/daily-edge/.test(src));

console.log(`\n${passed}/${passed + failed} passed`);
process.exit(failed ? 1 : 0);
