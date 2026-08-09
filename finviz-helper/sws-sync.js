// Runs inside simplywall.st pages ONLY when embedded in the JerryTrade
// dashboard. Simply Wall St stock URLs carry the listing in the third path
// segment as "{exchange}-{ticker}", e.g.
//   /stocks/us/semiconductors/nasdaq-nvda/nvidia
//   /stocks/us/tech/nasdaq-sndk/sandisk
// Poll the pathname and report the TICKER (and nothing else) up to the
// dashboard so the app's global ticker follows what you're viewing — the
// same two-way sync Finviz, TradingView and Unusual Whales already have.
(function swsSync() {
  if (window.top === window) return;   // normal simplywall.st tabs: do nothing
  let last = null;
  const report = () => {
    try {
      // /stocks/{country}/{sector}/{exchange}-{ticker}/{name}
      const m = /^\/stocks\/[a-z]{2}\/[^/]+\/([a-z]+)-([A-Za-z.\-]{1,10})(?:\/|$)/
        .exec(location.pathname);
      if (!m) return;
      const sym = m[2].toUpperCase();
      if (!/^[A-Z]{1,5}(\.[A-Z])?$/.test(sym)) return;
      if (sym === last) return;
      last = sym;
      window.parent.postMessage({ type: "jth-sws-ticker", symbol: sym }, "*");
    } catch (e) { /* no-op */ }
  };
  setInterval(report, 1500);
  window.addEventListener("load", report);
})();

// Frame reload channel — same contract as the other site scripts: the
// background worker asks affected frames to reload once after it installs
// the cookie-header fallback, so the session applies.
try {
  chrome.runtime.onMessage.addListener((msg) => {
    if (window.top === window) return;
    if (msg && msg.type === "jth-reload" && typeof msg.domain === "string"
        && (location.hostname === msg.domain || location.hostname.endsWith("." + msg.domain))) {
      location.reload();
    }
  });
} catch (e) { /* no-op */ }
