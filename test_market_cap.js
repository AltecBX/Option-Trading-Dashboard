// test_market_cap.js (v5.46) — the Trade chart's market-cap mode. Pure
// node, no DOM, no network. Run from the active dir:  node test_market_cap.js
const path = require("path");
const M = require(path.join(__dirname, "market_cap.js"));

let passed = 0, failed = 0; const fails = [];
function ok(name, cond) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name); }
}
const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));

// A history shaped like /api/share_history: counts on today's split basis.
const HIST = {
  ok: true, status: "ok", symbol: "AAPL", max_age_days: 200,
  points: [
    { as_of: "2024-01-19", shares: 15.46e9, form: "10-Q", filed: "2024-02-02" },
    { as_of: "2024-04-19", shares: 15.33e9, form: "10-Q", filed: "2024-05-03" },
    { as_of: "2024-07-19", shares: 15.20e9, form: "10-Q", filed: "2024-08-02" },
  ],
  coverage: { from: "2024-01-19", to: "2024-07-19" },
  latest: { as_of: "2024-07-19", shares: 15.20e9 },
  warnings: ["Share classes counted together: Class A 1.00B."],
  price_check: { closes: [], source: "Yahoo" },
};
// The bars the app hands the chart: `date` a Date at noon New York time.
const bar = (day, close, extra) => Object.assign(
  { date: new Date(day + "T12:00:00-04:00"), open: close - 1, high: close + 2, low: close - 2, close, volume: 1000 }, extra || {});

// ── formatting ─────────────────────────────────────────────────────────
ok("trillions", M.fmtCap(2.954e12) === "$2.95T");
ok("hundreds of billions keep one decimal", M.fmtCap(820.44e9) === "$820.4B");
ok("tens of billions keep two", M.fmtCap(95.123e9) === "$95.12B");
ok("millions", M.fmtCap(950.2e6) === "$950.2M");
ok("a narrow axis gets a decimal more", M.fmtCap(4.2456e12, 1) === "$4.246T" && M.fmtCap(820.44e9, 1) === "$820.44B");
ok("no value is a dash", M.fmtCap(null) === "—" && M.fmtCap(NaN) === "—");
ok("shares in billions", M.fmtShares(24.534e9) === "24.53B");
ok("shares in millions", M.fmtShares(950.24e6) === "950.2M");
ok("a day without time zones", M.fmtDay("2009-07-24") === "Jul 24, 2009");
ok("a split-adjusted penny price keeps its digits", M.fmtPrice(0.10476) === "$0.1048");
ok("the chart's day for a noon-New-York bar", M.dayKey(new Date("2024-05-01T12:00:00-04:00")) === "2024-05-01");
ok("the chart's day for a string", M.dayKey("2024-05-01T12:00:00-04:00") === "2024-05-01");

// ── the count each day uses ────────────────────────────────────────────
ok("before the first count there is none", M.sharesOn(HIST, "2024-01-18") === null);
ok("on its own day", M.sharesOn(HIST, "2024-01-19").shares === 15.46e9);
ok("the last one reported on or before the day",
   M.sharesOn(HIST, "2024-07-18").shares === 15.33e9 && M.sharesOn(HIST, "2024-07-18").as_of === "2024-04-19");
ok("its age in days", M.sharesOn(HIST, "2024-07-29").age_days === 10);
ok("a count older than max_age_days stands for nothing", M.sharesOn(HIST, "2025-02-05") === null
   && M.sharesOn(HIST, "2025-02-04") !== null);
ok("no history, no count", M.sharesOn(null, "2024-05-01") === null);

// ── bars as market cap ─────────────────────────────────────────────────
const bars = [bar("2024-01-18", 190), bar("2024-01-19", 191), bar("2024-04-22", 165), bar("2024-07-22", 224)];
const cap = M.capBars(bars, HIST);
ok("same bars, same days", cap.length === 4 && cap.every((b, i) => b.date === bars[i].date));
ok("each price times that day's count", near(cap[1].close, 191 * 15.46e9) && near(cap[1].high, 193 * 15.46e9)
   && near(cap[2].open, 164 * 15.33e9) && near(cap[3].low, 222 * 15.20e9));
