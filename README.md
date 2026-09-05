# Jerry's Setup — Options Trading Dashboard

A live options trading dashboard that pulls data from Yahoo Finance, scores 26 strategies, visualizes vol skew + GEX + payoff curves, and runs a multi-ticker scanner across your watchlist.

## Quick start (local)

```
pip install -r requirements.txt
python options_dashboard.py --serve --port 8765
```

Open http://localhost:8765/ in a browser. The page calls `/api/ticker?symbol=...` which the same Python process answers.

## Architecture

**Backend.** `options_dashboard.py` is still the HTTP entry point — stdlib `http.server`, every `/api/*` route — but it is no longer where the work happens. Most features now live in their own Python modules that it imports and wires: `gap_engine.py` / `gap_scan.py`, `invest_engine.py` / `invest_scan.py` / `fair_value.py` / `peers.py`, `korea_lead_engine.py` / `korea_lead.py` / `korea_research*.py` / `korea_capture.py`, `strat_states.py` / `gex_engine.py` / `market_state.py`, `backtest.py`, `patterns.py`, and others. The consistent split is a pure engine (mathematics, no I/O, no clock) beside a stateful module that owns fetching, caching and disk. Market data comes from Schwab first where a real quote is needed, with Yahoo as a fallback. Tunables live in `thresholds.json`, overridable key by key from `<data_dir>/thresholds.json`, and the effective config is hashed onto the results it produced.

**Frontend.** React source in `.jsx`, compiled by `node build_frontend.js` — there is no Babel in the browser. That script compiles the JSX to readable committed `.js`, minifies everything through esbuild into `dist/`, pre-compresses each asset to a `.gz` sibling, and stamps content hashes so a changed file gets a new URL. Several heavy tabs — Gap Scan, Investment, Backtest, Patterns and others — are lazy chunks that are not `<script>` tags in `index.html` at all; `LazyTab` injects them the first time that tab is opened. `config.js` is deliberately left unminified and unversioned because it is edited after deploy. Run the build after any `.jsx` change; deploy machines never run node.

**Korea Lead** is not a tab. It is a quantitative layer that renders at the top of Gap Scan, with its own research endpoints behind it (`/api/korea_lead`, `/api/korea_research/*`, `/api/korea_forward/*`) and a background thread that archives point-in-time Korean state and a pre-open prediction record each session.

**Deployment.** Railway starts the Python server from `Procfile`; it serves both the API and the built frontend out of `dist/`.

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
| `options_dashboard.py` | HTTP entry point; wires the feature modules listed under Architecture |
| `build_frontend.js` | Compiles JSX, minifies to `dist/`, pre-compresses, stamps versions |
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
| `strat_states.py` | Candle-state engine (1 / 2U / 2D / 3) and calendar bucketing — pure |
| `gex_engine.py` | Gamma exposure by strike and the Black-Scholes flip profile — pure |
| `market_state.py` | Live layer for the Sectors and Market Context tabs |
| `tab-strat.jsx` | Sectors, Market Context and Gamma Exposure (one lazy chunk) |
| `setup_engine.py` | Best Setup: the decision layer — delta ceiling, gamma modifier, scoring — pure |
| `setup_scan.py` | Best Setup: gathers the layers, measures how far the stock actually travels |
| `tab-setup.jsx` | The Best Setup card on the Trade tab (lazy chunk) |
| `CANDLE_STATES.md` | How the candle states, sectors and gamma exposure work |
| `setup_board.py` | Worth selling today: ranks the watchlist by how rich the premium is — pure |
| `BEST_SETUP.md` | How the one recommendation is built, and what it refuses to do |
| `sp_probability.py` | Short premium: P0 / P(touch) / P(profit) / early targets / tail at the contract's horizon — pure |
| `sp_evidence.py` | Short premium: each stock's measured breach history, shrunk toward peers and the universe — pure |
| `sp_engine.py` | Short premium: six gates in order, Sell Quality, modes, objectives, the plain-English defence — pure |
| `sell_scan.py` | Best Sales Today: rides the Premium Edge chain pass, keeps the board, records every row shown |
| `sp_forward.py` | Best Sales Today: grades recorded rows after expiry and builds the calibration tables |
| `tab-sell.jsx` | The Best Sales Today card at the top of the Trade tab (lazy chunk) |
| `SHORT_PREMIUM.md` | The short-premium engine: gates, probabilities, validation results, the honest ledger |
| `spike_evidence.py` | Sold into strength: what a stock does after it has already run, in its own sigma — pure |
| `spike_scan.py` | Sold into strength: today's runs, same-day chains, ranked by credit minus measured settlement |
| `tab-spike.jsx` | The Sold Into Strength card at the top of the Trade tab (lazy chunk) |
| `SPIKE_FADE.md` | What was measured about spikes, why sigma is the ruler, and what the feature refuses |
| `assets/app-logo.png` | Brand logo |

## Environment variables (production)

Set on Railway:

- `API_KEY` — required header value for all `/api/*` requests. Empty = no auth (don't use in prod).
- `ALLOWED_ORIGIN` — CORS origin allowed. Set to your Vercel URL.
- `PORT` — Railway sets this automatically.
- `GEX_DEV_FIXTURES` — set to `1` to serve a clearly-labelled synthetic option
  chain on the Gamma Exposure tab when the broker has none. **Off by default,
  and it should stay off in production** — see `CANDLE_STATES.md`.

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
