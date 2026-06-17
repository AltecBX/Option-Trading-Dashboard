# Always-On Deploy — Railway + dashboard.jerrytrade.com

Goal: run the dashboard in the cloud so it stays up with your Mac **off**,
keeping the same URL `https://dashboard.jerrytrade.com`.

Today that domain points (via a Cloudflare Tunnel) at the `jerry` server
running on your MacBook. We're going to point it at Railway instead. When
this is done you can quit `jerry` and close the Mac — the URL keeps working.

The repo is already deploy-ready: `Procfile` runs the server, it reads the
`PORT` env var Railway provides, and `.python-version` pins Python 3.11.

---

## Step 1 — Deploy the app to Railway (~3 min)

1. Go to https://railway.app and sign in **with GitHub**.
2. **New Project** → **Deploy from GitHub repo** → authorize, pick
   `AltecBX/Option-Trading-Dashboard`.
3. Railway auto-detects Python, installs `requirements.txt`, and runs the
   `Procfile`. Wait for the first deploy to go green (~2 min).
4. Open the **Settings** tab → **Networking** → **Generate Domain**.
   Railway gives you a temporary URL like
   `https://option-trading-dashboard-production-xxxx.up.railway.app`.
5. Open that URL in a browser. You should see the dashboard. (This proves
   the cloud copy works before we touch DNS.)

> If Yahoo data comes back empty / shows 429s, that's the datacenter-IP
> rate limit — ping me and we'll add caching or switch to the Schwab feed.

---

## Step 2 — Add the custom domain on Railway (~1 min)

1. Still in **Settings → Networking**, click **Custom Domain**.
2. Enter `dashboard.jerrytrade.com`.
3. Railway shows you a **CNAME target** to copy — something like
   `xxxx.up.railway.app`. Keep this tab open; you need that value next.

---

## Step 3 — Repoint the domain in Cloudflare (~2 min)

Your `jerrytrade.com` DNS is managed in Cloudflare (that's how the tunnel
works). We swap the `dashboard` record from the tunnel to Railway.

1. Go to https://dash.cloudflare.com → select `jerrytrade.com` → **DNS**.
2. Find the existing record for **`dashboard`** (it'll be a CNAME pointing
   at something like `<uuid>.cfargotunnel.com`). Click **Edit**.
3. Change **Target** to the Railway CNAME target from Step 2.
4. Set **Proxy status** to **DNS only** (grey cloud), not Proxied (orange).
   Railway needs to terminate TLS for the cert to issue. You can switch it
   back to Proxied later once the Railway cert is active, if you want
   Cloudflare in front.
5. **Save.**

DNS propagates in a few minutes. Railway's Custom Domain panel will flip
to a green "active" state once it sees the record and issues the cert.

---

## Step 4 — Verify, then cut the Mac loose

1. Open `https://dashboard.jerrytrade.com` — should load from Railway now.
2. On your Mac, stop the local server + tunnel so nothing conflicts:
   ```
   jerry stop
   ```
   (or quit however `jerry` shuts down — the tunnel PID from `jerry update`).
3. Close the MacBook. Re-open `https://dashboard.jerrytrade.com` from your
   phone or another computer. It should still work. Done — no Mac required.

---

## Step 5 (optional but recommended) — Persist your watchlist

Railway's filesystem is **ephemeral**: every redeploy wipes
`~/.jerry-dashboard/`, so your watchlist/notes would reset. To keep them:

1. Railway project → **Variables** → add
   `JERRY_DATA_DIR=/data`
2. Railway project → service → **Settings → Volumes** → **New Volume**,
   mount path `/data`.
3. Redeploy. Now watchlist/journal live on the volume and survive deploys.

To carry over your current watchlist, copy the contents of
`~/.jerry-dashboard/watchlist.json` from your Mac into the app once it's
running on the volume (or paste symbols back in via the UI).

---

## Optional — lock it down

Right now the URL is public (same as your Mac tunnel is today). If you want
a soft gate:

- Railway **Variables** → `API_KEY=some-random-string`
- Then set the same value in `config.js` (`apiKey: "some-random-string"`),
  commit, push — Railway auto-redeploys. The served frontend will send the
  key; random scanners without it get `unauthorized`.

---

## Updating later

```
git push
```
Railway auto-redeploys on every push to the default branch. Same as before,
minus the Mac.

## Cost

Railway free tier is ~$5/mo of credits; this app runs ~$0.10/day idle,
~$0.30/day active. Set a budget alert in Railway settings so there are no
surprises.
