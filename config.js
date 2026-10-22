/* config.js — runtime config for the Jerry's Setup dashboard.
 *
 * Empty defaults mean LOCAL DEV mode: the page makes relative requests
 * to /api/... which the same Python server answers from localhost:8765
 * or wherever you started it.
 *
 * The API key is NOT kept here (v5.49). When the API_KEY env var is set
 * (Railway), the server adds it to this file as it serves it, so the key
 * never goes into the repo and rotating it is one change on Railway.
 *
 * Note on apiKey: it ships to every browser that loads the page, so it is
 * not a secret from whoever can load the page. What keeps strangers from
 * loading the page is Cloudflare Access on the dashboard's domain, and
 * ORIGIN_SECRET on Railway for the Railway address (OPERATIONS.md).
 */
window.__APP_CONFIG = {
  // apiBase stays empty: Railway serves the page AND the API from the same
  // origin, so relative /api/... requests are correct (and local dev too).
  apiBase: "",
  // Filled in by the server from API_KEY (see above). Sent as X-API-Key on
  // every API call. Empty locally, where the server does not ask for one.
  apiKey: "",
};