ok("the bar keeps its own prices and its count", cap[2].px.close === 165 && cap[2].cap.as_of === "2024-04-19");
ok("a day with no count keeps its day and volume, and loses its prices",
   cap[0].close === null && cap[0].open === null && cap[0].volume === 1000 && cap[0].cap === null);
ok("the bars handed in are not changed", bars[1].close === 191 && bars[1].cap === undefined);
// Codex, #431: the strip's dot marks the bar a count begins on, worked out
// on the whole series: the chart is handed a slice of it.
ok("each count begins on the first bar it covers",
   cap.map(b => b.cap ? b.cap.starts : null).join() === ",true,true,true");
const mid = M.capBars([bar("2024-02-01", 180), bar("2024-02-02", 181), bar("2024-04-19", 170)], HIST);
ok("a series that starts part-way through a count does not mark its first bar",
   mid[0].cap.starts === false && mid[1].cap.starts === false && mid[2].cap.starts === true);
ok("a series that starts on a count's own day does", M.capBars([bar("2024-01-19", 191)], HIST)[0].cap.starts === true);

// ── the price check ────────────────────────────────────────────────────
const year = [];
for (let m = 1; m <= 12; m++) year.push(`2024-${String(m).padStart(2, "0")}-02`);
const yearBars = year.map((d, i) => bar(d, 100 + i)).concat([bar("2024-12-20", 120)]);
const refsFor = (fn) => ({ closes: year.map((d, i) => [d, fn(100 + i, i)]) });
let c = M.checkPrices(yearBars, refsFor(px => px));
ok("the same closes agree", c.status === "ok" && c.checked === 12 && c.bad === 0);
c = M.checkPrices(yearBars, refsFor(px => px * 1.004));
ok("closes a cent or two apart agree", c.status === "ok");
// Dividend-adjusted prices read low, and lower the further back they go.
c = M.checkPrices(yearBars, refsFor((px, i) => px / (1 - 0.003 * (12 - i))));
ok("dividend-adjusted prices are caught", c.status === "mismatch");
ok("with the day they are furthest apart", c.worst.date === "2024-01-02" && c.worst.diff_pct < -3);
c = M.checkPrices(yearBars, refsFor((px, i) => i === 4 ? px * 1.5 : px));
ok("one bad print in a dozen months is not a different basis", c.status === "ok" && c.bad === 1);
c = M.checkPrices(yearBars.slice(0, 3).concat([bar("2024-04-10", 104)]), refsFor((px, i) => i === 1 ? px * 1.5 : px));
ok("but one in three is", c.status === "mismatch");
// A ten-for-one split the share counts are not on yet.
c = M.checkPrices(yearBars, refsFor(px => px * 10));
ok("prices on another split basis are caught", c.status === "mismatch");
const penny = [bar("2012-03-01", 0.30), bar("2012-04-02", 0.31), bar("2012-09-04", 0.5)];
c = M.checkPrices(penny, { closes: [["2012-03-01", 0.3049], ["2012-04-02", 0.3051]] });
ok("a penny price rounded to the cent agrees", c.status === "ok" && c.checked === 2);
c = M.checkPrices(yearBars.concat([bar("2024-12-23", 121)]), { closes: [["2024-12-20", 50]] });
ok("the newest week is not checked (Yahoo's close for today may still be moving)", c.status === "unchecked");
c = M.checkPrices(yearBars.concat([bar("2024-12-30", 121)]), { closes: [["2024-12-20", 50]] });
ok("a close ten days old is", c.status === "mismatch" && c.checked === 1);
ok("today's live bar is never checked",
   M.checkPrices([bar("2024-05-01", 100), bar("2024-05-20", 1, { synthetic: true })], { closes: [["2024-05-01", 100]] }).checked === 1
   && M.checkPrices([bar("2024-05-01", 100, { synthetic: true }), bar("2024-05-20", 100)], { closes: [["2024-05-01", 50]] }).status === "unchecked");
