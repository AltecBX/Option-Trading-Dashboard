// journal.js (v1.21) — pure helpers over the closed-trade journal.
// One source of truth for the realized P/L math so the Win Rate tiles,
// the CSV export, and the cumulative P/L chart can never disagree.
// No DOM, no network. Dual exported for node tests (test_journal.js)
// and the browser via window.JournalUtil.
//
// Journal entry shape (POSTed to /api/trade_journal in v1.15):
//   ticker, type ("call"|"put"|stock), entry_premium, closed_premium,
//   qty (negative = short / sold), opened_at, closed_at,
//   optional: strike, expiration, entry_delta.

(function () {
  // Realized P/L per trade, in dollars. Short (qty<0, sold premium)
  // profits when the close price is below entry. Mirrors the formula
  // that shipped inline in WinRateCard (v1.15) exactly.
  function tradePnl(t) {
    if (!t) return 0;
    var qtyAbs = Math.abs(Number(t.qty) || 0);
    var entry = Number(t.entry_premium) || 0;
    var close = Number(t.closed_premium) || 0;
    var isShort = (Number(t.qty) || 0) < 0;
    return isShort
      ? (entry - close) * 100 * qtyAbs
      : (close - entry) * 100 * qtyAbs;
  }

  // Gross premium collected on a short entry. Long entries collect none.
  function premiumCollected(t) {
    if (!t) return 0;
    var isShort = (Number(t.qty) || 0) < 0;
    if (!isShort) return 0;
    return (Number(t.entry_premium) || 0) * 100 * Math.abs(Number(t.qty) || 0);
  }

  // True only for option rows. Stock rows are excluded from win-rate and
  // the P/L chart so the metric reflects premium-selling specifically.
  function isOption(t) {
    return t && (t.type === "call" || t.type === "put");
  }

  // Escape one CSV field per RFC 4180: wrap in quotes and double any
  // embedded quote when the value contains a comma, quote, or newline.
  function csvField(v) {
    if (v === null || v === undefined) return "";
    var s = String(v);
    if (/[",\n\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  // Build a tax-prep CSV string from the full journal (all rows, not
  // just options). realized_pnl and premium_collected are filled for
  // option rows and left blank otherwise. Returns header-only when empty.
  function buildJournalCsv(trades) {
    var cols = ["ticker", "type", "strike", "expiration", "qty",
                "entry_premium", "closed_premium", "entry_delta",
                "opened_at", "closed_at", "realized_pnl", "premium_collected"];
    var lines = [cols.join(",")];
    var list = Array.isArray(trades) ? trades : [];
    for (var i = 0; i < list.length; i++) {
      var t = list[i];
      var pnl = isOption(t) ? tradePnl(t).toFixed(2) : "";
      var prem = isOption(t) ? premiumCollected(t).toFixed(2) : "";
      lines.push([
        csvField(t.ticker), csvField(t.type), csvField(t.strike),
        csvField(t.expiration), csvField(t.qty), csvField(t.entry_premium),
        csvField(t.closed_premium), csvField(t.entry_delta),
        csvField(t.opened_at), csvField(t.closed_at),
        csvField(pnl), csvField(prem),
      ].join(","));
    }
    return lines.join("\n");
  }

  // Cumulative realized P/L over time for the chart. Option rows only,
  // sorted ascending by closed_at, running sum carried forward. Returns
  // [{ t: closed_at, pnl, cum }]. Rows without a closed_at are dropped.
  function buildCumulativePnlSeries(trades) {
    var list = (Array.isArray(trades) ? trades : [])
      .filter(function (t) { return isOption(t) && t.closed_at; })
      .slice()
      .sort(function (a, b) {
        return String(a.closed_at).localeCompare(String(b.closed_at));
      });
    var cum = 0;
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var p = tradePnl(list[i]);
      cum += p;
      out.push({ t: list[i].closed_at, pnl: p, cum: cum });
    }
    return out;
  }

  var api = {
    tradePnl: tradePnl,
    premiumCollected: premiumCollected,
    isOption: isOption,
    csvField: csvField,
    buildJournalCsv: buildJournalCsv,
    buildCumulativePnlSeries: buildCumulativePnlSeries,
  };

  if (typeof window !== "undefined") { window.JournalUtil = api; }
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
})();
