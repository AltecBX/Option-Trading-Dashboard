# Security Notes

Pragmatic notes for this deployment (static frontend on Vercel + Python backend
on Railway). This is a personal trading dashboard, not multi-tenant — scoped
accordingly.

## Current model (as implemented)
- **Browser API key is NOT a secret.** `config.js` ships `apiKey` to every
  browser that loads the page. It's a soft gate against random scanners, not
  authentication. Since v5.49 it is not committed: the server adds Railway's
  `API_KEY` to `config.js` as it serves it.
- **Origin check** (v5.49, `DashboardHandler.parse_request`): when the
  `ORIGIN_SECRET` env var is set, every request must carry it in the
  `X-Origin-Secret` header, which a Cloudflare Transform Rule adds; anything
  else gets a bare 403 (`forbidden: origin check failed`), page, `config.js`
  and API alike. Only `GET`/`HEAD /healthz` is exempt, and it says `ok` and
  nothing else. This is what shuts the `*.up.railway.app` back door
  (OPERATIONS.md, "Closing the Railway back door"). Unset, nothing changes.
- **Backend key check** (`options_dashboard.py::_check_api_key`): enforced only
  when the `API_KEY` env var is set (prod). Unset locally → no enforcement.
  Requests must send `X-API-Key` matching `API_KEY`.
- **CORS** (`_cors_headers`): `Access-Control-Allow-Origin` = `ALLOWED_ORIGIN`
  env (defaults to `*`). Methods `GET, PUT, OPTIONS`; headers `X-API-Key,
  Content-Type`.
- **Real secrets** (`SCHWAB_*`, `UW_API_KEY`, `PUSHOVER_*`) are read from
  `os.environ` only — none are committed. Verified: no tokens in the repo.
- **Persistence** lives in `JERRY_DATA_DIR` (`/data` volume on Railway), outside
  the repo. `watchlist_seed.json` (committed) contains only ticker symbols — not
  sensitive.

## Checklist
### Railway (backend)
- [ ] Set `API_KEY` to a long random string (gates the API).
- [ ] Set `ORIGIN_SECRET` to the same long random string the Cloudflare
      Transform Rule sends as `X-Origin-Secret` (OPERATIONS.md, "Closing the
      Railway back door"). Add the Cloudflare rule FIRST, then this.
- [ ] Set `ALLOWED_ORIGIN` to the exact Vercel URL (e.g.
      `https://dashboard.jerrytrade.com`) — **not** `*` in production.
- [ ] Set provider secrets as env vars only: `SCHWAB_*`, `UW_API_KEY`,
      `PUSHOVER_APP_TOKEN`/`PUSHOVER_USER_KEY`, `NTFY_TOPIC` as used.
- [ ] Mount a **persistent volume** and point `JERRY_DATA_DIR` at it (e.g.
      `/data`) so the watchlist/prefs survive deploys. (Confirmed present via
      `/api/watchlist/diag`.)
- [ ] Confirm the process runs with `--serve`.

### Vercel (frontend)
- [ ] `config.js` `apiBase` → Railway URL; `apiKey` → same value as Railway
      `API_KEY` (remember: public, soft gate only).
- [ ] No secrets in any committed JS.

### API keys
- [ ] Rotate `API_KEY` once the origin check is on (see below). The old key
      was committed to `config.js` in this repo until v5.49, so it is public.

#### Rotating `API_KEY` (v5.49: one change, on Railway only)
1. Make a new key: in a Terminal, `openssl rand -hex 24`.
2. Railway → the service → **Variables** → `API_KEY` → paste the new value →
   save. Railway redeploys by itself (about a minute).
3. Reload the dashboard. The page gets the new key from `config.js`; nothing
   is committed. If a phone shows "unauthorized" once, pull to refresh.
4. If the uptime watchman uses the key, update the `JT_API_KEY` secret on
   GitHub (repo → Settings → Secrets and variables → Actions) to match, or
   it reports the site down.
- [ ] Provider keys (Schwab/UW/Pushover) live only in Railway env; rotate via the
      provider if leaked.

### CORS
- [ ] Production `ALLOWED_ORIGIN` is the single Vercel origin, not `*`.
- [ ] Preflight `OPTIONS` returns the same origin + allowed headers/methods.

## Future login (if multi-device auth is ever wanted)
- The browser API key cannot provide real auth. For genuine auth you'd add a
  login flow (session cookie or signed token) and protect `PUT` endpoints
  (watchlist/prefs/journal) behind it. Until then, `API_KEY` + strict
  `ALLOWED_ORIGIN` is the practical barrier. Not required for single-user use.

## Do-not
- Do not treat `config.js` `apiKey` as a secret.
- Do not commit Schwab/UW/Pushover tokens.
- Do not set `ALLOWED_ORIGIN=*` in production.
