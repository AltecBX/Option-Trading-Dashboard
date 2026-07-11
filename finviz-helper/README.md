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
- Brave: also set Shields → Cookies to "Allow all" for the dashboard site,
  as Shields applies its own blocking above Chrome's.

## Diagnostics (temporary)

The helper logs cookie NAMES, domains, SameSite attributes, rule matches
and content-setting results — never values, passwords, or tokens. See them
in the dashboard page's DevTools console as "[finviz-helper] …" lines, or in
the extension's own console (chrome://extensions → Inspect views: service
worker).

Firefox/Safari and all mobile browsers do not support this kind of
extension; on those, the Finviz tab explains the limitation.
