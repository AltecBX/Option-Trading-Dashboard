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
means a request to `dashboard.jerrytrade.com` goes straight to Railway with
no login. Once the origin check is on (below) the app refuses those with a
403, but the site does not work for you either until the cloud is orange
again. Keep that window to minutes, not hours.

**Orange alone does not make the dashboard private: the Railway address is
a second door. See "Closing the Railway back door" below.**

**How to check it worked.** From any terminal:

```
curl -sS -o /dev/null -w "%{http_code}\n" https://dashboard.jerrytrade.com/
```

**What counts as good depends on which step you are on**, because the grey
cloud takes Cloudflare Access out of the path:

| Where you are | Good | Why |
|---|---|---|
| **Step 6** — cloud still GREY | **`200`**, or **`403`** once the origin check is on | Railway is answering you directly with a working certificate. There is no Access login in the way, so you get the app (`200`), or, with `ORIGIN_SECRET` set, the app's own refusal (`403`, `forbidden: origin check failed`): Cloudflare is not in the path to add the secret. Either one means the certificate works. |
| **Step 7** — cloud back ORANGE | **`302`** | Cloudflare Access is bouncing a signed-out visitor to the login. |

Either way, `000` with *"certificate has expired"* means the renewal has not
gone through yet, and `526` means it is still broken. A `200` (or the app's
`403`) at step 6 is the result you are waiting for — do not read it as a
failure and start removing the domain again.

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

### Closing the Railway back door

**Why.** Putting the cloud back to orange protects `dashboard.jerrytrade.com`.
It did **not** make the dashboard private, because the Railway address
served it too, and Cloudflare Access never sees that traffic. Measured
before v5.49, going straight to Railway with no credentials whatsoever:

```
GET /            200   the dashboard loads, no login
GET /config.js   200   hands over the API key
GET /api/prefs   401   without the key
GET /api/prefs   200   with the key config.js just gave out
```

**What the app does now (v5.49).** When the Railway variable `ORIGIN_SECRET`
is set, the app answers only requests that carry that same value in a
header called `X-Origin-Secret`. You set up Cloudflare to add that header
to every request it passes on to Railway, which it only does after Access
has let the request in. A request that goes to the Railway address
directly has no header, so it gets `403 forbidden: origin check failed`:
no page, no `config.js`, no API. Until `ORIGIN_SECRET` is set, nothing
changes, so until then **treat the `*.up.railway.app` address as a
secret**: keep it out of issues, screenshots and commit messages.

**Do it in this order: Cloudflare first, Railway second.** The other way
round, the app starts refusing everything before Cloudflare sends the
header, and the dashboard is locked until you finish.

1. **Make a secret.** In a Terminal: `openssl rand -hex 32`. Copy the 64
   characters it prints into your password manager. Don't paste it
   anywhere else: not in GitHub, not in a chat, not in a screenshot.
2. **Cloudflare adds it.** dash.cloudflare.com → `jerrytrade.com` →
   **Rules → Overview → Create rule → Request Header Transform Rule**
   (on the older dashboard: **Rules → Transform Rules → Modify Request
   Header → Create rule**).
   - Rule name: `Origin secret to Railway`
   - *If incoming requests match…* → **Custom filter expression**:
     Field **Hostname**, Operator **equals**, Value
     `dashboard.jerrytrade.com`
   - *Then…* → **Set static** → Header name `X-Origin-Secret` → Value: the
     secret from step 1
   - **Deploy.** Nothing changes yet: the app ignores the header until
     step 3.
3. **Railway checks it.** Railway → `web` → **Variables → New Variable** →
   name `ORIGIN_SECRET`, value: the same secret → save. Railway redeploys
   (about a minute). The start of the new deploy's log says
   `Origin check: ON, X-Origin-Secret required`.
4. **Check it worked.**
   - Open https://dashboard.jerrytrade.com: it loads as before, after the
     Access login.
   - The Railway address now refuses. `THE-RAILWAY-ADDRESS` is the
     `*.up.railway.app` value on Railway's Networking page:
     ```
     curl -s -o /dev/null -w "%{http_code}\n" https://THE-RAILWAY-ADDRESS/
     curl -s https://THE-RAILWAY-ADDRESS/config.js
     ```
     The first prints `403`, the second `forbidden: origin check failed`.
   - The watchman (below) stays green: it goes through Cloudflare, so its
     requests get the header too.
5. **Then rotate the API key.** It was committed to `config.js` in this
   repo until v5.49, so it is public. Now it's one change on Railway:
   SECURITY_NOTES.md, "Rotating `API_KEY`".

**If the dashboard itself says `forbidden: origin check failed`,** the
Cloudflare rule and the Railway variable don't match (a character missing,
a space pasted in, or the rule's hostname not matching). The quickest way
back is to delete `ORIGIN_SECRET` on Railway, which puts things back as they
were, then fix the rule and add the variable again. The watchman reports
this as `ORIGIN SECRET`.

**Changing the secret later, without a gap:** set `ORIGIN_SECRET` on Railway
to `NEW,OLD` (both values, comma between), change the Cloudflare rule's
value to `NEW`, then set `ORIGIN_SECRET` to `NEW` alone.

**Two things that must not use the Railway address once this is on:**

- **A Railway health check.** None is set up today. If you ever add one
  (Railway → `web` → Settings → Deploy → Healthcheck Path), use `/healthz`:
  it is the one path that answers without the secret, and all it says is
  `ok`. Any other path gets 403, and Railway would fail every deploy.
