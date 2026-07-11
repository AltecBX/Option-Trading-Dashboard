// Announces the helper's presence to the JerryTrade dashboard so it knows
// it can render the embedded Finviz frame. This script runs ONLY on the
// dashboard's own pages and touches nothing else.
(function announce() {
  try {
    document.documentElement.dataset.finvizHelper = "1";
    window.dispatchEvent(new CustomEvent("finviz-helper-ready"));
  } catch (e) { /* no-op */ }
  // Re-announce a few times in case the app mounts after us.
  let n = 0;
  const t = setInterval(() => {
    try {
      document.documentElement.dataset.finvizHelper = "1";
      window.dispatchEvent(new CustomEvent("finviz-helper-ready"));
    } catch (e) { /* no-op */ }
    if (++n >= 10) clearInterval(t);
  }, 1000);
})();
