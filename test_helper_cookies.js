// test_helper_cookies.js — the Site Helper's cookie-rewrite rules (v3.0).
//
// WHY THIS EXISTS
// The helper rewrites site cookies to SameSite=None so a login survives
// inside an embedded frame. That rewrite is the single most dangerous thing
// the extension does: blanket-rewriting TradingView's cookies once corrupted
// its anti-abuse state and broke every route ("Back before you know it"), and
// rewriting a Cloudflare clearance cookie would stop the page loading at all.
//
// So the eligibility rules are load-bearing in both directions:
//   - miss a site's session cookie  -> the framed login silently evaporates
//   - touch an anti-abuse cookie    -> the site breaks outright
// This runs the REAL background.js against a stub extension API and asserts
// both directions for every embedded site.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "finviz-helper", "background.js");

function loadBackground() {
  const noop = () => {};
  const listener = { addListener: noop, removeListener: noop };
  const chrome = {
    runtime: { onInstalled: listener, onStartup: listener, onMessage: listener,
               lastError: null, id: "test", getManifest: () => ({ version: "3.0" }) },
    cookies: { onChanged: listener, getAll: (q, cb) => cb && cb([]), set: noop, remove: noop, get: noop },
    contentSettings: { cookies: { set: noop } },
    declarativeNetRequest: { updateDynamicRules: noop, onRuleMatchedDebug: listener,
                             getDynamicRules: (cb) => cb && cb([]) },
    webRequest: { onBeforeSendHeaders: listener, onSendHeaders: listener,
                  onHeadersReceived: listener, onCompleted: listener },
    storage: { local: { get: (k, cb) => cb && cb({}), set: noop } },
    tabs: { query: (q, cb) => cb && cb([]), sendMessage: noop, reload: noop },
  };
  const ctx = { chrome, console, setTimeout, clearTimeout, setInterval, clearInterval,
                URL, Set, Map, JSON, Date, RegExp, Object };
  vm.createContext(ctx);
  vm.runInContext(
    fs.readFileSync(SRC, "utf8") +
    "\n;globalThis.__t = {eligible, REWRITE_DOMAINS, SKIP_COOKIE, COOKIE_RULE_IDS};",
    ctx);
  return ctx.__t || ctx.globalThis.__t;
}

const t = loadBackground();
let pass = 0, fail = 0;
const check = (label, got, want) => {
  if (got === want) { pass++; return; }
  fail++;
  console.error(`  FAIL ${label}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
};

// domain, cookie name, sameSite, partitioned, should-be-rewritten
const CASES = [
  // Simply Wall St (v3.0) — the login that kept evaporating.
  ["simplywall.st", "sws_session", "lax", null, true],
  [".simplywall.st", "auth_token", "lax", null, true],
  ["app.simplywall.st", "sid", "unspecified", null, true],
  // ...but never its Cloudflare layer, which is what lets the page load.
  ["simplywall.st", "cf_clearance", "lax", null, false],
  ["simplywall.st", "__cf_bm", "lax", null, false],
  ["simplywall.st", "_cfuvid", "lax", null, false],
  // Universal safety rails.
  ["simplywall.st", "sws_session", "no_restriction", null, false],  // already cross-site
  ["simplywall.st", "sws_session", "lax", "partition-key", false],  // partitioned copy
  // Existing sites must be unaffected by the addition.
  ["finviz.com", "sid", "lax", null, true],
  ["unusualwhales.com", "_uw_session", "lax", null, true],
  ["finviz.com", "datadome", "lax", null, false],
  // TradingView keeps its surgical allow-list — auth cookies only.
  ["tradingview.com", "sessionid", "lax", null, true],
  ["tradingview.com", "sessionid_sign", "lax", null, true],
  ["tradingview.com", "telemetry_blah", "lax", null, false],
  // Anything not embedded is never touched.
  ["example.com", "sid", "lax", null, false],
  ["google.com", "SID", "lax", null, false],
];

for (const [domain, name, sameSite, partitionKey, want] of CASES) {
  check(`${domain} / ${name}`,
        !!t.eligible({ domain, name, sameSite, partitionKey }), want);
}

// Every embedded site needs BOTH mechanisms, or a login breaks on some browser:
// the SameSite rewrite (Chrome) and the cookie-header fallback (Comet/Brave).
for (const d of ["finviz.com", "unusualwhales.com", "simplywall.st"]) {
  check(`${d} in REWRITE_DOMAINS`, t.REWRITE_DOMAINS.includes(d), true);
}
for (const d of ["finviz.com", "tradingview.com", "unusualwhales.com", "simplywall.st"]) {
  check(`${d} has a cookie-header rule id`, typeof t.COOKIE_RULE_IDS[d] === "number", true);
}
// Rule ids must stay unique — a collision silently drops one site's rule.
const ids = Object.values(t.COOKIE_RULE_IDS);
check("cookie rule ids unique", new Set(ids).size === ids.length, true);

// TradingView must NEVER be blanket-rewritten (the incident this guards).
check("tradingview excluded from REWRITE_DOMAINS",
      t.REWRITE_DOMAINS.includes("tradingview.com"), false);

if (fail) {
  console.error(`\ntest_helper_cookies: ${pass} passed, ${fail} FAILED`);
  process.exit(1);
}
console.log(`test_helper_cookies: ${pass} passed, 0 failed`);
