# Jerry Dashboard — Simple Operations Guide

The dashboard runs in the cloud on **Railway** and is always on, even with
the Mac off. Live at: **https://dashboard.jerrytrade.com**

---

## The one thing to check: where's my data coming from?

Open this in any browser:

```
https://dashboard.jerrytrade.com/api/data_source
```

- `"last_source": "schwab"`  → ✅ real-time Schwab prices (good)
- `"last_source": "yfinance"` → ⚠️ fell back to delayed data — fix Schwab below

---

## Fix Schwab (when it falls back to yfinance)

This happens if the Schwab token expires. Three steps:

1. **On your Mac**, mint a fresh token:
   ```
   jerry auth
   ```
   (Log in, copy the redirected `127.0.0.1:8182` URL, paste it back.)
   Then show it so you can copy it:
   ```
   cat ~/.jerry-dashboard/schwab_token.json
   ```

2. **In Railway** → `web` service → **Console** tab, paste it in:
   ```
   cat > /data/schwab_token.json
   ```
   Press Enter, paste the token, press Enter, then **Ctrl + D**.

3. **Restart**: Railway → `web` → **Deployments** → top one → **⋮** → **Restart**.

Check `/api/data_source` again — should say `"schwab"`.

> Keep `jerry` **stopped** on the Mac (`jerry stop`). Only Railway should use
> Schwab, or the two fight over the token.

---

## How updates work

Edit code → push to GitHub `main` → Railway redeploys automatically. Nothing
else to do.

---

## Where things live on Railway (`web` service)

- **Variables tab** — settings & secrets:
  - `JERRY_DATA_DIR = /data` (where the watchlist + token are saved)
  - `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET` (your Schwab app credentials)
- **Volume** (`web-volume`, mounted at `/data`) — keeps the watchlist and
  Schwab token forever, across restarts.
- **Custom domain** — `dashboard.jerrytrade.com` (DNS lives in Cloudflare as a
  `CNAME`, set to **DNS only / grey cloud**).

---

## Watch the cost

Railway shows your remaining credit in the top bar. If it runs low and you
want the app to stay up, add a payment method / upgrade the plan so it
doesn't pause.

---

## Quick reference

| I want to… | Do this |
|------------|---------|
| Open the app | https://dashboard.jerrytrade.com |
| Check data source | open `…/api/data_source` |
| Fix Schwab | `jerry auth` → paste token in Railway Console → Restart |
| Update the app | push to GitHub `main` |
| Run it locally again | `python options_dashboard.py --serve --port 8765` |
