# JerryTrade Site Helper

Lets **dashboard.jerrytrade.com** display Finviz, TradingView and Unusual Whales inside their dashboard tabs.

Finviz sends an `X-Frame-Options: SAMEORIGIN` header, which makes every
browser refuse to render it inside another website. This helper — installed
and controlled entirely by you — uses Chrome's official
`declarativeNetRequest` API to lift that restriction for **one case only**:
Finviz loaded *as a sub-frame* by *dashboard.jerrytrade.com*.

What it can and cannot do:

- ✅ Allows the JerryTrade Finviz tab to render finviz.com / elite.finviz.com.
- ✅ Finviz loads directly from Finviz's servers with **your** cookies — your
  real Elite login, saved screens, watchlists, portfolios and settings.
- ❌ No access to page content, browsing history, keystrokes, or any other
  site. The only "code" is a one-line announcement to the dashboard that the
  helper is installed.
- ❌ Nothing is proxied, scraped, stored, or sent anywhere.

## Install (Chrome / Edge / Brave — desktop)

1. Download `finviz-helper.zip` from the dashboard's Finviz tab and unzip it.
2. Open `chrome://extensions` (or `edge://extensions`).
3. Turn on **Developer mode** (top right).
4. Click **Load unpacked** and select the unzipped `finviz-helper` folder.
5. Reload the JerryTrade dashboard — the Finviz tab now shows Finviz live.

## Updating

Already installed an older version? Download the new zip, replace the
unzipped folder's contents, then click the ↻ reload icon on the extension's
card at `chrome://extensions` (or remove + Load unpacked again).

- v2.7 — IMPORTANT update for everyone.
  Chrome: removes the v2.5 Storage Access click-handler, which could
  reload an embedded frame when you clicked inside it — losing unsaved
  TradingView changes. Nothing else about Chrome behavior changes.
  Comet / Brave / forks: v2.6's fallback keyed off a missing
  contentSettings API, but some forks ship the API and still block the
  cookies — so it never armed. v2.7 detects blocking EMPIRICALLY: a
  passive observer watches the dashboard's own frame requests (presence
  of a Cookie header only — values are never read into logs or
  messages); if the browser sent one cookieless while you're logged in,
  that site's cookie-header fallback arms automatically and the frame
  reloads itself signed in. Uses one new permission: "webRequest"
  (observation only — nothing is modified with it).
