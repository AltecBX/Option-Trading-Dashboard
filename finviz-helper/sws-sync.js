// Runs inside simplywall.st pages when embedded in the JerryTrade dashboard,
// and (for the mirror source + snapshot) in normal simplywall.st tabs.
//
// THE PROBLEM THIS SOLVES (v3.6)
// Finviz, TradingView and Unusual Whales authenticate with COOKIES, so the
// helper's SameSite rewrite keeps them signed in inside a frame. Simply Wall
// St does not: an audit of the real cookie jar while signed in returned only
// Hotjar analytics and Cloudflare's __cf_bm — no session cookie exists. Their
// login is a token in localStorage, and Chrome partitions localStorage per
// top-level site, so the framed copy is a different, empty store.
//
// Nothing can un-partition that. But an extension has content scripts in BOTH
// contexts, and both are the SAME ORIGIN (simplywall.st). So the session is
// MIRRORED: read in your normal tab, written into the frame's partitioned
// store. Values stay in extension storage on this machine and are never
// transmitted anywhere — the same trust boundary the cookie handling already
// operates under, applied to the storage this particular site happens to use.

// Analytics, telemetry and big caches: never mirrored. Copying these would
// waste the frame's storage quota and can confuse the app's own cache
// versioning. What is left is the app's own session state.
var JTH_SKIP = /^(REACT_QUERY_OFFLINE_CACHE|snowplowOutQueue|_hj|_ga|_gid|_gcl|_fbp|_uet|IR_|sentry|__darkreader|_cltk|amplitude|mp_|_pk_|optimizely|intercom|_rdt|_tt|_pin)/i;
var JTH_MARK = "__jth_mirrored_keys";   // what WE wrote, so logout can undo it
var JTH_MAX_VALUE = 256 * 1024;         // per key
var JTH_MAX_TOTAL = 2 * 1024 * 1024;    // all keys

// ── Normal tabs: be the mirror SOURCE ──────────────────────────────────────
(function swsTopSnapshot() {
  if (window.top !== window) return;              // only real tabs
  const snap = () => {
    try {
      const keys = (st) => { try { return Object.keys(st).slice(0, 80); } catch (e) { return []; } };
      // Names only — this is what the "Diagnose login" button reports.
      chrome.storage.local.set({ swsTopSnapshot: {
        at: new Date().toISOString(),
        href: location.origin + location.pathname,
        cookies: document.cookie.split(";").map((s) => s.trim().split("=")[0]).filter(Boolean).slice(0, 80),
        localStorage: keys(localStorage),
        sessionStorage: keys(sessionStorage),
      } });

      // Values — the session itself, for the frame to adopt.
      const entries = {};
      let total = 0;
      for (const k of Object.keys(localStorage)) {
        if (JTH_SKIP.test(k) || k === JTH_MARK) continue;
        let v = null;
        try { v = localStorage.getItem(k); } catch (e) { continue; }
        if (typeof v !== "string" || v.length > JTH_MAX_VALUE) continue;
        if (total + v.length > JTH_MAX_TOTAL) break;
        total += v.length;
        entries[k] = v;
      }
      chrome.storage.local.set({ swsSession: { at: Date.now(), entries } });
    } catch (e) { /* no-op */ }
  };
  // Several triggers: the tab may predate this version, and a sign-in (or a
  // token refresh) can happen long after load.
  window.addEventListener("load", () => setTimeout(snap, 2500));
  setTimeout(snap, 3000);
  setTimeout(snap, 8000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) snap(); });
  window.addEventListener("focus", snap);
  window.addEventListener("pagehide", snap);
  setInterval(() => { if (!document.hidden) snap(); }, 30000);
})();

