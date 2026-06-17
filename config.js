/* config.js — runtime config for the Jerry's Setup dashboard.
 *
 * Empty defaults mean LOCAL DEV mode: the page makes relative requests
 * to /api/... which the same Python server answers from localhost:8765
 * or wherever you started it.
 *
 * AFTER YOU DEPLOY TO RAILWAY, edit this file and fill in:
 *   apiBase  — e.g. "https://your-app.up.railway.app"
 *   apiKey   — must match the API_KEY env var set on Railway
 *
 * Then commit + push. Vercel will auto-redeploy with the new config.
 *
 * Note on apiKey: this string ships to every browser that loads the
 * page, so it's not a secret. It's a soft barrier against random
 * scanners hitting your Railway URL. For real auth you'd need a login
 * flow. Pair the API key with a strict ALLOWED_ORIGIN env var on
 * Railway (set to your Vercel URL) for a second layer of defense.
 */
window.__APP_CONFIG = {
  apiBase: "",
  apiKey: "",
};
