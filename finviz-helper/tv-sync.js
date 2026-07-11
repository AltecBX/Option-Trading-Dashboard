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

// Storage Access (v2.5): in browsers that block third-party cookies with no
// extension-settable exception (Comet and friends), the standards path is
// for the EMBEDDED site itself to request cookie access after a user
// gesture. On the first click inside the frame we ask; the browser may show
// a one-time Allow prompt. Once granted (persisted ~30 days per site), the
// frame can use the real login cookies and we reload once to apply them.
// In Chrome (where the helper already registered an exception) access
// already exists, so this never fires.
(function storageAccess() {
  if (window.top === window) return;
  let settled = false;
  const tryGrant = () => {
    if (settled) return;
    try {
      if (!document.hasStorageAccess || !document.requestStorageAccess) { settled = true; return; }
      document.hasStorageAccess().then((has) => {
        if (has) { settled = true; return; }
        document.requestStorageAccess().then(() => {
          settled = true;
          location.reload();   // apply the now-accessible session cookies
        }).catch(() => { /* denied or gesture expired — retry on next click */ });
      }).catch(() => {});
    } catch (e) { /* no-op */ }
  };
  document.addEventListener("click", tryGrant, true);
})();
