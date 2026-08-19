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

## The volume is not optional any more

The Investment tab records something every trading day that **cannot be got
back**: the end-of-day option chain for each followed ticker, the valuation
state behind each recommendation, and the long-dated contracts around the
money. There is no source anywhere this app can reach that sells an option
chain as it stood on a past date. A trading day that goes uncaptured stays
uncaptured, and a day that gets erased is gone the same way.

All of it is written under `JERRY_DATA_DIR`. **If the volume is not
attached, every day of it is erased on the next deploy.**

**How to check, in ten seconds:** open the Investment tab → **PRODUCTION
READINESS** → read the first line.

- **READY TO ACCUMULATE DATA** — *"Data directory sits on a different
  filesystem from the container root. Persistent volume confirmed."* The
  volume is attached and working. Nothing to do.
- **BLOCKED — PERSISTENT STORAGE NOT CONFIRMED**, with either
  *"Data directory is on the container's own filesystem. Prospective history
  will be lost on redeploy."* or *"Persistent volume could not be confirmed.
  Treating production collection as NOT READY."* — **this is the one to act
  on.** Attach the volume in Railway and set `JERRY_DATA_DIR` to its mount
  point, then redeploy. Everything captured before that is already lost, and
  everything captured after is safe.

An unconfirmed volume is treated the same as no volume. A detached Railway
volume leaves an ordinary `/data` directory on the container's own disk
behind, which looks right and is not, so the panel will not call it safe
until it can see the mount.

The same panel lists the exact directory each thing is written to — the
investment history, the option chains, the long-dated contracts, the
capture-health log and the configuration archive — so there is never a
question of where to look.

The same panel says how much a year of it will cost in disk: about
**600 megabytes a year** at forty followed tickers, and about **1.2
gigabytes** once the chain store reaches its 500-day limit. Size the volume
above that.

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
| Check the Investment data will survive a deploy | Investment tab → **PRODUCTION READINESS** |
| Check yesterday's capture actually ran | Investment tab → **Data readiness** |
| Fix Schwab | `jerry auth` → paste token in Railway Console → Restart |
| Update the app | push to GitHub `main` |
| Run it locally again | `python options_dashboard.py --serve --port 8765` |
