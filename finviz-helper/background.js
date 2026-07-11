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
  for (const primary of ["https://[*.]finviz.com/*", "https://[*.]tradingview.com/*", "https://[*.]unusualwhales.com/*"]) {
    for (const secondary of DASHBOARDS) {
      chrome.contentSettings.cookies.set(
        { primaryPattern: primary, secondaryPattern: secondary, setting: "allow" },
        () => diag("content-setting", {
          primary, secondary,
          error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || null,
        }));
    }
  }
}
chrome.runtime.onInstalled.addListener(applyCookieException);
chrome.runtime.onStartup.addListener(applyCookieException);

// ── 2) SameSite upgrade — finviz + unusualwhales ONLY ───────────────────────
// v2.2: tradingview.com is deliberately EXCLUDED. Rewriting TV's cookies
// (v2.0/2.1) corrupted their anti-abuse/CSRF cookie state and produced
// duplicate partitioned/unpartitioned copies — TV's backend then errored on
// every route ("Back before you know it"). TV works framed without any
// rewriting. Additional safety everywhere: never touch known anti-abuse
// cookies, never touch partitioned cookies, and delete-before-set so a
// rewrite can never create a duplicate.
const REWRITE_DOMAINS = ["finviz.com", "unusualwhales.com"];
const SKIP_COOKIE = /^(cf_clearance|__cf|_cfuvid|datadome|__ddg|_px|_dd_s|__stripe)/i;
// TradingView (v2.3): SURGICAL allow-list. Blanket rewriting corrupted TV's
// anti-abuse cookie state (the 'Back before you know it' incident), but with
// NO rewriting the login can't work framed — TV's auth cookies are
// SameSite-restricted, so the browser refuses to send them cross-site. The
// middle path: rewrite ONLY the named auth cookies, nothing else, ever.
const TV_AUTH_COOKIE = /^(sessionid|sessionid_sign|device_t|csrftoken)$/;

function upgradeCookie(cookie, why) {
  const bare = (cookie.domain || "").replace(/^\./, "");
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
  // Delete first so the rewrite can never leave two copies behind.
  chrome.cookies.remove({ url: details.url, name: cookie.name, storeId: cookie.storeId }, () => {
    void chrome.runtime.lastError;
    chrome.cookies.set(details, (c) =>
      diag("cookie-upgraded", { name: cookie.name, why, ok: !!c,
        error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || null }));
  });
}

function eligible(cookie) {
  const bare = (cookie.domain || "").replace(/^\./, "");
  if (cookie.partitionKey) return false;          // frame-partitioned: leave alone
  if (cookie.sameSite === "no_restriction") return false;
  if (SKIP_COOKIE.test(cookie.name || "")) return false;
  if (bare === "tradingview.com" || bare.endsWith(".tradingview.com")) {
    return TV_AUTH_COOKIE.test(cookie.name || "");   // allow-list ONLY
  }
  return REWRITE_DOMAINS.some((d) => bare === d || bare.endsWith("." + d));
}

chrome.cookies.onChanged.addListener(({ cookie, removed, cause }) => {
  try {
    const bare = (cookie.domain || "").replace(/^\./, "");
    const watched = ["finviz.com", "tradingview.com", "unusualwhales.com"];
    if (!watched.some((d) => bare === d || bare.endsWith("." + d))) return;
    diag("cookie", { name: cookie.name, domain: cookie.domain, path: cookie.path,
                     sameSite: cookie.sameSite, secure: cookie.secure,
                     session: cookie.session, hostOnly: cookie.hostOnly,
                     partitioned: !!cookie.partitionKey, removed, cause });
    scheduleCookieRuleRefresh("cookie-changed");   // v2.6 fallback stays current
    if (removed || cause === "overwrite") return;
    if (!eligible(cookie)) return;
    upgradeCookie(cookie, "changed");
  } catch (e) {
    diag("cookie-error", { message: String(e && e.message || e) });
  }
});

