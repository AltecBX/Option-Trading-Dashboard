// Runs inside simplywall.st pages when embedded in the JerryTrade dashboard,
// and (names-only, for diagnostics) in normal simplywall.st tabs.
//
// HISTORY WORTH KEEPING (v3.9)
// v3.6/3.7 mirrored localStorage from a normal tab into the frame, on the
// belief that Simply Wall St kept its login there. That belief came from a
// cookie audit that reported "no session cookie" — which was itself wrong: the
// extension only held https:// host permission, and a cookie without the
// Secure flag lives on the http:// origin, so the real session cookies (auth,
// PHPSESSID, _sws_*) were invisible to it. v3.8 fixed the permission, the
// existing SameSite rewrite reached those cookies, and the login worked.
//
// The mirror is therefore REMOVED: it copied session values between storage
// contexts for no benefit, and storing those values was the only part of this
// extension that ever held anything sensitive at rest. What remains here never
// reads a value — only key and cookie NAMES, for the diagnostic.

var JTH_MARK = "__jth_mirrored_keys";   // v3.6/3.7 leftovers, cleaned up below

// ── Normal tabs: names-only snapshot, for "Diagnose login" ─────────────────
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
  window.addEventListener("load", () => setTimeout(snap, 2500));
  setTimeout(snap, 3000);
  setTimeout(snap, 8000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) snap(); });
  window.addEventListener("focus", snap);
  window.addEventListener("pagehide", snap);
  setInterval(() => { if (!document.hidden) snap(); }, 30000);
})();

// ── Framed ────────────────────────────────────────────────────────────────
(function swsSync() {
  if (window.top === window) return;   // normal simplywall.st tabs: do nothing

  // One-time cleanup of what the v3.6/3.7 mirror wrote into this frame's
  // storage. Anyone who ran those versions has copied values sitting here;
  // removing them restores the frame to state it created itself. Runs once —
  // after the marker is gone there is nothing left to clean.
  (function unmirror() {
    try {
      const raw = localStorage.getItem(JTH_MARK);
      if (!raw) return;
      let keys = [];
      try { keys = JSON.parse(raw) || []; } catch (e) { keys = []; }
      keys.forEach((k) => { try { localStorage.removeItem(k); } catch (e) {} });
      localStorage.removeItem(JTH_MARK);
      // And drop the stored copy in extension storage.
      try { chrome.storage.local.remove("swsSession"); } catch (e) {}
    } catch (e) { /* no-op */ }
  })();

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
      // Report the PATH too (v2.9): only the "{exchange}-{ticker}" segment is
      // derivable from a symbol, so reporting the real path lets the dashboard
      // LEARN a company's exact address instead of guessing it.
      window.parent.postMessage(
        { type: "jth-sws-ticker", symbol: sym, path: location.pathname }, "*");
    } catch (e) { /* no-op */ }
  };
  setInterval(report, 1500);
  window.addEventListener("load", report);

  // ── Storage Access (v3.1) ────────────────────────────────────────────────
  // Cookie-side only. simplywall.st only, one request per tab, one reload
  // ever — it cannot repeat v2.5's reload-on-every-click that cost unsaved
  // TradingView work.
  let asked = false;
  const askForStorage = () => {
    if (asked) return;
    asked = true;
    try {
      if (!document.hasStorageAccess || !document.requestStorageAccess) return;
      document.hasStorageAccess().then((has) => {
        if (has) return;
        return document.requestStorageAccess().then(() => {
          let already = null;
          try { already = sessionStorage.getItem("jth-sa-reloaded"); } catch (e) {}
          if (already) return;
          try { sessionStorage.setItem("jth-sa-reloaded", "1"); } catch (e) {}
          location.reload();
        }, () => {});
      }).catch(() => {});
    } catch (e) { /* no-op */ }
  };
  window.addEventListener("click", askForStorage, { once: true, capture: true });
  window.addEventListener("keydown", askForStorage, { once: true, capture: true });

  // ── Diagnose-login channel ───────────────────────────────────────────────
  // Answers one explicit request from the dashboard with the SHAPE of storage
  // — key and cookie NAMES only, never values, never page content. This is
  // what identified the real bug (the frame was missing auth / PHPSESSID /
  // _sws_* while localStorage matched exactly), so it stays.
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
      const send = (storageAccess, idb) => {
        const mine = {
          cookies: cookieNames(),
          localStorage: names(localStorage),
          sessionStorage: names(sessionStorage),
          indexedDB: idb || [],
        };
        const post = (top, audit) => {
          const missing = (a, b) => (a || []).filter((k) => !(b || []).includes(k));
          let hv = "";
          try { hv = chrome.runtime.getManifest().version; } catch (e) {}
          window.parent.postMessage({
            type: "jth-sws-diag",
            helperVersion: hv,
            cookieAudit: audit,
            href: location.origin + location.pathname,
            framed: window.top !== window,
            cookieEnabled: navigator.cookieEnabled,
            storageAccess,
            ...mine,
            topTab: top || null,
            // The comparison that found the bug: which cookies a normal tab
            // has that this frame does not.
            missingVsTopTab: top ? {
              cookies: missing(top.cookies, mine.cookies),
              localStorage: missing(top.localStorage, mine.localStorage),
              sessionStorage: missing(top.sessionStorage, mine.sessionStorage),
            } : null,
          }, e.origin);
        };
        // Refresh the normal-tab view first (names only), then read the jar.
        const then = () => {
          try {
            chrome.storage.local.get("swsTopSnapshot", (st) => {
              void chrome.runtime.lastError;
              const top = st && st.swsTopSnapshot;
              try {
                chrome.runtime.sendMessage({ type: "jth-sws-cookie-audit" }, (audit) => {
                  void chrome.runtime.lastError;
                  post(top, audit || null);
                });
              } catch (e2) { post(top, null); }
            });
          } catch (err) { post(null, null); }
        };
        try {
          chrome.runtime.sendMessage({ type: "jth-sws-pull-snapshot" }, () => {
            void chrome.runtime.lastError; then();
          });
        } catch (err) { then(); }
      };
      const withIdb = (sa) => {
        try {
          if (indexedDB && indexedDB.databases) {
            indexedDB.databases().then(
              (dbs) => send(sa, (dbs || []).map((d) => d && d.name).filter(Boolean).slice(0, 30)),
              () => send(sa, []));
            return;
          }
        } catch (e) {}
        send(sa, []);
      };
      if (document.hasStorageAccess) {
        document.hasStorageAccess().then(withIdb, () => withIdb("error"));
      } else {
        withIdb("unsupported");
      }
    } catch (err) { /* no-op */ }
  });
})();

// Frame reload channel — the background worker asks affected frames to reload
// once after it installs the cookie-header fallback, so the login applies.
try {
  chrome.runtime.onMessage.addListener((msg) => {
    if (window.top === window) return;
    if (msg && msg.type === "jth-reload" && typeof msg.domain === "string"
        && (location.hostname === msg.domain || location.hostname.endsWith("." + msg.domain))) {
      location.reload();
    }
  });
} catch (e) { /* no-op */ }
