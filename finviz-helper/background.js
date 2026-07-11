// JerryTrade Finviz Helper — background service worker (v1.2).
//
// Why this exists: Finviz's session cookies are SameSite=Lax, and browsers
// neither store nor send Lax cookies inside a cross-site iframe — so logging
// in inside the embedded view succeeded on Finviz's side but the browser
// dropped the session cookie, landing you back logged-out. Chrome's
// third-party-cookie blocking can drop even SameSite=None cookies without an
// exception. Both are fixed here with official, user-consented APIs:
//
//   1. contentSettings: registers a third-party-cookie EXCEPTION for finviz
//      cookies when the top-level site is the JerryTrade dashboard — the
//      same exception you could add by hand in Chrome's cookie settings.
//   2. cookies API: when Finviz sets a cookie as Lax/unspecified, rewrite
//      its METADATA to SameSite=None; Secure so it works inside the frame.
//      The rewrite happens entirely inside your browser via the browser's
//      own cookie store. Cookie VALUES are never logged, stored elsewhere,
//      or transmitted anywhere.
//
// Diagnostics (temporary): every event logs cookie NAME / DOMAIN / SameSite
// / cause, redirect-relevant rule matches, and content-setting results —
// never values, passwords, or tokens. View them in this service worker's
// console (chrome://extensions → "Inspect views: service worker") AND in the
// dashboard page's DevTools console, where they're relayed as
// "[finviz-helper] …" debug lines.

const DASHBOARDS = [
  "https://dashboard.jerrytrade.com/*",
  "http://localhost/*",
  "http://127.0.0.1/*",
];

const DIAG = [];
function diag(kind, info) {
  const event = { ts: new Date().toISOString(), kind, ...info };
  DIAG.push(event);
  if (DIAG.length > 80) DIAG.shift();
  console.log("[finviz-helper]", kind, JSON.stringify(info));
  // Relay to any open dashboard tab so the page console shows it too.
  try {
    chrome.tabs.query({ url: DASHBOARDS }, (tabs) => {
      void chrome.runtime.lastError;
      (tabs || []).forEach((t) =>
        chrome.tabs.sendMessage(t.id, { type: "fvh-diag", event }, () => void chrome.runtime.lastError));
    });
  } catch (e) { /* no-op */ }
}

// ── 1) Third-party cookie exception ─────────────────────────────────────────
function applyCookieException() {
  if (!chrome.contentSettings || !chrome.contentSettings.cookies) {
    diag("content-setting", { supported: false,
      note: "contentSettings API unavailable in this browser — relying on SameSite upgrade only" });
    return;
  }
  for (const secondary of DASHBOARDS) {
    chrome.contentSettings.cookies.set(
      { primaryPattern: "https://[*.]finviz.com/*", secondaryPattern: secondary, setting: "allow" },
      () => diag("content-setting", {
        primary: "https://[*.]finviz.com/*", secondary,
        error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || null,
      }));
  }
}
chrome.runtime.onInstalled.addListener(applyCookieException);
chrome.runtime.onStartup.addListener(applyCookieException);

// ── 2) SameSite upgrade for finviz cookies ──────────────────────────────────
chrome.cookies.onChanged.addListener(({ cookie, removed, cause }) => {
  try {
    const bare = (cookie.domain || "").replace(/^\./, "");
    if (!(bare === "finviz.com" || bare.endsWith(".finviz.com"))) return;
    // Diagnostic: metadata only — NEVER the value.
    diag("cookie", { name: cookie.name, domain: cookie.domain, path: cookie.path,
                     sameSite: cookie.sameSite, secure: cookie.secure,
                     session: cookie.session, hostOnly: cookie.hostOnly,
                     removed, cause });
    if (removed || cookie.sameSite === "no_restriction") return;
    // Re-write the SAME cookie with SameSite=None; Secure so the embedded
    // (cross-site) frame can use it. Everything else is preserved.
    const details = {
      url: "https://" + bare + (cookie.path || "/"),
      name: cookie.name,
      value: cookie.value,
      path: cookie.path,
      secure: true,
      httpOnly: cookie.httpOnly,
      sameSite: "no_restriction",
      storeId: cookie.storeId,
    };
    if (!cookie.hostOnly) details.domain = cookie.domain;
    if (!cookie.session && cookie.expirationDate) details.expirationDate = cookie.expirationDate;
    chrome.cookies.set(details, (c) =>
      diag("cookie-upgraded", { name: cookie.name, ok: !!c,
        error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || null }));
  } catch (e) {
    diag("cookie-error", { message: String(e && e.message || e) });
  }
});

// One-time sweep on install/update: upgrade cookies that already exist
// (e.g. you logged into Finviz in a normal tab before installing v1.2).
function sweepExistingCookies() {
  try {
    chrome.cookies.getAll({ domain: "finviz.com" }, (cookies) => {
      void chrome.runtime.lastError;
      (cookies || []).forEach((cookie) => {
        if (cookie.sameSite === "no_restriction") return;
        const bare = (cookie.domain || "").replace(/^\./, "");
        const details = {
          url: "https://" + bare + (cookie.path || "/"),
          name: cookie.name, value: cookie.value, path: cookie.path,
          secure: true, httpOnly: cookie.httpOnly,
          sameSite: "no_restriction", storeId: cookie.storeId,
        };
        if (!cookie.hostOnly) details.domain = cookie.domain;
        if (!cookie.session && cookie.expirationDate) details.expirationDate = cookie.expirationDate;
        chrome.cookies.set(details, () => void chrome.runtime.lastError);
      });
      diag("cookie-sweep", { count: (cookies || []).length });
    });
  } catch (e) { /* no-op */ }
}
chrome.runtime.onInstalled.addListener(sweepExistingCookies);

// ── 3) DNR rule-match diagnostics (available for unpacked extensions) ──────
if (chrome.declarativeNetRequest && chrome.declarativeNetRequest.onRuleMatchedDebug) {
  chrome.declarativeNetRequest.onRuleMatchedDebug.addListener((m) => {
    try {
      diag("rule-match", { url: (m.request.url || "").split("?")[0],
                           type: m.request.type, ruleId: m.rule.ruleId });
    } catch (e) { /* no-op */ }
  });
}
