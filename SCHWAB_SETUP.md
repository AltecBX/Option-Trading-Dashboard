# Schwab API Setup

One-time setup to wire your Schwab account into the dashboard. After
this, every price quote, option chain, and chart pulls from Schwab. If
Schwab fails or rate-limits, the dashboard auto-falls-back to yfinance.

## Prerequisites

You already have:
- A Schwab brokerage account
- An approved app at https://developer.schwab.com (Market Data Production
  product, status = Ready For Use)
- App Key and App Secret from your app's details page
- Callback URL set to `https://127.0.0.1:8182` in the app config

## Step 1 — Add credentials to .env

Create or edit `.env` in the dashboard project root:

```
SCHWAB_APP_KEY=your_app_key_here
SCHWAB_APP_SECRET=your_app_secret_here
```

If you also use the dashboard's API_KEY/ALLOWED_ORIGIN env vars from
the cloud-deploy path, keep them in the same .env file.

## Step 2 — Run the auth helper

```
cd ~/Downloads/Sell_covered_calls_v34
python3 schwab_auth.py
```

The script will:
1. Open your browser to Schwab's login page
2. After login, redirect to `https://127.0.0.1:8182/?code=...` — page
   will fail to load, that's normal
3. Copy the FULL URL from the browser bar
4. Paste it back into Terminal
5. Save `schwab_token.json`

## Step 3 — Start the server normally

```
python3 options_dashboard.py --serve --host 0.0.0.0 --port 8765
```

The server reads `schwab_token.json` on startup. Check the sidebar of
the dashboard — you should see a green "Schwab live" badge under the
status line.

## Token lifecycle

- Access token: 30 min, auto-refreshed by the server every ~25 min
- Refresh token: 7 days, rolls forward each access-token refresh
- If refresh token expires (server offline > 7 days), re-run
  `python3 schwab_auth.py` to mint a new one

## What's covered

- `/api/ticker` — quotes + chains + price history all from Schwab
- `/api/scan` — watchlist quotes from Schwab (one batched call)
- `/api/data_source` — live status of Schwab integration

## What's NOT covered (not requested this round)

- Account balances
- Positions
- Order placement
- Real-time WebSocket streaming

## Rate limit safety

Schwab limits to 120 req/min. The client maintains:
- 25-second cache on quotes
- 30-second cache on option chains
- 10-minute cache on daily price history
- Hard cap at 110 req/min (refuses to call Schwab beyond this)

Auto-refresh at 60s + 5-symbol watchlist scan = ~10 calls/min worst case.

## Troubleshooting

**Badge says "Schwab not configured"** — Either credentials missing
from .env, or `schwab_token.json` doesn't exist. Re-run `schwab_auth.py`.

**Badge says "yfinance fallback"** — Token expired or Schwab rejected
a call. Check Terminal logs for `[schwab]` lines.

**`schwab_auth.py` fails on token exchange** — App Key/Secret typo, or
Callback URL in your Schwab app config doesn't exactly match
`https://127.0.0.1:8182`. Schwab requires exact match.
