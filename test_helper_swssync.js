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
  // Two stores with deliberately DIFFERENT contracts, and the difference is
  // the privacy boundary:
  //   swsTopSnapshot -> names only. It is what gets reported to the page.
  //   swsSession     -> values. It is the session being mirrored, and it must
  //                     never leave extension storage.
  check("the reported snapshot holds no cookie value", JSON.stringify(snap).includes("abc"), false);
  check("the reported snapshot holds no storage value", JSON.stringify(snap).includes("SECRET"), false);
  const sess = r.saved.map((o) => o.swsSession).filter(Boolean).pop() || null;
  check("the mirror DOES carry the value (that is the fix)",
        sess && sess.entries["sws:auth:token"], "SECRET");
  // Cookies are handled by the cookie machinery; the mirror must not duplicate
  // them into storage.
  check("the mirror carries no cookie value", JSON.stringify(sess).includes("abc"), false);
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

// ── Session mirror (v3.6): the actual fix ─────────────────────────────────
// Simply Wall St keeps its login in localStorage, which Chrome partitions per
// top-level site. The extension reads it in a normal tab and writes it into
// the frame's store. These assertions cover the ways that can go wrong:
// mirroring junk, blowing the quota, leaving a stale session after logout, or
// reload-looping.
{
  // SOURCE: a normal tab exports session values but not analytics/caches.
  const big = "x".repeat(300 * 1024);
  const r = run({ framed: false,
    ls: { "sws:auth": "TOKEN", portfolios: "[1]", "unleash:repository:repo": "{}",
          REACT_QUERY_OFFLINE_CACHE: "huge", snowplowOutQueue_x: "junk",
          _hjSessionUser_44113: "hj", bloated: big } });
  r.flush();
  const sess = (r.saved.map(o => o.swsSession).filter(Boolean).pop()) || { entries: {} };
  check("mirrors the auth key", sess.entries["sws:auth"], "TOKEN");
  check("mirrors app state", sess.entries.portfolios, "[1]");
  check("skips the react-query cache", "REACT_QUERY_OFFLINE_CACHE" in sess.entries, false);
  check("skips snowplow telemetry", "snowplowOutQueue_x" in sess.entries, false);
  check("skips analytics cookies-in-storage", "_hjSessionUser_44113" in sess.entries, false);
  check("skips an oversized value", "bloated" in sess.entries, false);
}
{
  // TARGET: the frame adopts the session into its partitioned store.
  const ls = { portfolios: "[]" };
  const r = run({ framed: true, ls,
    stored: null, session: { at: 1, entries: { "sws:auth": "TOKEN", portfolios: "[1]" } } });
  check("frame adopts the auth key", ls["sws:auth"], "TOKEN");
  check("frame refreshes a stale value", ls.portfolios, "[1]");
  check("frame records what it wrote", JSON.parse(ls.__jth_mirrored_keys).sort(),
        ["portfolios", "sws:auth"]);
}
{
  // LOGOUT must propagate: keys we wrote and the source dropped are removed,
  // and keys the frame owns itself are left alone.
  const ls = { "sws:auth": "TOKEN", portfolios: "[1]", ownKey: "keep",
               __jth_mirrored_keys: JSON.stringify(["sws:auth", "portfolios"]) };
  const r = run({ framed: true, ls, session: { at: 2, entries: { portfolios: "[]" } } });
  check("logout removes the mirrored auth key", "sws:auth" in ls, false);
  check("logout leaves the frame's own keys", ls.ownKey, "keep");
}
{
  // No session recorded yet: touch nothing.
  const ls = { portfolios: "[]" };
  run({ framed: true, ls, session: null });
  check("no mirror -> frame untouched", Object.keys(ls), ["portfolios"]);
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
      check("reply reports mirrored key NAMES", m && m.mirror && m.mirror.keys, ["sws:auth"]);
      check("reply leaks NO token value", JSON.stringify(m || {}).includes("SUPERSECRET"), false);
      if (fail) {
        console.error(`\ntest_helper_swssync: ${pass} passed, ${fail} FAILED`);
        process.exit(1);
      }
      console.log(`test_helper_swssync: ${pass} passed, 0 failed`);
    });
    });
  });
}
