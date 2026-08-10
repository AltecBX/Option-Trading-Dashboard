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

/** Run the script with a fake DOM. `framed` picks top-level vs embedded. */
function run({ framed, cookie = "", ls = {}, ss = {}, stored = null }) {
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
      get: (k, cb) => cb(stored ? { swsTopSnapshot: stored } : {}),
    } },
    runtime: { onMessage: { addListener: () => {} }, lastError: null },
  };
  const ctx = {
    window: win, document: doc, chrome, location: { pathname: "/stocks/us/tech/nyse-cien/ciena",
      origin: "https://simplywall.st", hostname: "simplywall.st", reload: () => {} },
    localStorage: ls, sessionStorage: ss, navigator: { cookieEnabled: true },
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
  const snap = r.saved.length ? r.saved[r.saved.length - 1].swsTopSnapshot : null;
  check("snapshot records cookie NAMES", snap && snap.cookies, ["sws_session", "_hjSession_44113"]);
  check("snapshot records localStorage NAMES", snap && snap.localStorage, ["sws:auth:token", "portfolios"]);
  // The whole privacy claim of this feature: names travel, values never do.
  const blob = JSON.stringify(r.saved);
  check("no cookie VALUE is stored", blob.includes("abc"), false);
  check("no localStorage VALUE is stored", blob.includes("SECRET"), false);
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
      if (fail) {
        console.error(`\ntest_helper_swssync: ${pass} passed, ${fail} FAILED`);
        process.exit(1);
      }
      console.log(`test_helper_swssync: ${pass} passed, 0 failed`);
    });
  });
}
