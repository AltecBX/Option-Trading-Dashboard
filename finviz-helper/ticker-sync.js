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

  // Theme toggle fix (v1.4): Finviz persists the theme via
  // /api/set_cookie, whose response cookie is SameSite=Lax — browsers
  // REJECT such cookies when they arrive inside a cross-site frame, so the
  // native toggle silently did nothing here. Intercept the click and write
  // the same chartsTheme cookie through the extension's cookies API (not
  // subject to that rejection), then reload — identical outcome to
  // pressing the toggle on finviz.com directly.
  try {
    document.addEventListener("click", (e) => {
      const t = e.target;
      const a = t && t.closest &&
        t.closest('[data-testid="chart-layout-theme"], a[onclick*="setChartThemeCookie"]');
      if (!a) return;
      e.preventDefault();
      const toValue = document.documentElement.classList.contains("dark") ? "light" : "dark";
      chrome.runtime.sendMessage({ type: "fvh-set-theme", value: toValue }, () => {
        void chrome.runtime.lastError;
        location.reload();
      });
    }, true);
  } catch (e) { /* no-op */ }
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