// ── Framed: adopt the mirrored session, then behave as before ──────────────
(function swsSync() {
  if (window.top === window) return;   // normal simplywall.st tabs: do nothing

  // Apply as early as possible. This script runs at document_start, so on a
  // normal load the keys are in place before the app boots and it simply
  // renders signed in — no reload, no flash of a logged-out page.
  const applySession = (then) => {
    try {
      chrome.storage.local.get("swsSession", (st) => {
        void chrome.runtime.lastError;
        const sess = st && st.swsSession;
        if (!sess || !sess.entries) { if (then) then(false); return; }
        let changed = false;
        let mirrored = [];
        try { mirrored = JSON.parse(localStorage.getItem(JTH_MARK) || "[]"); } catch (e) {}
        // Adopt anything new or stale.
        const now = [];
        for (const k of Object.keys(sess.entries)) {
          now.push(k);
          try {
            if (localStorage.getItem(k) !== sess.entries[k]) {
              localStorage.setItem(k, sess.entries[k]);
              changed = true;
            }
          } catch (e) { /* quota or blocked: skip this key */ }
        }
        // Sign out in the normal tab must sign out here too: drop keys WE
        // wrote that the source no longer has. Keys the frame created itself
        // are never touched.
        for (const k of mirrored) {
          if (now.indexOf(k) === -1) {
            try { localStorage.removeItem(k); changed = true; } catch (e) {}
          }
        }
        try { localStorage.setItem(JTH_MARK, JSON.stringify(now)); } catch (e) {}
        if (then) then(changed);
      });
    } catch (e) { if (then) then(false); }
  };

  // If the app had already booted before the keys landed, it read an empty
  // store — reload ONCE so it re-reads. Guarded so this can never loop.
  // Ask the background worker to read a normal tab RIGHT NOW, then adopt.
  // This removes the dependence on that tab's content script having run.
  const pullThenApply = (then) => {
    try {
      chrome.runtime.sendMessage({ type: "jth-sws-pull-session" }, () => {
        void chrome.runtime.lastError;
        applySession(then || null);
      });
    } catch (e) { applySession(then || null); }
  };
  pullThenApply();

  applySession((changed) => {
    if (!changed) return;
    if (document.readyState === "loading") return;      // app hasn't read it yet
    let done = null;
    try { done = sessionStorage.getItem("jth-sws-adopted"); } catch (e) {}
    if (done) return;
    try { sessionStorage.setItem("jth-sws-adopted", "1"); } catch (e) {}
    location.reload();
  });
  // Keep it fresh (token refresh / later sign-in) without ever reloading again.
  setInterval(() => pullThenApply(null), 20000);
  window.addEventListener("focus", () => pullThenApply(null));

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
  // Kept for the cookie side (Cloudflare, and anything cookie-based they add
  // later). simplywall.st only, one request per tab, one reload ever — it can
  // never repeat v2.5's reload-on-every-click that cost TradingView work.
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

  // ── Diagnose-login channel (v3.1) ────────────────────────────────────────
  // Answers one explicit request with the SHAPE of storage — key and cookie
  // NAMES only, never values, never page content.
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
        const post = (top, audit, sess) => {
          const missing = (a, b) => (a || []).filter((k) => !(b || []).includes(k));
          let hv = "";
          try { hv = chrome.runtime.getManifest().version; } catch (e) {}
          window.parent.postMessage({
            type: "jth-sws-diag",
            helperVersion: hv,
            cookieAudit: audit,
            // Mirror status: how many keys are available and when they were
            // last read from a normal tab. Counts and names only.
            mirror: sess ? { at: sess.at, keys: Object.keys(sess.entries || {}) } : null,
            href: location.origin + location.pathname,
            framed: window.top !== window,
            cookieEnabled: navigator.cookieEnabled,
            storageAccess,
            ...mine,
            topTab: top || null,
            missingVsTopTab: top ? {
              cookies: missing(top.cookies, mine.cookies),
              localStorage: missing(top.localStorage, mine.localStorage),
              sessionStorage: missing(top.sessionStorage, mine.sessionStorage),
            } : null,
          }, e.origin);
        };
        try {
          chrome.storage.local.get(["swsTopSnapshot", "swsSession"], (st) => {
            void chrome.runtime.lastError;
            const top = st && st.swsTopSnapshot;
            const sess = st && st.swsSession;
            try {
              chrome.runtime.sendMessage({ type: "jth-sws-cookie-audit" }, (audit) => {
                void chrome.runtime.lastError;
                post(top, audit || null, sess);
              });
            } catch (e2) { post(top, null, sess); }
          });
        } catch (err) { post(null, null, null); }
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
