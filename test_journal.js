// test_journal.js (v1.21) — verifies the journal P/L math, CSV export,
// and cumulative series. Pure node. Run:  node test_journal.js
const path = require("path");
const J = require(path.join(__dirname, "journal.js"));

let passed = 0, failed = 0; const fails = [];
function ok(name, cond) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name); }
}
function close(a, b) { return Math.abs(a - b) < 1e-6; }

// Reference: short call, sold at 1.50, bought back at 0.40, 2 contracts.
// (1.50 - 0.40) * 100 * 2 = 220 profit.
const shortWin = { ticker: "AAPL", type: "call", entry_premium: 1.50, closed_premium: 0.40, qty: -2, opened_at: "2026-05-01", closed_at: "2026-05-10", strike: 240, expiration: "2026-05-16", entry_delta: 0.21 };
ok("short win pnl", close(J.tradePnl(shortWin), 220));
ok("short win premium collected", close(J.premiumCollected(shortWin), 300));

// Short put that went against us: sold 1.00, closed 1.80, 1 contract = -80.
const shortLoss = { ticker: "TSLA", type: "put", entry_premium: 1.00, closed_premium: 1.80, qty: -1, opened_at: "2026-05-02", closed_at: "2026-05-12" };
ok("short loss pnl", close(J.tradePnl(shortLoss), -80));

// Long position: bought 2.00, sold 3.00, qty +1 = +100.
const longWin = { ticker: "NVDA", type: "call", entry_premium: 2.00, closed_premium: 3.00, qty: 1, opened_at: "2026-05-03", closed_at: "2026-05-11" };
ok("long win pnl", close(J.tradePnl(longWin), 100));
ok("long collects no premium", J.premiumCollected(longWin) === 0);

// Parity check against the inline formula that shipped in WinRateCard.
function inlineFormula(t) {
  const isShort = (t.qty || 0) < 0;
  const qtyAbs = Math.abs(t.qty || 0);
  return isShort
    ? (t.entry_premium - t.closed_premium) * 100 * qtyAbs
    : (t.closed_premium - t.entry_premium) * 100 * qtyAbs;
}
ok("parity with shipped formula (win)", close(J.tradePnl(shortWin), inlineFormula(shortWin)));
ok("parity with shipped formula (loss)", close(J.tradePnl(shortLoss), inlineFormula(shortLoss)));
ok("parity with shipped formula (long)", close(J.tradePnl(longWin), inlineFormula(longWin)));

ok("null trade pnl is 0", J.tradePnl(null) === 0);
ok("missing qty pnl is 0", J.tradePnl({ entry_premium: 1, closed_premium: 0 }) === 0);

// isOption gate
ok("call is option", J.isOption(shortWin) === true);
ok("stock is not option", J.isOption({ type: "stock" }) === false);

// CSV escaping
ok("plain field unquoted", J.csvField("AAPL") === "AAPL");
ok("comma field quoted", J.csvField("a,b") === '"a,b"');
ok("quote field escaped", J.csvField('he said "hi"') === '"he said ""hi"""');
ok("null field empty", J.csvField(null) === "");

// CSV build
const csv = J.buildJournalCsv([shortWin, shortLoss, longWin]);
const rows = csv.split("\n");
ok("csv has header + 3 rows", rows.length === 4);
ok("csv header first col", rows[0].split(",")[0] === "ticker");
ok("csv header has realized_pnl", rows[0].indexOf("realized_pnl") !== -1);
ok("csv short win pnl cell", rows[1].indexOf("220.00") !== -1);
ok("empty journal is header only", J.buildJournalCsv([]).split("\n").length === 1);
ok("stock row leaves pnl blank", (function () {
  const c = J.buildJournalCsv([{ ticker: "AAPL", type: "stock", qty: 100, entry_premium: 0, closed_premium: 0, opened_at: "x", closed_at: "y" }]);
  const cells = c.split("\n")[1].split(",");
  return cells[cells.length - 2] === "" && cells[cells.length - 1] === "";
})());

// Cumulative series: sorted by closed_at, running sum, options only.
const series = J.buildCumulativePnlSeries([longWin, shortWin, shortLoss, { type: "stock", qty: 10, closed_at: "2026-05-09" }]);
ok("series drops stock + sorts", series.length === 3 && series[0].t === "2026-05-10");
ok("series cumulative carries", close(series[series.length - 1].cum, 220 - 80 + 100));
ok("series first cum equals first pnl", close(series[0].cum, series[0].pnl));
ok("series drops rows without closed_at", J.buildCumulativePnlSeries([{ type: "call", entry_premium: 1, closed_premium: 0, qty: -1 }]).length === 0);

console.log("\n" + passed + "/" + (passed + failed) + " passed, " + failed + " failed");
if (failed) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
