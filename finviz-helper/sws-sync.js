// Runs inside simplywall.st pages ONLY when embedded in the JerryTrade
// dashboard. Three jobs, all confined to this frame:
//   1. report the ticker (and its real path) up to the dashboard
//   2. ask for Storage Access so a login survives inside the frame  (v3.1)
//   3. answer the dashboard's "diagnose login" request               (v3.1)
// ── Top-level snapshot (v3.2) ──────────────────────────────────────────────
// In a NORMAL simplywall.st tab (not framed) record the NAMES of the keys and
// cookies that tab can see. The framed copy has no way to know what it is
// missing without this: comparing the two is what distinguishes "the frame's
// cookies are blocked" (fixable) from "the auth token lives in localStorage,
// which Chrome partitions per top-level site" (not fixable by an extension).
// Names only — never values, never page content — kept in extension storage
// on this machine and never sent anywhere.
(function swsTopSnapshot() {
  if (window.top !== window) return;              // only real tabs
  const snap = () => {
    try {
      const keys = (st) => { try { return Object.keys(st).slice(0, 80); } catch (e) { return []; } };
      chrome.storage.local.set({ swsTopSnapshot: {
        at: new Date().toISOString(),
        href: location.origin + location.pathname,
        cookies: document.cookie.split(";").map((s) => s.trim().split("=")[0]).filter(Boolean).slice(0, 80),
        localStorage: keys(localStorage),
        sessionStorage: keys(sessionStorage),
      } });
    } catch (e) { /* no-op */ }
  };
  // Fire on several triggers, not just load: the tab may already have been
  // open before this version was installed, the user may sign in minutes
  // later, and an auth token can be written well after load. Re-snapshotting
  // is cheap (a few key names) and always overwrites with the latest view.
  window.addEventListener("load", () => setTimeout(snap, 2500));
  setTimeout(snap, 3000);
  setTimeout(snap, 8000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) snap(); });
  window.addEventListener("focus", snap);
  window.addEventListener("pagehide", snap);       // capture the final state
  setInterval(() => { if (!document.hidden) snap(); }, 30000);
})();

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
      // Report the PATH too (v2.9). Only the "{exchange}-{ticker}" segment is
      // derivable from a symbol — Simply Wall St's sector and company slugs
      // are theirs, and their pages cannot be fetched server-side to confirm
      // a guess. Reporting the real path lets the dashboard LEARN the exact
      // URL for every company you actually open, so a wrong guess is
      // self-correcting. Path only: no page content is ever read.
      window.parent.postMessage(
        { type: "jth-sws-ticker", symbol: sym, path: location.pathname }, "*");
    } catch (e) { /* no-op */ }
  };
  setInterval(report, 1500);
  window.addEventListener("load", report);

  // ── Storage Access (v3.1) ────────────────────────────────────────────────
  // Chrome blocks third-party cookies by default, so SameSite=None alone is
  // not enough: the frame is handed a partitioned jar and a login vanishes on
  // the next request. requestStorageAccess() is the standards-blessed way for
  // embedded content to ask for its OWN first-party cookies.
  //
  // v2.5 shipped this app-wide and it was removed in v2.7 because it reloaded
  // the frame on EVERY click, losing unsaved TradingView work. This version
  // avoids that failure mode completely:
  //   - simplywall.st only (TradingView is untouched)
  //   - at most ONE request per tab, recorded before the async call resolves
  //   - at most ONE reload, ever, guarded in sessionStorage
  //   - no reload at all if access was already granted
  let asked = false;
  const askForStorage = () => {
    if (asked) return;
    asked = true;                       // set FIRST: no double-fire on rapid clicks
    try {
      if (!document.hasStorageAccess || !document.requestStorageAccess) return;
      document.hasStorageAccess().then((has) => {
        if (has) return;                // already usable — never reload
        return document.requestStorageAccess().then(() => {
          // Granted. Reload once so the session applies to this document.
          let already = null;
          try { already = sessionStorage.getItem("jth-sa-reloaded"); } catch (e) {}
          if (already) return;
          try { sessionStorage.setItem("jth-sa-reloaded", "1"); } catch (e) {}
          location.reload();
        }, () => { /* denied or dismissed: leave the frame exactly as it was */ });
      }).catch(() => {});
    } catch (e) { /* no-op */ }
  };
  // A user gesture is required, so hang it off the first interaction.
  window.addEventListener("click", askForStorage, { once: true, capture: true });
  window.addEventListener("keydown", askForStorage, { once: true, capture: true });

  // ── Diagnose-login channel (v3.1) ────────────────────────────────────────
  // The dashboard cannot read anything from this cross-origin frame, which
  // left the user hand-running console snippets to work out why a login was
  // not sticking. This answers a single explicit request with the SHAPE of
  // storage — key and cookie NAMES only, never values, never page content.
  window.addEventListener("message", (e) => {
    try {
      if (!e.data || e.data.type !== "jth-sws-diag-req") return;
      if (!/^https?:\/\/(dashboard\.jerrytrade\.com|localhost(:\d+)?|127\.0\.0\.1(:\d+)?)$/
            .test(e.origin)) return;
      const names = (store) => {
        try { return Object.keys(store).slice(0, 60); } catch (err) { return ["<blocked: " + err.name + ">"]; }
      };
      const cookieNames = () => {
        try {
          return document.cookie.split(";").map((s) => s.trim().split("=")[0])
            .filter(Boolean).slice(0, 60);
        } catch (err) { return ["<blocked: " + err.name + ">"]; }
      };
      const send = (storageAccess) => {
        const mine = {
          cookies: cookieNames(),              // NAMES only (httpOnly ones are invisible here)
          localStorage: names(localStorage),   // NAMES only
          sessionStorage: names(sessionStorage),
        };
        const post = (top) => {
          const missing = (a, b) => (a || []).filter((k) => !(b || []).includes(k));
          window.parent.postMessage({
            type: "jth-sws-diag",
            href: location.origin + location.pathname,
            framed: window.top !== window,
            cookieEnabled: navigator.cookieEnabled,
            storageAccess,                     // true / false / "unsupported"
            ...mine,
            topTab: top || null,               // what a NORMAL tab sees, if seen
            missingVsTopTab: top ? {
              cookies: missing(top.cookies, mine.cookies),
              localStorage: missing(top.localStorage, mine.localStorage),
              sessionStorage: missing(top.sessionStorage, mine.sessionStorage),
            } : null,
          }, e.origin);
        };
        try {
          chrome.storage.local.get("swsTopSnapshot", (st) => {
            void chrome.runtime.lastError;
            post(st && st.swsTopSnapshot);
          });
        } catch (err) { post(null); }
      };
      if (document.hasStorageAccess) {
        document.hasStorageAccess().then(send, () => send("error"));
      } else {
        send("unsupported");
      }
    } catch (err) { /* no-op */ }
  });
})();

// Frame reload channel — same contract as the other site scripts: the
// background worker asks affected frames to reload once after it installs
// the cookie-header fallback, so the login applies.
try {
  chrome.runtime.onMessage.addListener((msg) => {
    if (window.top === window) return;
    if (msg && msg.type === "jth-reload" && typeof msg.domain === "string"
        && (location.hostname === msg.domain || location.hostname.endsWith("." + msg.domain))) {
      location.reload();
    }
  });
} catch (e) { /* no-op */ }