- v2.6 — Comet / Brave / forks: the login FINALLY sticks — no settings, no
  prompts. v2.5's Storage Access API turned out not to help there: these
  browsers send the frame's own page request without cookies no matter
  what, so the site renders logged out (and the grant-then-reload cycle
  could reload the frame on every click — fixed too). v2.6 instead mirrors
  each site's own cookie jar into an in-memory declarativeNetRequest
  SESSION rule that attaches the Cookie header on dashboard-initiated
  frame requests — exactly what a first-party visit would send. Active
  ONLY in browsers missing the contentSettings API; Chrome is untouched.
  Cookie values stay inside your browser (in memory, never on disk), are
  never logged, and never leave it. After updating: sign in once in a
  normal tab (or the TV tab's 'Sign in ↗' popup) and the embedded views
  stay signed in.
- v2.5 — Comet (and any browser without a third-party-cookie exception
  setting): the in-frame scripts now use the standards-based Storage Access
  API. After updating: open the embedded site, CLICK ANYWHERE inside it
  once, and approve the cookie/storage prompt if the browser shows one.
  The grant persists (~30 days), the frame reloads itself, and the login
  sticks from then on. No settings hunt required.
  (Superseded by v2.6 — this approach did not work in Comet.)
- v2.4 — non-Chrome Chromium browsers (Comet, Brave, ...): these often lack
  the contentSettings API, so the helper can't register the third-party-
  cookie exception itself and the embedded logins won't stick until you add
  it by hand. The helper now reports this to the dashboard, which shows a
  'cookies: browser setting needed' chip with exact steps (see below).
- v2.3 — TradingView login persistence, done right. v2.2 stopped touching
  TV cookies entirely, which fixed the error pages but meant the browser
  wouldn't send TV's SameSite-restricted auth cookies inside the frame —
  you were asked to log in on every page. v2.3 rewrites ONLY TradingView's
  named auth cookies (sessionid, sessionid_sign, device_t, csrftoken) to
  SameSite=None; Secure. Anti-abuse cookies are never touched, so the
  corruption cannot recur. Tip: sign in via the TV tab's 'Sign in ↗'
  popup (a normal first-party page), then the embedded view stays signed
  in everywhere.
- v2.2 — TradingView repair. v2.0/2.1 rewrote TV cookies, which corrupted
  their anti-abuse cookie state and made EVERY TradingView page show 'Back
  before you know it' inside the embed. v2.2 stops touching TV cookies
  entirely (TV works framed without any rewriting), clears the damaged
  tradingview.com cookie jar once on update (log in once afterwards), adds
  a Repair-session command the dashboard's TV tab can invoke, and hardens
  the remaining rewrites (anti-abuse cookie skip-list, partitioned cookies
  left alone, delete-before-set so duplicates can't form).
- v2.1 — Unusual Whales support: login cookie handling (SameSite upgrade +
  third-party-cookie exception + install sweep) now covers
  unusualwhales.com, plus in-frame ticker sync from the /stock/SYMBOL URL.
  UW doesn't block framing, so the embed works even before updating — the
  update makes the LOGIN persist inside the frame.
- v2.0 — TradingView support: the same frame-unlock, cookie exception and
  SameSite upgrade now cover tradingview.com, and a tiny in-frame script
  reports the active chart SYMBOL to the dashboard for two-way ticker sync
  (US-equity symbols only; nothing else is read or sent).
- v1.4 — Finviz's own Theme (light/dark) toggle now works inside the
  embedded view. Finviz saves the theme via a SameSite=Lax cookie that
  browsers reject in a cross-site frame; the helper intercepts the toggle
  and writes the same preference cookie through the extension's cookies
  API instead. One named cookie ("chartsTheme"), nothing else.
- v1.3 — click-to-research: when you click a stock inside the embedded
  Finviz (screener, maps, news), the dashboard's global ticker switches to
  it automatically. A tiny script inside embedded Finviz pages reports the
  SYMBOL (and nothing else) to the dashboard; it does nothing in normal
  Finviz tabs.
- v1.2 — login now STICKS inside the embedded view. Finviz's session cookies
  are SameSite=Lax, which browsers refuse to use inside a cross-site frame;
  the helper now (a) registers a third-party-cookie exception for finviz
  under the dashboard (the same exception you could add by hand in Chrome's
  settings) and (b) rewrites finviz cookies' METADATA to SameSite=None;
  Secure so the frame can use them. Cookie values never leave your browser
  and are never logged. Requires two new permissions: "cookies" and
  "contentSettings" — Chrome will ask you to approve them on update.
- v1.1 — navigation *inside* the embedded frame (Login, menu links) is now
  covered too; v1.0 only allowed the initial page load.

## Login notes

- Log into Finviz Elite inside the embedded view once (tick "Remember me");
  with v1.2 the session persists across ticker changes, reloads and visits,
  and it is the SAME session as a normal finviz.com tab.
- Comet / Brave / other Chromium forks: if embedded logins don't persist,
  add the third-party-cookie exception by hand (the helper can't register
  it in these browsers): open Settings → search "third-party cookies" →
  under "Sites allowed to use third-party cookies" click Add and enter
  dashboard.jerrytrade.com (tick "Include third-party cookies" if shown).
  Also make sure the browser isn't set to clear cookies on close. Brave
  additionally: Shields → Cookies → "Allow all" for the dashboard site.

## Diagnostics (temporary)

The helper logs cookie NAMES, domains, SameSite attributes, rule matches
and content-setting results — never values, passwords, or tokens. See them
in the dashboard page's DevTools console as "[finviz-helper] …" lines, or in
the extension's own console (chrome://extensions → Inspect views: service
worker).

Firefox/Safari and all mobile browsers do not support this kind of
extension; on those, the Finviz tab explains the limitation.