- **Schwab's one-click sign-in.** If `SCHWAB_REDIRECT_URI` is set, it must
  point at `https://dashboard.jerrytrade.com/...`, not at the Railway
  address, or Schwab's redirect back is refused.

**Why a shared header and not Access's own token.** Cloudflare also adds a
signed Access token (`Cf-Access-Jwt-Assertion`) to the requests it lets
through. Checking it properly means verifying an RSA signature against keys
the app would download from Cloudflare: a new dependency (or hand-written
crypto) and a network call on the request path. The header does the same
job here with neither: only Cloudflare knows the value, and Cloudflare only
forwards a request to Railway after Access has passed it. Not done; it
could be added later as a second check.

**One trap worth knowing.** Railway keeps old addresses alive for a while,
and an old one will still answer a plain request while having no valid
certificate. So "the old address responds" proves nothing, and neither does
"the old address says 403". The only
address that counts is the one Railway's Networking page shows **right
now**.

---

## The watchman (so you find out before you notice)

On 15 September the site was down from before the open until mid-morning and
nothing said so — it was found by opening the page. A check now runs on
**GitHub every ten minutes**, deliberately not on Railway: a watchman living
in the building he is guarding is no use on the morning the building is the
problem.

**It asks three questions.**

1. **Can it reach the app?** Not "did something answer" — it carries the
   Cloudflare Access service token and insists on the app's own JSON. This
   matters: Access answers a stranger with a redirect to its login *before*
   Cloudflare ever contacts Railway, so a simpler check would have reported
   everything fine throughout the outage it was built for.
2. **Is a stranger still turned away?** It asks again with no credentials and
   requires to be refused. If the site ever answers a stranger — a grey cloud
   left on after the certificate fix — that is reported as loudly as an
   outage. **It only watches the `dashboard.jerrytrade.com` door.** The
   Railway address is a second door it cannot see; `ORIGIN_SECRET` is what
   shuts that one (see "Closing the Railway back door" above).
3. **When does the certificate expire?** Given the origin address it warns
   **fourteen days ahead**, which turns a market-hours outage into a quiet
   evening's work. This is the one that prevents rather than reports.

**Switching it on.** GitHub → the repo → **Settings → Secrets and variables
→ Actions → New repository secret**. Until `JT_BASE` exists the check runs
and says `NOT CONFIGURED`, so it never cries wolf in a fork.

| Secret | What it is | Needed? |
|---|---|---|
| `JT_BASE` | `https://dashboard.jerrytrade.com` | **yes** |
| `CF_ACCESS_CLIENT_ID` | Cloudflare Access **service token** id | **yes** |
| `CF_ACCESS_CLIENT_SECRET` | the service token secret | **yes** |
| `JT_API_KEY` | same value as the `API_KEY` on Railway | **yes** |
| `NTFY_TOPIC` | your ntfy topic — free, no account | for push |
| `PUSHOVER_APP_TOKEN` / `PUSHOVER_USER_KEY` | Pushover instead of, or as well as, ntfy | for push |
| `RAILWAY_ORIGIN_HOST` | the `*.up.railway.app` address on Railway's Networking page | for the 14-day warning |

The service token is made in Cloudflare → **Access → Service Auth**, and the
Access policy for the dashboard has to allow it. Without push configured you
still get GitHub's own "workflow failed" email, which is a slower but real
alarm.

`RAILWAY_ORIGIN_HOST` **changes every time the custom domain is re-added**,
so update it as the last step of the certificate fix above. If it is wrong or
missing the other two checks still run and the certificate line reads
`SKIPPED` rather than pretending to pass.

**What the alerts mean.**

| It says | What happened | What to do |
|---|---|---|
| `ORIGIN CERTIFICATE` | the 526 — certificate expired | the certificate fix above |
| `ORIGIN DOWN` | Cloudflare cannot reach Railway | Railway: deployed? in credit? |
| `WIDE OPEN` | **a stranger reached the dashboard** | set the DNS record back to Proxied (orange) now |
| `BLOCKED BY ACCESS` | the service token was refused | remake it in Cloudflare → Access → Service Auth and update the secrets |
| `CERTIFICATE EXPIRING` | 14 days or fewer left | do the certificate fix on a quiet evening |
| `UNREACHABLE` | no answer at all | check Railway and Cloudflare are both up |

It shouts once when something breaks, then about once an hour while it stays
broken — loud enough to catch, quiet enough to leave your phone usable.

**To check it by hand**, or to test a change to it:

```
JT_BASE=https://dashboard.jerrytrade.com \
CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... JT_API_KEY=... \
python3 monitor_uptime.py
```

Three `PASS` lines and exit `0` is healthy.

---

## How updates work

Edit code → push to GitHub `main` → Railway redeploys automatically. Nothing
else to do.

---

## Where things live on Railway (`web` service)

- **Variables tab** — settings & secrets:
  - `JERRY_DATA_DIR = /data` (where the watchlist + token are saved)
  - `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET` (your Schwab app credentials)
  - `API_KEY` (the page gets it through `config.js`; nothing to commit)
  - `ORIGIN_SECRET` (shuts the Railway address; see "Closing the Railway
    back door")
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
| Site says **forbidden: origin check failed** | the Cloudflare rule and Railway's `ORIGIN_SECRET` don't match; delete the variable to get back in, then fix |
| Rotate the API key | Railway → Variables → `API_KEY` → new value (SECURITY_NOTES.md) |
| Check the watchman is working | GitHub → **Actions** → **uptime** → newest run |
| Update the app | push to GitHub `main` |
| Run it locally again | `python options_dashboard.py --serve --port 8765` |
