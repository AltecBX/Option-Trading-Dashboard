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

// ── 2b-compat) Cookie-header fallback (v2.6, empirical gating v2.7) ─────────
// Some Chromium forks (Comet, Brave, ...) block cookies on cross-site frame
// requests outright — SameSite rewriting, contentSettings exceptions and the
// Storage Access API all fail there because the frame's own page request is
// sent cookieless, so the site renders logged out. Fallback: mirror each
// site's own cookie jar into a declarativeNetRequest SESSION rule that sets
// the Cookie header on dashboard-initiated frame requests — exactly what a
// first-party visit would send. Values live in memory only (session rules
// are never written to disk), are never logged, and never leave the browser.
//
// v2.7: gating is EMPIRICAL, not API-detection. v2.6 keyed off "contentSettings
// missing", but forks can ship the API and still block the cookies, so the
// fallback never armed. Now a passive webRequest observer watches the
// dashboard's own frame requests: if the browser sent one COOKIELESS while
// the jar holds cookies for that site, that domain is marked blocked (saved
// in extension storage), the header rule is installed, and the logged-out
// frame is reloaded once. In Chrome the browser attaches cookies natively,
// the observer sees them, and none of this ever activates.
const COOKIE_RULE_IDS = { "finviz.com": 9001, "tradingview.com": 9002, "unusualwhales.com": 9003 };
const DASH_ORIGINS = ["https://dashboard.jerrytrade.com", "http://localhost", "http://127.0.0.1"];
const INJECT = {};   // domain -> true once the browser was SEEN dropping cookies

function siteDomainOf(url) {
  try {
    const h = new URL(url).hostname;
    return Object.keys(COOKIE_RULE_IDS).find((d) => h === d || h.endsWith("." + d)) || null;
  } catch (e) { return null; }
}

// Restore the blocked-domain markers on every service-worker start.
try {
  chrome.storage.local.get("cookieInject", (st) => {
    void chrome.runtime.lastError;
    Object.assign(INJECT, (st && st.cookieInject) || {});
    if (Object.keys(INJECT).length) refreshCookieRules("sw-start");
  });
} catch (e) { /* no-op */ }

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
function refreshCookieRules(why, done) {
  const doms = Object.keys(COOKIE_RULE_IDS).filter((d) => INJECT[d]);
  const removeIds = Object.values(COOKIE_RULE_IDS);
  if (!doms.length) { done && done(); return; }
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
          { removeRuleIds: removeIds, addRules: add },
          () => {
            diag("cookie-header-rules", { why, counts,
              error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || null });
            done && done();
          });
      }
    });
  });
}
function scheduleCookieRuleRefresh(why) {
  if (!Object.keys(COOKIE_RULE_IDS).some((d) => INJECT[d])) return;
  if (_cookieRuleTimer) clearTimeout(_cookieRuleTimer);
  _cookieRuleTimer = setTimeout(() => { _cookieRuleTimer = null; refreshCookieRules(why); }, 400);
}
chrome.runtime.onInstalled.addListener(() => refreshCookieRules("install"));
chrome.runtime.onStartup.addListener(() => refreshCookieRules("startup"));

// Passive observer (v2.7): detect the browser dropping cookies on the
// dashboard's frame requests. Metadata only — it checks whether a Cookie
// header was PRESENT, never reads its value into any log or message.
if (chrome.webRequest && chrome.webRequest.onSendHeaders) {
  chrome.webRequest.onSendHeaders.addListener((d) => {
    try {
      if (!d.initiator || !DASH_ORIGINS.some((o) => d.initiator === o || d.initiator.startsWith(o + ":"))) return;
      const dom = siteDomainOf(d.url);
      if (!dom || INJECT[dom]) return;
      const hasCookie = (d.requestHeaders || []).some((h) => (h.name || "").toLowerCase() === "cookie");
      if (hasCookie) return;   // browser sent cookies natively (Chrome) — fallback stays off
      chrome.cookies.getAll({ domain: dom }, (cookies) => {
        void chrome.runtime.lastError;
        if (!(cookies || []).some((c) => !c.partitionKey)) return;  // nothing to send anyway
        if (INJECT[dom]) return;
        INJECT[dom] = true;
        chrome.storage.local.set({ cookieInject: INJECT }, () => void chrome.runtime.lastError);
        diag("cookie-blocked-detected", { domain: dom, note:
          "browser sent the frame request without cookies — enabling cookie-header fallback" });
        refreshCookieRules("detected", () => {
          // Reload the frame that just rendered logged-out; sync scripts in
          // that tab reload only the frames whose hostname matches.
          if (d.tabId >= 0) {
            chrome.tabs.sendMessage(d.tabId, { type: "jth-reload", domain: dom },
              () => void chrome.runtime.lastError);
          }
        });
      });
    } catch (e) { /* no-op */ }
  },
  { urls: ["*://*.finviz.com/*", "*://*.tradingview.com/*", "*://*.unusualwhales.com/*"],
    types: ["sub_frame"] },
  ["requestHeaders", "extraHeaders"]);
}

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
    sendResponse({
      contentSettings: !!(chrome.contentSettings && chrome.contentSettings.cookies),
      inject: Object.keys(INJECT).filter((d) => INJECT[d]),   // domains on the v2.7 fallback
    });
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
