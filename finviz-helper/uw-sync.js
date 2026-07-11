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
