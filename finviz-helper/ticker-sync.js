// Runs inside finviz.com pages ONLY when they are embedded in the JerryTrade
// dashboard (window.top !== window). When the page is a quote page — i.e.
// the user clicked a stock in the screener/maps/news — it reports the SYMBOL
// (and nothing else) up to the dashboard so the app's global ticker follows.
// The dashboard validates the message origin and symbol format on its side.
(function tickerSync() {
  if (window.top === window) return;   // normal Finviz tabs: do nothing
  try {
    const m = /[?&]t=([A-Za-z0-9.\-]{1,12})\b/.exec(location.search);
    if (m) {
      window.parent.postMessage(
        { type: "fvh-ticker", symbol: m[1].toUpperCase() }, "*");
    }
  } catch (e) { /* no-op */ }
})();
