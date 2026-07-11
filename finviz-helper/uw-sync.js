// Runs inside unusualwhales.com pages ONLY when embedded in the JerryTrade
// dashboard. UW's URLs carry the ticker (/stock/SYMBOL/...): poll the
// pathname and report the SYMBOL (and nothing else) up to the dashboard so
// the app's global ticker follows what you're viewing.
(function uwSync() {
  if (window.top === window) return;   // normal UW tabs: do nothing
  let last = null;
  const report = () => {
    try {
      const m = /^\/stock\/([A-Za-z.\-]{1,10})(?:\/|$)/.exec(location.pathname);
      if (!m) return;
      const sym = m[1].toUpperCase();
      if (!/^[A-Z]{1,5}(\.[A-Z])?$/.test(sym)) return;
      if (sym === last) return;
      last = sym;
      window.parent.postMessage({ type: "jth-uw-ticker", symbol: sym }, "*");
    } catch (e) { /* no-op */ }
  };
  setInterval(report, 1500);
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
