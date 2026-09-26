# Daily edge — next-day call or put on the six daily-expiry ETFs (v5.35)

`daily_edge.py` · `tab-dailyedge.jsx` · `GET /api/daily-edge` (`/forward`, `/status`) ·
first card on the Trade tab · push on each first signal per ETF per day.

SMH, QQQ, SPY, IWM, XLF and GLD list an expiry every weekday. The question:
when does selling the NEXT session's call or put have an edge, and when
should nothing be sold?

## What was measured

Hourly bars, Sep 2024 – Sep 2026, all six. At every bar close from 10:30 to
the close, a strike 0.84 of the ETF's own daily sigma out (stdev of the last
20 daily moves, scaled to the time left to the next close — about 20 delta)
was sold for the next session's expiry and graded on that close. The move is
measured from the prior close, in the ETF's own sigma, so a 1% day on SPY and
a 1% day on SMH are not treated alike. A pattern is used only if it held in
BOTH years, measured separately.

## The rules

| Situation | Action | Held on |
|---|---|---|
| Up 0.15 to 0.90 sigma, 10:30 to 3:30 | Sell the next-day **call** | SMH, QQQ, SPY, IWM, XLF |
| Same zone on GLD | Sell the next-day **put** | GLD (its calls broke 23%) |
| Down more than 0.90 sigma | **No call** | SMH, QQQ, SPY, IWM, XLF (not GLD) |
| SMH, second down day in a row, last 15 min | Sell the put | SMH (10y daily + hourly) |
| SMH, up on Friday, last 15 min | Sell Monday's put | SMH (10y daily) |
| Anything else | Skip | — |

## The evidence (first signal per day; last year / prior year)

| ETF | Side | Signal days | Broke on signal days | Broke selling at every close |
|---|---|---|---|---|
| SMH | call | 122 / 122 | 13.9% / 17.2% | 22.2% / 20.7% |
| QQQ | call | 116 / 133 | 19.8% / 11.3% | 23.4% / 20.7% |
| SPY | call | 135 / 142 | 13.3% / 12.0% | 20.2% / 18.2% |
| IWM | call | 130 / 133 | 12.3% / 17.3% | 18.6% / 19.0% |
| XLF | call | 147 / 133 | 12.9% / 9.8% | 19.4% / 19.4% |
| GLD | put | 118 / 103 | 12.7% / 11.7% | 17.0% / 16.0% |

No call: on days down more than 0.9 sigma, calls broke 20–34% versus
16–20% on an average day (SMH, QQQ, SPY, IWM, XLF, both years).

SMH close rules: second down day, put broke 10.8% / 8.5% (37 / 47 days);
ten years of daily bars, put 15.8% vs call 29.3%. Up Friday, ten years,
Monday put broke 9.9% vs call 23.2% (233 Fridays).

What did NOT hold and is shown as SKIP: small red days, flat days, up days
past 0.9 sigma, and puts sold after a selloff (great one year, bad the next).

## The price check

The bars carry no option prices. Each row therefore shows the live bid on the
listed strike nearest the measured one, beside the record's average loss past
that strike (per share, from the measured loss multiple of a random-walk fair
value). **Pays?** is bid minus that loss. That is the check history could not
make, and it is made live.

## The forward record

Every first SELL per ETF per day is written to `daily_edge_log.jsonl` in the
data dir with its strike and bid, and graded from daily bars after its expiry
closes. The card shows graded count, breach rate and net per share.

## Limits

- Two years of hourly bars; 100–150 signal days per ETF per year. Real, not large.
- Live checks run every minute, the research used hourly bar closes; the zone is wide enough that this matters little, but a brief touch can fire.
- A call is covered only with 100 shares; otherwise sell it as a spread.
- Rules and zone live in `DEFAULTS`; override under `"daily_edge"` in `thresholds.json`.
