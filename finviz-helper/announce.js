// Announces the helper's presence (and version) to the JerryTrade dashboard,
// and relays the background worker's diagnostic events into the page console
// as "[finviz-helper] …" debug lines (metadata only — never cookie values,
// passwords, or tokens). Runs ONLY on the dashboard's own pages.
(function announce() {
  const VERSION = "2.7";
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

  // Capability flags: whether the contentSettings exception is available
  // (Chrome), and whether the v2.7 cookie-header fallback is active for any
  // site (browsers seen dropping frame cookies — Comet, Brave, ...). The
  // fallback can arm mid-session (it's detected on the first frame load), so
  // re-query for a while instead of once.
  const caps = () => {
    try {
      chrome.runtime.sendMessage({ type: "jth-get-caps" }, (res) => {
        void chrome.runtime.lastError;
        try {
          document.documentElement.dataset.jthCookieApi =
            res && res.contentSettings ? "1" : "0";
          document.documentElement.dataset.jthCompat =
            res && res.inject && res.inject.length ? "1" : "0";
          window.dispatchEvent(new CustomEvent("finviz-helper-ready"));
        } catch (e) { /* no-op */ }
      });
    } catch (e) { /* no-op */ }
  };
  caps();
  let cn = 0;
  const ct = setInterval(() => { caps(); if (++cn >= 24) clearInterval(ct); }, 5000);

  // Command relay: dashboard page → background (e.g. Repair session).
  try {
    window.addEventListener("message", (e) => {
      if (e.source !== window || !e.data || e.data.type !== "jth-cmd") return;
      if (e.data.cmd === "clear-cookies" && typeof e.data.domain === "string") {
        chrome.runtime.sendMessage({ type: "jth-clear-cookies", domain: e.data.domain }, (res) => {
          void chrome.runtime.lastError;
          window.postMessage({ type: "jth-cmd-done", cmd: "clear-cookies",
                               domain: e.data.domain, res: res || null }, "*");
        });
      }
    });
  } catch (e) { /* no-op */ }

  // Diagnostic relay: background → page console.
  try {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.type === "fvh-diag" && msg.event) {
        console.debug("[finviz-helper]", msg.event.kind, msg.event);
      }
    });
  } catch (e) { /* no-op */ }
})();
