// Announces the helper's presence (and version) to the JerryTrade dashboard,
// and relays the background worker's diagnostic events into the page console
// as "[finviz-helper] …" debug lines (metadata only — never cookie values,
// passwords, or tokens). Runs ONLY on the dashboard's own pages.
(function announce() {
  const VERSION = "2.0";
  const mark = () => {
    try {
      document.documentElement.dataset.finvizHelper = "1";
      document.documentElement.dataset.finvizHelperVersion = VERSION;
      window.dispatchEvent(new CustomEvent("finviz-helper-ready"));
    } catch (e) { /* no-op */ }
  };
  mark();
  let n = 0;
  const t = setInterval(() => { mark(); if (++n >= 10) clearInterval(t); }, 1000);

  // Diagnostic relay: background → page console.
  try {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.type === "fvh-diag" && msg.event) {
        console.debug("[finviz-helper]", msg.event.kind, msg.event);
      }
    });
  } catch (e) { /* no-op */ }
})();
