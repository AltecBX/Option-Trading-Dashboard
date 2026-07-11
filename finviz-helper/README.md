# JerryTrade Finviz Helper

Lets **dashboard.jerrytrade.com** display Finviz inside its Finviz tab.

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

## Login notes

- Log into Finviz Elite inside the embedded view once; the session persists
  the way Finviz and your browser normally allow.
- If the login doesn't stick, your browser is blocking third-party cookies.
  Either allow cookies for `finviz.com`/`elite.finviz.com` in the browser's
  cookie settings, or log in at elite.finviz.com in a normal tab first, then
  reload the dashboard.

Firefox/Safari and all mobile browsers do not support this kind of
extension; on those, the Finviz tab explains the limitation.
