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

Step 7 is not optional: **Cloudflare Access only applies to traffic that
goes through Cloudflare, which is only while the cloud is orange.** Grey
means anyone who knows `dashboard.jerrytrade.com` reaches the dashboard with
no login at all. Keep that window to minutes, not hours.

**Orange is necessary but it is not sufficient — see the open item below.**

**How to check it worked.** From any terminal:

```
curl -sS -o /dev/null -w "%{http_code}\n" https://dashboard.jerrytrade.com/
```

**What counts as good depends on which step you are on**, because the grey
cloud takes Cloudflare Access out of the path:

| Where you are | Good | Why |
|---|---|---|
| **Step 6** — cloud still GREY | **`200`** | Railway is answering you directly with a working certificate. There is no Access login in the way, so you get the app, not a redirect. |
| **Step 7** — cloud back ORANGE | **`302`** | Cloudflare Access is bouncing a signed-out visitor to the login. |

Either way, `000` with *"certificate has expired"* means the renewal has not
gone through yet, and `526` means it is still broken. A `200` at step 6 is
the result you are waiting for — do not read it as a failure and start
removing the domain again.

**To read the certificate itself**, ask the Railway host for the custom
domain by name. That is exactly what Cloudflare does, so it sees the
certificate Cloudflare judges:

```
openssl s_client -connect THE-RAILWAY-ADDRESS:443 \
  -servername dashboard.jerrytrade.com < /dev/null 2>/dev/null \
  | openssl x509 -noout -subject -dates
```

`THE-RAILWAY-ADDRESS` is the `*.up.railway.app` value on Railway's
Networking page. `notAfter` is the expiry.

> **Not** `curl -v https://dashboard.jerrytrade.com`. While the cloud is
> orange that inspects **Cloudflare's** edge certificate, which is always
> healthy — it will happily print `SSL certificate verify ok.` at the exact
> moment Cloudflare is returning 526, because the certificate that expired
> is Railway's, on the hop Cloudflare makes behind the scenes. That command
> only tells you about Railway while the cloud is grey.

**Do not "fix" it by turning off Full (strict).** SSL/TLS → *Full (strict)*
is the correct setting and is what catches this. Switching to plain *Full*
does make the site load again — Cloudflare simply stops checking the
certificate — so it is a fair **temporary** bridge if you need the
dashboard open during market hours. Set it back to Full (strict) once the
certificate is reissued.

### Open item: the Railway address is a back door

Putting the cloud back to orange protects `dashboard.jerrytrade.com`. It does
**not** make the dashboard private, because the Railway address still serves
it directly, and Cloudflare Access never sees that traffic. The app does not
check Access itself: `/` is served to anyone who asks, and the `config.js` it
serves carries the API key every later request needs.

Measured against the live deployment, going straight to Railway with the
custom domain as the `Host` header and no credentials whatsoever:

```
GET /            200   the dashboard loads, no login
GET /config.js   200   hands over the API key
GET /api/prefs   401   without the key
GET /api/prefs   200   with the key config.js just gave out
```

The API key is not a second lock. The page hands it to whoever asks for it,
which is fine behind a login and is the whole story without one. So today
the Railway address is the only thing standing between a stranger and the
dashboard — it is a password, not a hostname, and it sits in Cloudflare DNS
in plain sight whenever the record is grey.

Until that is closed:

- **Treat the `*.up.railway.app` address as a secret.** Do not paste it into
  issues, screenshots or commit messages.
- Keep the grey-cloud window short, because the address is publicly visible
  in DNS while it lasts.

Closing it properly means the app refusing requests that did not come
through Cloudflare — either by verifying the Access token Cloudflare adds to
every request it forwards, or by requiring a shared secret header that only
Cloudflare sends. Neither is done yet.

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