// One-time sweep on install/update: upgrade cookies that already exist
// (e.g. you logged into Finviz in a normal tab before installing v1.2).
function sweepExistingCookies() {
  for (const dom of [...REWRITE_DOMAINS, "tradingview.com"]) sweepDomain(dom);
}
function sweepDomain(dom) {
  try {
    chrome.cookies.getAll({ domain: dom }, (cookies) => {
      void chrome.runtime.lastError;
      (cookies || []).forEach((cookie) => { if (eligible(cookie)) upgradeCookie(cookie, "sweep"); });
      diag("cookie-sweep", { domain: dom, count: (cookies || []).length });
    });
  } catch (e) { /* no-op */ }
}
chrome.runtime.onInstalled.addListener(sweepExistingCookies);

// ── 2b-compat) Cookie-header fallback (v2.6) — Comet / Brave / forks ────────
// Browsers without the contentSettings API can't get the third-party-cookie
// exception, and they BLOCK cookies on cross-site frame requests outright —
// SameSite rewriting and the Storage Access API don't help because the
// frame's own page request is sent cookieless, so the site renders logged
// out. Fallback (active ONLY when contentSettings is missing — Chrome is
// untouched): mirror each site's own cookie jar into a declarativeNetRequest
// SESSION rule that sets the Cookie header on dashboard-initiated frame
// requests. The value is exactly what a first-party visit would send; it is
// kept in memory only (session rules are never written to disk), never
// logged, and never leaves the browser. Sign in once in a NORMAL tab (or the
// TV Sign-in popup) and the embedded views stay signed in.
const COOKIE_RULE_IDS = { "finviz.com": 9001, "tradingview.com": 9002, "unusualwhales.com": 9003 };
function cookieFallbackActive() {
  return !(chrome.contentSettings && chrome.contentSettings.cookies);
}

function buildCookieHeader(dom, cb) {
  chrome.cookies.getAll({ domain: dom }, (cookies) => {
    void chrome.runtime.lastError;
    const seen = new Set();
    const parts = [];
    (cookies || []).forEach((c) => {
      if (c.partitionKey) return;         // partitioned copies: leave alone
      if (seen.has(c.name)) return;       // one value per name
      seen.add(c.name);
      parts.push(c.name + "=" + c.value);
    });
    cb(parts.join("; "), parts.length);
  });
}

let _cookieRuleTimer = null;
function refreshCookieRules(why) {
  if (!cookieFallbackActive()) return;
  const doms = Object.keys(COOKIE_RULE_IDS);
  let left = doms.length;
  const add = [];
  const counts = {};
  doms.forEach((dom) => {
    buildCookieHeader(dom, (value, n) => {
      counts[dom] = n;
      if (value) {
        add.push({
          id: COOKIE_RULE_IDS[dom],
          priority: 2,
          action: {
            type: "modifyHeaders",
            requestHeaders: [{ header: "cookie", operation: "set", value }],
          },
          condition: {
            requestDomains: [dom],
            initiatorDomains: ["dashboard.jerrytrade.com", "localhost", "127.0.0.1", dom],
            resourceTypes: ["sub_frame", "xmlhttprequest"],
          },
        });
      }
      if (--left === 0) {
        chrome.declarativeNetRequest.updateSessionRules(
          { removeRuleIds: doms.map((d) => COOKIE_RULE_IDS[d]), addRules: add },
          () => diag("cookie-header-rules", { why, counts,
            error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || null }));
      }
    });
  });
}
function scheduleCookieRuleRefresh(why) {
  if (!cookieFallbackActive()) return;
  if (_cookieRuleTimer) clearTimeout(_cookieRuleTimer);
  _cookieRuleTimer = setTimeout(() => { _cookieRuleTimer = null; refreshCookieRules(why); }, 400);
}
chrome.runtime.onInstalled.addListener(() => refreshCookieRules("install"));
chrome.runtime.onStartup.addListener(() => refreshCookieRules("startup"));

