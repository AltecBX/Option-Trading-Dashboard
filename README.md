# Jerry's Setup — Options Trading Dashboard

A live options trading dashboard that pulls data from Yahoo Finance, scores 26 strategies, visualizes vol skew + GEX + payoff curves, and runs a multi-ticker scanner across your watchlist.

## Quick start (local)

```
pip install -r requirements.txt
python options_dashboard.py --serve --port 8765
```

Open http://localhost:8765/ in a browser. The page calls `/api/ticker?symbol=...` which the same Python process answers.

## Architecture

- **Backend.** `options_dashboard.py` — single-file Python. Uses stdlib `http.server` plus `yfinance` for Yahoo data. Exposes `/api/ticker`, `/api/scan`, `/api/search`.
- **Frontend.** `index.html` + `app.jsx` (~3700 lines) + `charts.jsx` + `strategies.jsx` + supporting modules. Pure static, no build step. Loaded via Babel-standalone in the browser.
- **Config.** `config.js` sets the API base + key for the deployed environment. Empty values mean local dev.

## Deploy to your phone

See `DEPLOY.md` for the full walkthrough. Short version:

1. Push to a private GitHub repo
2. Deploy backend to Railway (auto-detects Python, runs from Procfile)
3. Deploy frontend to Vercel (pure static)
4. Edit `config.js` to point at the Railway URL
5. Add the Vercel URL to your iPhone home screen

## Files

| File | Purpose |
|------|---------|
| `options_dashboard.py` | Backend server + yfinance integration |
| `requirements.txt` | Python dependencies (Railway reads this) |
| `Procfile` | How Railway starts the server |
| `vercel.json` | Tells Vercel this is pure static |
| `config.js` | Frontend runtime config (API base + key) |
| `index.html` | Entry point, loads everything |
| `app.jsx` | Main React app |
| `charts.jsx` | All SVG chart components |
| `strategies.jsx` | 26 strategies + payoff math + reference docs |
| `tooltips.jsx` | Glossary popovers |
| `tweaks-panel.jsx` | Theme + layout settings drawer |
| `data.js` | Mock fallback data |
| `styles.css` | All styles, including mobile-friendly breakpoints |
| `assets/app-logo.png` | Brand logo |

## Environment variables (production)

Set on Railway:

- `API_KEY` — required header value for all `/api/*` requests. Empty = no auth (don't use in prod).
- `ALLOWED_ORIGIN` — CORS origin allowed. Set to your Vercel URL.
- `PORT` — Railway sets this automatically.

## Updating

```
git add .
git commit -m "what changed"
git push
```

Both Railway and Vercel auto-redeploy.

## Unusual Whales (optional)

Add your UW API key to `~/.jerry-dashboard/.env`:
```
UW_API_KEY=your_token_here
```

Then `jerry restart`. The sidebar will show a blue UW pill with current minute quota when connected. Endpoints exposed: `/api/uw/health`, `/api/uw/flow_alerts?symbol=XYZ`, `/api/uw/option_chains?symbol=XYZ`, `/api/uw/greek_exposure?symbol=XYZ`, `/api/uw/net_premium?symbol=XYZ`, `/api/uw/market_tide`, `/api/uw/sector_flow`. Caching and rate-limit aware throttling are handled in `unusual_whales_client.py`.
