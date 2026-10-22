// market_cap.js (v5.46) — the Trade chart's market-cap mode, as plain
// functions: no DOM, no network. node test_market_cap.js runs its tests.
//
// A market cap here is a day's price times the share count the company
// filed for that day (share_history.py): the last count reported on or
// before it, never an estimate. A day with no count (before the first one,
// or past a count too old to stand for it) has no market cap: the chart
// leaves a gap rather than inventing one.
//
// Before anything is multiplied, the chart's own prices are checked against
// Yahoo's unadjusted closes, one a month. Prices adjusted for dividends read
// low in the past, and so would every market cap drawn from them; prices on
// a different split basis than the counts would be off by the split. Either
// way the chart says so and draws the price instead.
(function () {
  "use strict";

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var CHECK_TOL_PCT = 1.0;     // closes this far apart disagree...
  var CHECK_TOL_ABS = 0.006;   // ...unless it is only the rounding of a tiny price
  var CHECK_SKIP_DAYS = 7;     // the newest closes may still be revised: not checked

  function isNum(v) { return typeof v === "number" && isFinite(v); }

  // A bar's calendar day, the same way the chart puts it on its time axis.
  function dayKey(d) {
    if (d == null) return "";
    var m = /^(\d{4}-\d{2}-\d{2})/.exec(typeof d === "string" ? d : "");
    if (m) return m[1];
    var t = new Date(d);
    return isNaN(+t) ? "" : t.toISOString().slice(0, 10);
  }

  function daysBetween(a, b) {
    return Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / 86400000);
  }

  // "Jul 24, 2009" from 2009-07-24, without a time zone getting a say.
  function fmtDay(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
    if (!m) return String(iso || "");
    return MONTHS[+m[2] - 1] + " " + (+m[3]) + ", " + m[1];
  }

  // $2.95T · $820.4B · $95.12B · $2.01B · $950.2M
  function fmtCap(v) {
    if (!isNum(v)) return "—";
    var a = Math.abs(v), sign = v < 0 ? "-" : "";
    var u = a >= 1e12 ? [1e12, "T"] : a >= 1e9 ? [1e9, "B"] : a >= 1e6 ? [1e6, "M"] : [1e3, "K"];
    var x = a / u[0];
    return sign + "$" + x.toFixed(x >= 100 ? 1 : 2) + u[1];
  }

  // 24.53B · 950.2M · 12,345
  function fmtShares(v) {
    if (!isNum(v)) return "—";
    if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
    return Math.round(v).toLocaleString("en-US");
  }

  function fmtPrice(v) {
    if (!isNum(v)) return "—";
    return "$" + v.toFixed(Math.abs(v) < 1 ? 4 : 2);
  }

  // The count `day`'s price is multiplied by: the last one reported on or
  // before it. null before the first count, and when the last one is older
  // than the history's max_age_days (a missed filing is a gap, not a guess).
  function sharesOn(hist, day) {
    var pts = (hist && hist.points) || [];
    var lo = 0, hi = pts.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (pts[mid].as_of <= day) lo = mid + 1; else hi = mid;
    }
    if (!lo) return null;
    var p = pts[lo - 1];
    var age = daysBetween(p.as_of, day);
    if (age > ((hist && hist.max_age_days) || 200)) return null;
    return { shares: p.shares, as_of: p.as_of, age_days: age, form: p.form || null, filed: p.filed || null };
  }

  // Daily bars as market cap: the same bars on the same days, each price
  // times that day's count. The bar's own prices stay in `px` and its count
  // in `cap`. A bar with no count keeps its day and volume and loses its
  // prices, so the time axis is the price chart's and zoom carries over.
  // `cap.starts` marks the bar a count begins on, worked out here on the
  // whole series because the chart is handed a slice of it (Codex, #431):
  // the first bar of the series only when the count is dated that day.
  function capBars(bars, hist) {
    var prevAsOf, first = true;
    return (bars || []).map(function (b) {
      if (!b) return b;
      var day = dayKey(b.date);
      var s = sharesOn(hist, day);
      if (s) s.starts = first ? s.as_of === day : s.as_of !== prevAsOf;
      prevAsOf = s ? s.as_of : null;
      first = false;
      var out = Object.assign({}, b, {
        px: { open: b.open, high: b.high, low: b.low, close: b.close },
        cap: s,
      });
      ["open", "high", "low", "close"].forEach(function (k) {
        out[k] = s && isNum(b[k]) ? b[k] * s.shares : null;
      });
      return out;
    });
  }

  // The chart's closes against the reference closes on the days both have.
  // "ok" when they agree; "mismatch" when more than a tenth of the days
  // disagree (one bad print in ten years is not a different basis);
  // "unchecked" when no day could be compared.
  function checkPrices(bars, priceCheck) {
    var closes = (priceCheck && priceCheck.closes) || [];
    var byDay = {}, newest = "";
    (bars || []).forEach(function (b) {
      if (!b) return;
      var k = dayKey(b.date);
      if (k > newest) newest = k;
      if (!b.synthetic && isNum(b.close)) byDay[k] = b.close;   // today's live bar is not a close yet
    });
    var checked = 0, bad = [];
    closes.forEach(function (row) {
      var d = row[0], ref = row[1];
      if (!isNum(ref) || ref <= 0 || !(d in byDay)) return;
      if (newest && daysBetween(d, newest) < CHECK_SKIP_DAYS) return;
      checked++;
      var px = byDay[d], diff = px / ref - 1;
      if (Math.abs(diff) * 100 > CHECK_TOL_PCT && Math.abs(px - ref) > CHECK_TOL_ABS) {
        bad.push({ date: d, chart: px, ref: ref, diff_pct: diff * 100 });
      }
    });
    if (!checked) return { status: "unchecked", checked: 0, bad: 0, worst: null };
    var worst = null;
    bad.forEach(function (b) { if (!worst || Math.abs(b.diff_pct) > Math.abs(worst.diff_pct)) worst = b; });
    return { status: bad.length > checked * 0.1 ? "mismatch" : "ok", checked: checked, bad: bad.length, worst: worst };
  }

  // Everything the chart needs to draw a market cap for `bars`, or why it
  // draws the price instead. `draw` false comes with a `note` saying why.
  function capState(hist, bars, symbol) {
    var sym = symbol || (hist && hist.symbol) || "this stock";
    if (!hist) return { draw: false, note: "Loading " + sym + "'s share counts from its SEC filings… showing price meanwhile." };
    if (hist.status === "building") {
      return { draw: false, building: true,
               note: (hist.reason || "Reading " + sym + "'s SEC filings for its share counts.") + " Showing price meanwhile." };
    }
    if (hist.status === "unsupported") {
      return { draw: false, unsupported: true, note: "Market cap isn't available for " + sym + ". " + (hist.reason || "") + " Showing price." };
    }
    if (hist.status !== "ok") {
      return { draw: false, note: "Market cap couldn't load for " + sym + ": " + (hist.reason || hist.error || "no answer") + " Showing price." };
    }
    var check = checkPrices(bars, hist.price_check);
    if (check.status === "mismatch") {
      var w = check.worst;
      return { draw: false, check: check,
               note: "Market cap is off for " + sym + ": this chart's prices don't match Yahoo's unadjusted closes ("
                     + (w.diff_pct > 0 ? "+" : "") + w.diff_pct.toFixed(1) + "% on " + fmtDay(w.date) + ", "
                     + check.bad + " of " + check.checked + " months apart). They look adjusted for dividends, or for a split "
                     + "the share counts aren't on yet, and a market cap from them would be wrong. Showing price." };
    }
    var out = capBars(bars, hist);
    var firstIx = -1;
    for (var i = 0; i < out.length; i++) { if (out[i] && isNum(out[i].close)) { firstIx = i; break; } }
    var last = out.length ? out[out.length - 1] : null;
    var lastDay = last ? dayKey(last.date) : "";
    var latest = hist.latest || {};
    if (firstIx < 0) {
      var cov = hist.coverage || {};
      var why = bars && bars.length && cov.from && dayKey(bars[bars.length - 1].date) < cov.from
        ? "its SEC share counts start " + fmtDay(cov.from)
        : "its newest SEC share count (" + fmtDay(latest.as_of) + ") is too old to stand for these days";
      return { draw: false, check: check, note: "No market cap for " + sym + " in this range: " + why + ". Showing price." };
    }
    var notes = [];
    if (firstIx > 0) notes.push("Market cap starts " + fmtDay(out[firstIx].cap.as_of) + ", " + sym + "'s first SEC share count in this range; the days before it have none.");
    if (check.status === "unchecked") notes.push("This chart's prices could not be checked against Yahoo's unadjusted closes yet.");
    var now = lastDay ? sharesOn(hist, lastDay) : null;
    if (!now) notes.push("Strike and level lines are hidden: there is no current share count to convert them with.");
    return {
      draw: true, bars: out, check: check, notes: notes, warnings: (hist.warnings || []).slice(),
      levelShares: now ? now.shares : null, levelAsOf: now ? now.as_of : null,
      points: hist.points || [], source: hist.source || "", hist: hist,
    };
  }

  // The line under the chart's title in market-cap mode, for one bar.
  function readout(bar, hist) {
    if (!bar) return "";
    var day = fmtDay(dayKey(bar.date));
    if (bar.cap && isNum(bar.close) && bar.px && isNum(bar.px.close)) {
      var c = bar.cap;
      return day + " · Market cap " + fmtCap(bar.close) + " = " + fmtPrice(bar.px.close) + " × "
        + fmtShares(c.shares) + " shares · shares as of " + fmtDay(c.as_of) + (c.form ? " (" + c.form + ")" : "");
    }
    var cov = (hist && hist.coverage) || {};
    if (cov.from && dayKey(bar.date) < cov.from) return day + " · no market cap: before the first SEC share count (" + fmtDay(cov.from) + ")";
    return day + " · no market cap: the last share count before this day is over "
      + ((hist && hist.max_age_days) || 200) + " days old";
  }

  var api = {
    CHECK_TOL_PCT: CHECK_TOL_PCT, dayKey: dayKey, fmtDay: fmtDay, fmtCap: fmtCap, fmtShares: fmtShares,
    fmtPrice: fmtPrice, sharesOn: sharesOn, capBars: capBars, checkPrices: checkPrices, capState: capState,
    readout: readout,
  };
  if (typeof window !== "undefined") { window.MarketCap = api; }
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
})();
