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

## Fix "Invalid SSL certificate" / Error 526 (the site is down)

**What you see:** a Cloudflare page saying **Invalid SSL certificate ·
Error 526**, with Browser ✅, Cloudflare ✅, and your host ❌.

**What it means:** the app is fine. Railway's security certificate for
`dashboard.jerrytrade.com` has expired, and Cloudflare refuses to hand
traffic to a server it cannot verify. Nothing to do with the code, a
deploy, or anything you changed.

**Why it keeps happening:** that certificate lives on Railway and has to be
renewed about every 90 days. To renew it, Railway has to be reachable at
your domain name — but the **orange cloud** in Cloudflare stands in the
way, and Cloudflare is refusing to connect *because the certificate is
bad*. It is stuck in a loop it cannot leave on its own. The fix is to step
Cloudflare out of the path for a few minutes.

**The fix — about five minutes:**

1. **Cloudflare → DNS → `dashboard` row → Edit.** Click the **orange cloud
   so it turns grey** ("DNS only"). Save.
2. **Railway → `web` → Settings → Networking.** Next to
   `dashboard.jerrytrade.com` click the **trash icon** to remove it. Then
   **+ Custom Domain** and type `dashboard.jerrytrade.com` again.
3. Railway shows the DNS records it wants. **The CNAME value will be a NEW
   address** — a fresh one is minted every time the domain is added, so
   expect it to differ from what is in Cloudflare. Copy it.
4. **Cloudflare → DNS → `dashboard` → Edit.** Paste Railway's new value
   into **Content**. Leave it **grey** for now. Save.
5. Also compare the `_railway-verify.dashboard` **TXT** record against the
   value in Railway's dialog. Usually unchanged; if it differs, paste
   Railway's in.
6. Wait a minute. Check it works (below).
7. **Cloudflare → DNS → `dashboard` → Edit → click the cloud back to
   ORANGE.** Save. Check it works again.

Step 7 is not optional: **Cloudflare Access only guards the site while the
cloud is orange.** Grey means anyone with the address reaches the login-free
dashboard. Keep that window to minutes, not hours.

**How to check it worked.** From any terminal:

```
curl -sS -o /dev/null -w "%{http_code}\n" https://dashboard.jerrytrade.com/
```

- `302` → good. Cloudflare is bouncing you to the Access login, which is
  what should happen to anyone not signed in.
- `000` with *"certificate has expired"* → the certificate is still bad;
  the renewal has not gone through yet.
- `526` → still broken.

To see the certificate error itself, which is the thing that proves the
diagnosis rather than guessing at it:

```
curl -sSv https://dashboard.jerrytrade.com/ 2>&1 | grep -i certificate
```

Healthy looks like `SSL certificate verify ok.` Broken looks like
`SSL certificate problem: certificate has expired`.

**Do not "fix" it by turning off Full (strict).** SSL/TLS → *Full (strict)*
is the correct setting and is what catches this. Switching to plain *Full*
does make the site load again — Cloudflare simply stops checking the
certificate — so it is a fair **temporary** bridge if you need the
dashboard open during market hours. Set it back to Full (strict) once the
certificate is reissued.

**One trap worth knowing.** Railway keeps old addresses alive for a while,
and an old one will still answer a plain request while having no valid
certificate. So "the old address responds" proves nothing. The only
address that counts is the one Railway's Networking page shows **right
now**.

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
  `CNAME` pointing at whatever address Railway's Networking page currently
  shows, set to **Proxied / orange cloud**). Orange is required: Cloudflare
  Access — the login that keeps the dashboard private — only applies while
  the traffic goes through Cloudflare. The only time it should be grey is
  the few minutes of the certificate fix above.

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
| Site shows **Error 526** | grey cloud → re-add domain in Railway → new CNAME → orange cloud |
| Update the app | push to GitHub `main` |
| Run it locally again | `python options_dashboard.py --serve --port 8765` |