// ── 2b) One-time TradingView repair (v2.2) ──────────────────────────────────
// Undo the damage the earlier rewriting did: clear tradingview.com cookies
// ONCE on update so the browser rebuilds a clean jar. You'll need to log
// into TradingView once afterwards (normal tab or the embedded view).
function clearDomainCookies(dom, done) {
  chrome.cookies.getAll({ domain: dom }, (cookies) => {
    void chrome.runtime.lastError;
    let n = (cookies || []).length;
    if (!n) { done && done(0); return; }
    let left = n;
    (cookies || []).forEach((cookie) => {
      const bare = (cookie.domain || "").replace(/^\./, "");
      chrome.cookies.remove({
        url: (cookie.secure ? "https://" : "http://") + bare + (cookie.path || "/"),
        name: cookie.name, storeId: cookie.storeId,
      }, () => { void chrome.runtime.lastError; if (--left === 0) done && done(n); });
    });
  });
}
chrome.runtime.onInstalled.addListener(() => {
  try {
    chrome.storage.local.get("tvRepair22", (st) => {
      void chrome.runtime.lastError;
      if (st && st.tvRepair22) return;
      clearDomainCookies("tradingview.com", (n) => {
        diag("tv-repair", { cleared: n });
        chrome.storage.local.set({ tvRepair22: true }, () => void chrome.runtime.lastError);
      });
    });
  } catch (e) { /* no-op */ }
});

// ── 3) Theme cookie writer (v1.4) ───────────────────────────────────────────
// Finviz's own theme endpoint answers with a SameSite=Lax Set-Cookie, which
// browsers reject inside a cross-site frame. The in-frame script intercepts
// the toggle and asks us to write the same preference cookie through the
// cookies API instead — no page content involved, just one named cookie.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "jth-get-caps") {
    // Capability report: Chromium forks (Comet, Brave, ...) often lack the
    // contentSettings API, so the third-party-cookie exception can't be
    // registered programmatically — the v2.6 cookie-header fallback covers
    // them. A dashboard load also asks this, so refresh the fallback rules.
    scheduleCookieRuleRefresh("dashboard-load");
    sendResponse({ contentSettings: !!(chrome.contentSettings && chrome.contentSettings.cookies) });
    return;
  }
  if (msg && msg.type === "jth-clear-cookies"
      && ["tradingview.com", "finviz.com", "unusualwhales.com"].includes(msg.domain)) {
    clearDomainCookies(msg.domain, (n) => {
      diag("clear-cookies", { domain: msg.domain, cleared: n });
      scheduleCookieRuleRefresh("cleared");
      sendResponse({ ok: true, cleared: n });
    });
    return true;  // async
  }
  if (!msg || msg.type !== "fvh-set-theme" || !/^(light|dark)$/.test(msg.value)) return;
  chrome.cookies.set({
    url: "https://finviz.com/",
    name: "chartsTheme",
    value: msg.value,
    domain: ".finviz.com",
    path: "/",
    secure: true,
    sameSite: "no_restriction",
    expirationDate: Math.floor(Date.now() / 1000) + 365 * 86400,
  }, (c) => {
    diag("theme", { value: msg.value, ok: !!c,
      error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || null });
    sendResponse({ ok: !!c });
  });
  return true;  // async response
});

// ── 4) DNR rule-match diagnostics (available for unpacked extensions) ──────
if (chrome.declarativeNetRequest && chrome.declarativeNetRequest.onRuleMatchedDebug) {
  chrome.declarativeNetRequest.onRuleMatchedDebug.addListener((m) => {
    try {
      diag("rule-match", { url: (m.request.url || "").split("?")[0],
                           type: m.request.type, ruleId: m.rule.ruleId });
    } catch (e) { /* no-op */ }
  });
}
