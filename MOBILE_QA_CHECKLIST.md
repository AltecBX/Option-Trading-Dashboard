# Mobile QA Checklist (iPhone Safari + Android Chrome)

Run after any frontend change. Primary target: **iPhone Safari** (most fragile),
then Android Chrome. Use a real device where possible; Safari Responsive Design
Mode is a fallback, not a substitute.

## Setup
1. Open `https://dashboard.jerrytrade.com` on the phone.
2. Hard-reload (close tab, reopen) to clear cached JS; confirm the version pill
   in the sidebar matches the expected `vX.YZ`.
3. For perf numbers: on desktop, `localStorage.setItem("jerryDebug","1")`, reload,
   and read the console (mobile timings will be similar but slower).

## A. Load & shell
- [ ] First paint shows the shell + Trade tab quickly (no long white screen).
- [ ] No layout shift after data loads (price/MAs settle without jumping).
- [ ] App "Add to Home Screen" launches full-screen; status bar readable.

## B. No horizontal page scroll (critical)
- [ ] On every tab, you **cannot** swipe the whole page sideways.
- [ ] Wide tables (Watchlist, option chain, scanner boards) scroll **inside their
      own container**, not the page.
- [ ] Rotate to landscape and back — still no page-level horizontal scroll.

## C. Touch targets
- [ ] Tab bar items, ticker switch, sidebar toggle, refresh `↻`, filter chips,
      and sort headers are all easily tappable (≥44×44px effective).
- [ ] Number inputs (Acct $, risk %, target delta) open the **numeric** keypad.
- [ ] No "stuck hover" highlight left on a row/button after tapping.

## D. Navigation
- [ ] Tab bar scrolls horizontally; the **active tab is visible** after switching.
- [ ] Sidebar drawer opens/closes; overlay dismiss works; content behind doesn't scroll.
- [ ] Safe-area: nothing hidden under the notch or home-bar (sticky header/footer padded).

## E. Charts
- [ ] Price chart fits width without pinching; resizes on rotate.
- [ ] Scrolling the page over a chart scrolls the page (chart doesn't hijack touch)
      — or the chart has an intentional inspect gesture.
- [ ] No jank/lag when the chart is on screen during scroll.

## F. Tables / option chain / scanners
- [ ] Watchlist: scroll loads more rows smoothly (progressive render), momentum OK.
- [ ] Option chain: ATM strikes visible; can pick call/put strikes by tap.
- [ ] Scanner "Scan now": progress is visible; UI never freezes; failed tickers shown.
- [ ] No important trading number is clipped/hidden (Edge, EV, size, strikes,
      breakevens, risk).

## G. Per-card reliability
- [ ] Kill network (airplane mode) → cards show error/stale state, **no blank crash**.
- [ ] Re-enable network → cards recover (auto or via retry).
- [ ] Switch to a bad symbol (e.g. `ZZZZ`) → clean "no data", not a crash.
- [ ] Switch symbols rapidly → no old-symbol data lingers after the new loads.

## H. Performance feel
- [ ] Ticker switch shows the Trade tab quickly (cached symbols feel instant).
- [ ] Switching tabs is smooth; non-active tabs didn't block the first load.
- [ ] Battery/heat: leave it open 5 min — no runaway polling (background tab pauses).

## I. Regression guard (trading correctness)
- [ ] Recommendation, strategy P/L, breakevens, Greeks unchanged vs desktop.
- [ ] Watchlist count matches "Manage (N)" after a scan; deletes persist.
- [ ] Saved settings (ticker, weeks, baseline, delta, tab order) survive reload.

## Devices to cover
- iPhone (notch + Dynamic Island), iPhone SE (small), Android Chrome, iPad Safari
  (portrait + landscape).