ok("no closes to compare is unchecked, not ok", M.checkPrices(yearBars, null).status === "unchecked");

// ── what the chart draws ───────────────────────────────────────────────
let s = M.capState(null, bars, "AAPL");
ok("loading draws the price and says so", !s.draw && /Loading AAPL/.test(s.note) && /showing price/i.test(s.note));
s = M.capState({ status: "building", reason: "Reading this company's SEC filings for its share counts; this takes up to a minute the first time." }, bars, "META");
ok("building says why and keeps the price", !s.draw && s.building && /up to a minute/.test(s.note));
s = M.capState({ status: "unsupported", reason: "No SEC share counts for this symbol: ETFs, funds and indexes file none." }, bars, "SPY");
ok("unsupported gives the reason", !s.draw && s.unsupported && /isn't available for SPY/.test(s.note) && /ETFs/.test(s.note));
s = M.capState({ status: "unavailable", reason: "SEC could not be reached just now." }, bars, "AAPL");
ok("unavailable gives the reason", !s.draw && /couldn't load/.test(s.note) && /SEC could not/.test(s.note));
const histY = Object.assign({}, HIST, { points: [{ as_of: "2023-12-01", shares: 1e9, form: "10-K" }], coverage: { from: "2023-12-01", to: "2023-12-01" } });
s = M.capState(Object.assign({}, histY, { price_check: refsFor((px, i) => px / (1 - 0.003 * (12 - i))) }), yearBars, "KO");
ok("a failed price check draws the price and says why", !s.draw && /off for KO/.test(s.note)
   && /dividends/.test(s.note) && /Jan 2, 2024/.test(s.note));
s = M.capState(Object.assign({}, histY, { price_check: refsFor(px => px) }), yearBars, "KO");
ok("a passed check draws the market cap", s.draw && s.check.status === "ok" && s.bars.length === yearBars.length);
ok("its lines are converted with today's count", s.levelShares === null || typeof s.levelShares === "number");
s = M.capState(HIST, bars, "AAPL");
ok("the days before the first count are said", s.draw && s.notes.some(n => /starts Jan 19, 2024/.test(n)));
ok("an unchecked basis is said", s.notes.some(n => /could not be checked/.test(n)));
ok("the history's own warnings come along", s.warnings.length === 1 && /Share classes/.test(s.warnings[0]));
ok("the lines use the count that applies to the newest bar", s.levelShares === 15.20e9 && s.levelAsOf === "2024-07-19");
s = M.capState(HIST, bars.concat([bar("2025-03-03", 230)]), "AAPL");
ok("no current count hides the lines and says so", s.draw && s.levelShares === null
   && s.notes.some(n => /lines are hidden/.test(n)));
s = M.capState(HIST, [bar("2023-05-01", 170), bar("2023-06-01", 180)], "AAPL");
ok("a range with no counts at all draws the price", !s.draw && /share counts start Jan 19, 2024/.test(s.note));

// ── a single price on a given day (v5.47: swing ends, levels) ───────────
ok("a price on a day is that price times that day's count", near(M.capAt(HIST, "2024-04-22", 165), 165 * 15.33e9));
ok("no count that day, no market cap", M.capAt(HIST, "2024-01-02", 165) === null);
ok("no price, no market cap", M.capAt(HIST, "2024-04-22", null) === null);

// ── the readout ────────────────────────────────────────────────────────
ok("the readout shows the sum", M.readout(cap[2], HIST)
   === "Apr 22, 2024 · Market cap $2.53T = $165.00 × 15.33B shares · shares as of Apr 19, 2024 (10-Q)");
ok("before the first count", /before the first SEC share count \(Jan 19, 2024\)/.test(M.readout(cap[0], HIST)));
ok("past a stale count", /over 200 days old/.test(M.readout(M.capBars([bar("2025-03-03", 1)], HIST)[0], HIST)));

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) { console.log("FAILED:\n  " + fails.join("\n  ")); process.exit(1); }
