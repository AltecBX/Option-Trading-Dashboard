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
