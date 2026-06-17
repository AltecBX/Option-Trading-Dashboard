# Deployment Guide — Jerry's Setup Dashboard

End-to-end deploy: GitHub → Railway (backend) → Vercel (frontend). Time: ~45 min.

---

## Prerequisites

- Mac with the project unzipped at `~/Downloads/Sell_covered_calls_v29/`
- `git` installed (run `git --version` to check; Macs usually have it)
- A GitHub account
- A Railway account (sign up at railway.app — sign in with GitHub for fastest)
- A Vercel account (sign up at vercel.com — sign in with GitHub)

---

## Step 1 — Local git setup

In Terminal:

```
cd ~/Downloads/Sell_covered_calls_v29
git init
git add .
git commit -m "Initial dashboard commit"
git branch -M main
```

You now have a local repo. The `.gitignore` already excludes secrets, virtualenvs, and `.DS_Store`.

---

## Step 2 — Create the GitHub repo

1. Go to https://github.com/new
2. Name it whatever (e.g. `jerry-dashboard`). **Set to Private.**
3. **Do NOT** check "Add a README", "Add .gitignore", or pick a license — we already have those.
4. Click "Create repository"
5. Copy the commands GitHub shows you for "push an existing repository". They look like:

```
git remote add origin https://github.com/YOUR_USERNAME/jerry-dashboard.git
git push -u origin main
```

Run those in your Terminal. If GitHub asks for password, it wants a Personal Access Token, not your account password. Generate one at https://github.com/settings/tokens (classic, with `repo` scope).

You should see your code on github.com when done.

---

## Step 3 — Pick an API key

Pick a random string, e.g. `jerry-7K9xQp2nLm`. You'll use it twice:
- As the `API_KEY` env var on Railway
- In `config.js` as `apiKey`

I'll refer to it as `YOUR_API_KEY` below.

---

## Step 4 — Deploy backend to Railway

1. Go to https://railway.app, sign in with GitHub
2. Click "New Project" → "Deploy from GitHub repo"
3. Authorize Railway to see your repo, pick `jerry-dashboard`
4. Railway auto-detects Python, installs from `requirements.txt`, runs the `Procfile`
5. Wait ~2 minutes for the first deploy to finish

Set the env vars Railway needs:

1. Click your project → click the `web` service → click the **Variables** tab
2. Add:
   - `API_KEY` = `YOUR_API_KEY` (the random string from Step 3)
   - `ALLOWED_ORIGIN` = leave blank for now, we'll set it after Vercel deploy

Get the public URL:

1. Click the **Settings** tab → **Networking** section → **Generate Domain**
2. Railway gives you a URL like `https://jerry-dashboard-production-abcd.up.railway.app`
3. Test it: open `https://YOUR_URL.up.railway.app/api/ticker?symbol=SPY` in a new tab

If `API_KEY` is set you'll see `{"error":"unauthorized"}` — that's good, it means the gate works. If you see SPY data, the gate isn't enforcing (check the env var is saved and the service has redeployed).

To test the gate is open with a key, run in Terminal:

```
curl -H "X-API-Key: YOUR_API_KEY" "https://YOUR_URL.up.railway.app/api/ticker?symbol=SPY" | head -c 200
```

You should see JSON starting with `{"ticker":"SPY",...`.

---

## Step 5 — Deploy frontend to Vercel

1. Go to https://vercel.com, sign in with GitHub
2. Click **Add New** → **Project** → import `jerry-dashboard`
3. Framework preset: **Other** (Vercel auto-detects most things; this project is pure static)
4. Root directory: leave as `.`
5. Build / install commands: leave blank (vercel.json already says no build)
6. Click **Deploy**

After ~30 seconds Vercel gives you a URL like `https://jerry-dashboard-xyz.vercel.app`. Open it. The page loads but every API call fails — that's expected, we haven't pointed it at Railway yet.

---

## Step 6 — Wire frontend → backend

1. On your Mac, edit `config.js`:

```javascript
window.__APP_CONFIG = {
  apiBase: "https://YOUR_RAILWAY_URL.up.railway.app",
  apiKey: "YOUR_API_KEY",
};
```

2. Commit and push:

```
git add config.js
git commit -m "Point frontend at Railway"
git push
```

Vercel auto-redeploys in ~30s. Refresh your Vercel URL — you should see live data.

---

## Step 7 — Lock down CORS

Now that you have a Vercel URL, lock the backend to only accept calls from it.

1. Go back to Railway → your project → Variables tab
2. Set `ALLOWED_ORIGIN` = `https://YOUR_VERCEL_URL.vercel.app` (no trailing slash)
3. Railway redeploys automatically (~30s)
4. Refresh your Vercel page. Should still work.
5. To verify the lockdown: open https://google.com in another tab, open DevTools console, run:

```javascript
fetch("https://YOUR_RAILWAY_URL.up.railway.app/api/ticker?symbol=SPY", {
  headers: {"X-API-Key": "YOUR_API_KEY"}
}).then(r => r.json()).then(console.log)
```

You should get a CORS error in the browser. (curl will still work — that's by design; CORS is browser-only.)

---

## Step 8 — Save to iPhone home screen

1. Open your Vercel URL in iPhone Safari
2. Tap the share icon → "Add to Home Screen"
3. Icon shows up like a real app. Tap it, opens fullscreen.

---

## How to update later

Any change to local files:

```
git add .
git commit -m "what changed"
git push
```

Both Railway and Vercel auto-redeploy. Backend changes redeploy Railway, frontend changes redeploy Vercel.

---

## Troubleshooting

**"unauthorized" on every request** — `API_KEY` env var on Railway doesn't match `apiKey` in `config.js`. Common cause: typo, or you saved env var but didn't trigger a redeploy.

**CORS error in browser console** — `ALLOWED_ORIGIN` on Railway doesn't match your Vercel URL exactly. Check no trailing slash, https not http.

**Yahoo Finance returns 429 / empty data** — yfinance is rate-limited from datacenter IPs. Railway's IPs sometimes get throttled. If this becomes a problem, we add a caching layer.

**Page loads but says "Live API offline"** — `apiBase` in `config.js` is wrong, or Railway service is sleeping/crashed. Check Railway dashboard → Deployments → latest log.

**Service costs unexpected money** — Railway free tier is $5 of monthly credits. The service uses ~$0.10/day idle, ~$0.30/day active. If you exceed $5 they bill you. Set a budget alert in Railway settings.

---

## What you can change later

- **Custom domain.** Both Railway and Vercel support custom domains. Cheap to do, makes URLs prettier.
- **Real auth.** If the page ever leaks, anyone with the URL + key can use it. For real security add a login flow (separate ~3hr project).
- **Caching.** Add Redis / Cloudflare in front of Railway to reduce yfinance hits.
- **Schwab API.** Replace yfinance entirely with Schwab's official feed once you wire OAuth.
