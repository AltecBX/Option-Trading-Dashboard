// Runs inside tradingview.com pages ONLY when embedded in the JerryTrade
// dashboard. TradingView is a SPA whose URL doesn't change on symbol switch,
// but its document TITLE always leads with the active symbol (e.g.
// "AMD 559.77 ▲ +0.34% …"). Poll the title cheaply and report the SYMBOL
// (and nothing else) up to the dashboard. Only plain US-equity-style
// symbols are reported — futures/crypto/forex are ignored so the app's
// ticker never lands on something its data feeds can't research.
(function tvSync() {
  if (window.top === window) return;   // normal TradingView tabs: do nothing
  let last = null;
  const report = () => {
    try {
      let t = document.title || "";
      t = t.replace(/^\(\d+\)\s*/, "");            // "(2) AMD …" alert counters
      const m = /^([A-Z0-9.\-:!]{1,15})\s/.exec(t);
      if (!m) return;
      let sym = m[1];
      if (sym.indexOf(":") !== -1) sym = sym.split(":").pop();  // "NASDAQ:AMD"
      if (!/^[A-Z]{1,5}(\.[A-Z])?$/.test(sym)) return;          // equities only
      if (sym === last) return;
      last = sym;
      window.parent.postMessage({ type: "jth-tv-ticker", symbol: sym }, "*");
    } catch (e) { /* no-op */ }
  };
  setInterval(report, 2000);
  window.addEventListener("load", report);
})();

// Frame reload channel (v2.7): when the background worker detects that this
// browser dropped cookies on the frame request (Comet/Brave/forks) and
// installs the cookie-header fallback, it asks the affected frames to reload
// once so the login applies. The v2.5 Storage Access click-handler is gone —
// it could not help (these browsers send the frame's page request cookieless
// regardless of any grant) and its grant-then-reload cycle disturbed
// browsers that were already working.
try {
  chrome.runtime.onMessage.addListener((msg) => {
    if (window.top === window) return;
    if (msg && msg.type === "jth-reload" && typeof msg.domain === "string"
        && (location.hostname === msg.domain || location.hostname.endsWith("." + msg.domain))) {
      location.reload();
    }
  });
} catch (e) { /* no-op */ }
