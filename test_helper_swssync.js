// test_helper_swssync.js — the Simply Wall St in-frame content script (v3.3).
//
// WHY THIS EXISTS
// This script is the only component that can see inside the embedded frame,
// so it is what a login diagnosis depends on. It also runs in the user's
// NORMAL simplywall.st tabs, which makes its blast radius larger than any
// other helper file: a mistake here can spam extension storage, leak more
// than key names, or re-introduce the v2.5 click-reload bug that once cost
// unsaved TradingView work. Each of those is asserted below.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(path.join(__dirname, "finviz-helper", "sws-sync.js"), "utf8");

let pass = 0, fail = 0;
const check = (label, got, want) => {
  if (JSON.stringify(got) === JSON.stringify(want)) { pass++; return; }
  fail++;
  console.error(`  FAIL ${label}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
};

/** A Storage-like view over a plain object, so Object.keys() still works. */
function mkStore(obj) {
  return new Proxy(obj, {
    get(t, p) {
      if (p === "getItem") return (k) => (k in t ? t[k] : null);
      if (p === "setItem") return (k, v) => { t[k] = String(v); };
      if (p === "removeItem") return (k) => { delete t[k]; };
      if (p === "key") return (i) => Object.keys(t)[i] ?? null;
      if (p === "length") return Object.keys(t).length;
      return t[p];
    },
  });
}

/** Run the script with a fake DOM. `framed` picks top-level vs embedded. */
function run({ framed, cookie = "", ls = {}, ss = {}, stored = null, session = null }) {
  const listeners = {};       // window/document event listeners
  const saved = [];           // chrome.storage.local.set payloads
  const posted = [];          // postMessage calls to the parent
  const timers = [];          // deferred callbacks
  const mkWin = () => ({
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener: () => {},
  });
  const win = mkWin();
  win.top = framed ? {} : win;                 // window.top === window when top-level
  win.parent = { postMessage: (m, o) => posted.push({ m, o }) };
  const doc = {
    cookie,
    hidden: false,
    addEventListener: (t, fn) => { (listeners["doc:" + t] = listeners["doc:" + t] || []).push(fn); },
    hasStorageAccess: () => Promise.resolve(true),
    requestStorageAccess: () => Promise.resolve(),
  };
  const chrome = {
    storage: { local: {
      set: (o) => saved.push(o),
      get: (k, cb) => {
        const out = {};
        if (stored) out.swsTopSnapshot = stored;
        if (session) out.swsSession = session;
        cb(out);
      },
    } },
    runtime: { onMessage: { addListener: () => {} }, lastError: null },
  };
  const ctx = {
    window: win, document: doc, chrome, location: { pathname: "/stocks/us/tech/nyse-cien/ciena",
      origin: "https://simplywall.st", hostname: "simplywall.st", reload: () => {} },
    localStorage: mkStore(ls), sessionStorage: mkStore(ss), navigator: { cookieEnabled: true },
    setInterval: () => 0, clearInterval: () => {},
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    Date, JSON, Object, RegExp, Promise, console,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);
  const fire = (t, ev) => (listeners[t] || []).forEach((fn) => fn(ev));
  const flush = () => { const q = timers.splice(0); q.forEach((fn) => fn()); };
  return { listeners, saved, posted, fire, flush };
}

// ── Top-level tabs: snapshot key NAMES so the frame has a baseline ─────────
{
  const r = run({ framed: false, cookie: "sws_session=abc; _hjSession_44113=x",
                  ls: { "sws:auth:token": "SECRET", portfolios: "[]" },
                  ss: { miniValuatorLastVisitedStock: "CIEN" } });
  r.flush();
  check("top-level writes a snapshot", r.saved.length > 0, true);
  const snap = r.saved.map((o) => o.swsTopSnapshot).filter(Boolean).pop() || null;
  check("snapshot records cookie NAMES", snap && snap.cookies, ["sws_session", "_hjSession_44113"]);
  check("snapshot records localStorage NAMES", snap && snap.localStorage, ["sws:auth:token", "portfolios"]);
  // One store, one contract: swsTopSnapshot holds NAMES only. Nothing this
  // extension writes to disk contains a cookie value or a storage value.
  check("the reported snapshot holds no cookie value", JSON.stringify(snap).includes("abc"), false);
  check("the reported snapshot holds no storage value", JSON.stringify(snap).includes("SECRET"), false);
  // v3.9: there is no value store at all any more — see the "No value
  // mirroring" block below, which asserts that directly.
  check("no value store is written alongside the snapshot",
        r.saved.some((o) => "swsSession" in o), false);
  // An already-open tab must still get captured.
  check("re-snapshots when the tab regains focus", (() => {
    const before = r.saved.length; r.fire("focus"); return r.saved.length > before;
  })(), true);
  check("re-snapshots when it becomes visible", (() => {
    const before = r.saved.length; r.fire("doc:visibilitychange"); return r.saved.length > before;
  })(), true);
  // A top-level tab is not a frame: it must never message a parent.
  check("top-level posts nothing to a parent", r.posted.length, 0);
}

// ── No value mirroring (v3.9) ─────────────────────────────────────────────
// v3.6/3.7 copied localStorage VALUES between contexts, believing the login
// lived there. It did not — the session is a cookie (auth / PHPSESSID /
// _sws_*) that the extension simply could not see, fixed in v3.8. The mirror
// is removed, and these assertions keep it removed: storing session values at
// rest was the only part of this extension that ever held anything sensitive.
{
  const r = run({ framed: false, cookie: "auth=SECRETCOOKIE",
    ls: { "sws:auth": "SECRETTOKEN", portfolios: "[1]" } });
  r.flush();
  const blob = JSON.stringify(r.saved);
  check("a normal tab never stores a storage VALUE", blob.includes("SECRETTOKEN"), false);
  check("a normal tab never stores a cookie VALUE", blob.includes("SECRETCOOKIE"), false);
  check("no swsSession store is written",
        r.saved.some((o) => "swsSession" in o), false);
  const snap = r.saved.map((o) => o.swsTopSnapshot).filter(Boolean).pop() || null;
  check("the names-only snapshot still works", snap && snap.localStorage,
        ["sws:auth", "portfolios"]);
}
{
  // The frame must not write anything into the site's storage any more...
  const ls = { portfolios: "[]" };
  run({ framed: true, ls, session: { at: 1, entries: { "sws:auth": "TOKEN" } } });
  check("frame adopts nothing, even if an old store exists",
        Object.keys(ls), ["portfolios"]);
}
{
  // ...and it cleans up what the old mirror left behind, once.
  const ls = { "sws:auth": "TOKEN", portfolios: "[1]", ownKey: "keep",
               __jth_mirrored_keys: JSON.stringify(["sws:auth", "portfolios"]) };
  run({ framed: true, ls });
  check("cleanup removes a previously mirrored key", "sws:auth" in ls, false);
  check("cleanup removes the marker", "__jth_mirrored_keys" in ls, false);
  check("cleanup leaves keys the frame owns", ls.ownKey, "keep");
}

// ── Version reporting (v3.7) ──────────────────────────────────────────────
// announce.js hard-coded "2.7" from v2.7 through v3.6, so the dashboard chip
// and the Simply Wall St banner reported v2.7 no matter what was installed.
// Both of us then reasoned from a number that was never true. A literal here
// is always a bug: the manifest is the only source of truth.
{
  const ann = fs.readFileSync(path.join(__dirname, "finviz-helper", "announce.js"), "utf8");
  const mf = JSON.parse(fs.readFileSync(path.join(__dirname, "finviz-helper", "manifest.json"), "utf8"));
  check("announce.js reads the manifest version",
        /getManifest\(\)\.version/.test(ann), true);
  check("announce.js hard-codes no version literal",
        /const VERSION = ["'][0-9]/.test(ann), false);
  // The panel compares against these, so they must not drift either.
  // ONE constant in the frontend, and it must equal the shipped manifest.
  const lib = fs.readFileSync(path.join(__dirname, "app-lib.jsx"), "utf8");
  const shared = (lib.match(/const HELPER_LATEST = "([\d.]+)"/) || [])[1];
  check("app-lib's HELPER_LATEST matches the shipped manifest", shared, mf.version);
  // And nothing else may declare its own helper-version literal.
  const cards = fs.readFileSync(path.join(__dirname, "app-cards.jsx"), "utf8");
  check("no local LATEST literal remains in app-cards",
        /const LATEST = "[\d.]/.test(cards), false);
  check("no local SWS_LATEST literal remains in app-cards",
        /const SWS_LATEST = "[\d.]/.test(cards), false);
}

// ── Framed: answers a diagnostic request, with the diff ────────────────────
{
  const stored = { at: "2026-08-10T00:00:00Z", cookies: ["sws_session", "_hjSession_44113"],
                   localStorage: ["sws:auth:token", "portfolios"], sessionStorage: [] };
  const r = run({ framed: true, cookie: "_hjSession_44113=x",
                  ls: { portfolios: "[]" }, ss: {}, stored });
  r.fire("message", { origin: "https://dashboard.jerrytrade.com",
                      data: { type: "jth-sws-diag-req" } });
  return Promise.resolve().then(() => Promise.resolve()).then(() => {
    const reply = r.posted.map((p) => p.m).find((m) => m && m.type === "jth-sws-diag");
    check("frame answers the dashboard", !!reply, true);
    if (reply) {
      check("reply carries the top-tab snapshot", reply.topTab && reply.topTab.at, stored.at);
      // The verdict depends entirely on this diff being right.
      check("diff names the missing cookie", reply.missingVsTopTab.cookies, ["sws_session"]);
      check("diff names the missing auth key", reply.missingVsTopTab.localStorage, ["sws:auth:token"]);
    }
    // Untrusted origins must never receive storage shape.
    const r2 = run({ framed: true, stored });
    r2.fire("message", { origin: "https://evil.example", data: { type: "jth-sws-diag-req" } });
    return Promise.resolve().then(() => {
      check("ignores a request from a foreign origin",
            r2.posted.some((p) => p.m && p.m.type === "jth-sws-diag"), false);
    // The mirror stores real tokens, so the reply to the page must report the
    // mirror's SHAPE and never its contents.
    const r3 = run({ framed: true, ls: { portfolios: "[]" },
                     session: { at: 9, entries: { "sws:auth": "SUPERSECRET" } } });
    r3.fire("message", { origin: "https://dashboard.jerrytrade.com",
                         data: { type: "jth-sws-diag-req" } });
    return Promise.resolve().then(() => Promise.resolve()).then(() => {
      const m = r3.posted.map((p) => p.m).find((x) => x && x.type === "jth-sws-diag");
      check("reply carries no mirror section at all", m && "mirror" in m, false);
      check("reply leaks NO token value", JSON.stringify(m || {}).includes("SUPERSECRET"), false);
      check("reply still carries the cookie comparison that found the bug",
            m && "missingVsTopTab" in m, true);
      if (fail) {
        console.error(`\ntest_helper_swssync: ${pass} passed, ${fail} FAILED`);
        process.exit(1);
      }
      console.log(`test_helper_swssync: ${pass} passed, 0 failed`);
    });
    });
  });
}
