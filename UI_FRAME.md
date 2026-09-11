# The permanent frame (v4.93)

What changed in the presentation layer, why, what was measured, and the
feature-preservation checklist this was built against.

This is a **presentation and navigation** change. No calculation, data source,
evidence definition, saved setting, watchlist, tag, position, journal entry,
alert or integration was altered.

---

## 1. The problem, measured

The live review of September 10, 2026 asked for ten market charts, four
high/low rails and two bottom feeds to be present on every destination. They
already were — *rendered*. The whole document scrolled, so:

| | Before | After |
|---|---|---|
| Trade, wide desktop (2160×1200) | document **11,018 px** tall; charts scrolled away in one wheel flick | document **1,200 px** — the workspace scrolls, the frame does not |
| Trade, phone (440×956) | document **22,067 px**; charts a horizontal strip showing **3 of 10**; the actual tool began at y=**734**, below the fold | charts a **2×5 grid, all ten**; the tool begins at y=**375** |
| Bottom feeds, phone | last element of a 22,067 px document | pinned at y=**868**, on screen on every destination |
| Trade, laptop (1440×900) | workspace shared one scroll with everything | workspace **374 px**, frame fixed |
| Trade, **landscape phone** (956×440) | 956 px is past every `max-width:900px` rule, so it took the full desktop frame: workspace **50 px**, both feeds **below the fold** | workspace **209 px**, feeds on screen |

"It renders" was true and useless. The frame is now structural.

## 2. The structure

```
.shell                     height:100dvh, grid-template-rows: auto 1fr auto
├── .frame-top             app bar · market band · ten charts · section bar
├── .frame-body            sidebar │ .main  ← THE ONLY SCROLLING BOX
└── .frame-bottom          Market News row · Tickers row · status line
    (+ .lrail ×4, fixed in the margins, ending where the feeds begin)
```

The frame measures itself with a `ResizeObserver` and publishes
`--frame-top-h`, `--frame-bottom-h` and `--ws-h`. The rails end at
`--frame-bottom-h`; panels that used to size themselves in `vh` now use
`--ws-h`.

### Where things mount, and why it is not CSS

CSS can hide a node. It cannot move one. Three placements are therefore
decided in the component:

| | Desktop | Phone / short viewport |
|---|---|---|
| Four high/low lists | fixed side rails (≥2080 px) | one tabbed card in the workspace |
| Market band (posture, regime, catalysts, opportunity ribbon) | in the frame, as the reference shows | first thing in the Trade workspace |
| Four navigation rows | in the frame | hidden; the grouped, searchable tool picker replaces them |

Only the *selected* high/low list mounts on a phone, so the card polls **less**
than the four hidden rails it replaces.

## 3. Feature-preservation checklist

### The ten market charts
- [x] All ten present: S&P, NASDAQ, Dow, US Dollar, VIX, Bitcoin, Gold, 10Y, Hi-Yield, Crude
- [x] Real data, labels, prices, changes, sparklines, source dots, symbol links
- [x] 5×2 on wide desktop · 2×5 in portrait · 5×2 in landscape and on short screens
- [x] Never a carousel, never text-only badges, never collapsible
- [x] A strip that fails to load keeps its ten tiles and says *Not answering* rather than vanishing

### The four rails
- [x] Near 52W Low, Daily Low, Daily High, Near 52W High
- [x] Same data, same gates, same scroll, same tag grouping, same click-to-load
- [x] Owned-symbol highlight, live-quote overlay, empty-state notes preserved
- [x] Reachable on mobile — previously `display:none` below 2080 px

### The two bottom feeds
- [x] Market News and Tickers are two separate continuously scrolling rows
- [x] Present on every destination, **including embedded partner tools**, which render inside the workspace and cannot cover them
- [x] Height reserved by the layout — they never cover content and are never something you scroll to reach
- [x] Coordinated with the mobile action bar and `env(safe-area-inset-bottom)`

### Version
- [x] Compact bottom status line carries `APP_VERSION`, the existing single source
- [x] Shown once (the duplicate sidebar pill was removed)
- [x] No connection status invented

### All 30 destinations
Trade · Discover · Analyze · Patterns · Watchlist · News · Market Calendar ·
Flow · Scanners · Streaks · Manage · Ask AI · Investment · Hedge Funds ·
Premium Edge · Gap Scan · Recovery · 0DTE Juice · Backtest · Sectors ·
Market Context · Gamma Exposure · Breadth · Journal · US Treasuries ·
Earnings Ops · Finviz · TradingView · Unusual Whales · Simply Wall St

- [x] All 30 still declared and reachable; `test_ui_frame.js` asserts the four
      navigation groups are a **total and disjoint partition** of them, because a
      destination in no group is a destination that silently disappears
- [x] Drag-to-reorder and the saved order survive, within a group
- [x] Subordinate tools untouched: Scanners' 13 tools; Discover's Playbook /
      Analyst calls / Movers / Trend / HV Rank; Watchlist's Stocks / Sectors /
      Industries; Hedge Funds' Pulse / Named Funds / Weekly Report; Korea Lead
      inside Gap Scan; Recovery as the Prior High Recovery scanner; Journal's
      picks and radar performance; Trade's panels, 26 strategies, payoff views,
      options chain and builder
- [x] Partner tools keep global ticker following and their helper/session behaviour

### Global utilities
- [x] Market posture, early movers, macro regime, catalysts, sector rotation,
      opportunity ribbon, global ticker and search, starred watchlist, editable
      presets, weeks of history, strike picker, return baseline, expiration,
      source/freshness badges, weather, theme, strategy cheat sheet, keyboard
      shortcuts, percent calculator, alerts, partner helper, mobile quick actions

## 4. Functional UI changes

**Local section navigation.** Trade (~12,000 px tall) and Scanners (13 tools)
get a jump list at the top of the workspace. It is built from the **rendered
page**, not a hand-kept list — a list would have to be edited whenever a panel
was added, and nothing would enforce that, so the panel would stop being
reachable from the index while still being on the page. Trade's 17 panels are
grouped into Opportunities / Chart & timing / Contracts & strategies /
Risk & management by heading **prefix**, because most headings carry live
detail ("Net Greeks · Short Strangle").

**Watchlist.** Five presets (Overview, Options flow, Technicals,
Earnings & value, All columns) plus a column chooser. Views over the same 46
columns — nothing deleted, sorting/filtering/saved column order untouched, any
column one tick away. Symbol is pinned and cannot be hidden. On a phone the
table becomes one summary card per row with **all 46 fields** behind
*All 46 fields*.

**Data status vocabulary.** One `DataStatus` component with seven kinds — live
quote, last close, cached scan, modeled estimate, measured history, loading,
unavailable — each with its own tooltip explaining what kind of claim it is.
The timestamp belongs to the **result**, not the page.

**Compact verdicts.** `PanelVerdict` puts the answer, its reason and its
timestamp first; `PanelMethod` keeps the long explanation one line away in a
`<details>` — reachable, keyboard-navigable, findable by browser search, never
cut.

**The loading transition.** Reproduced and fixed — see §5.

## 5. The AEHR / Apple defect

The review saw AEHR selected while Apple values appeared. Reproduced with the
payload held in flight:

```
requested: AEHR -> {'name': 'Apple Inc.', 'price': '$234.18',
                    'input': 'AEHR', 'mentionsApple': True}
```

`dataset` asked `PRESETS[ticker]`, and when the answer was "nothing yet" it
built the whole frame — rows, bars, chain, company name, price — from
`Object.keys(PRESETS)[0]`, which is AAPL. It was every symbol outside the six
demo presets, on every cold load and every first visit to a name.

The placeholder now carries the shape and none of the content. **JSX children
are evaluated eagerly**, so a hidden panel has already computed itself from
whatever was in scope — gating inside `TabPanel` fixed nothing and
`baselinePrice.toFixed(2)` still threw. The panels are therefore not built at
all while the symbol has no data; the workspace shows one card naming the
symbol it is waiting for, or why the fetch failed, with a retry. The six demo
presets are untouched, so the offline demo still works.

## 6. What was verified, and how

Local server, real Chromium, the pinned React the tests use.

- **Every destination, wide desktop and phone**, one fresh page each, scrolled
  to the bottom of the workspace: the charts, the tiles count, both feeds and
  the absence of page scroll re-checked on each. **28/30 at both sizes.**
- The two exceptions (**US Treasuries**, **Flow**) sit in loading skeletons
  because their upstream sources never answer in this offline sandbox.
  Verified **identical on the pre-change build at `90d0e8f`**, so not a
  regression — and the live review opened both successfully.
- Viewports: 2160×1200, 1440×900, 1440×700, 956×440 (landscape), 440×956.
- `node build_frontend.js` · `node verify_frontend.js` · `npm test` ·
  the full Python suite (3,708 tests) · `test_hf_render` in a real browser.


## 7. The defect this shipped with, and what now guards it

The frame put the shell on a single `1fr` grid track. A `1fr` track does not
cap its item — it grows to the item's **min-content** width. The bottom feeds
are one unbroken line of headlines, so on the live deployment, with sixty real
headlines and forty ticker quotes, `.frame-bottom` measured **83,838 px** wide,
the shell's column grew with it, and `.frame-top` and `.frame-body` — which
centre themselves inside that column — ended up at **x = 41,119**. Everything
except the fixed rails and the tapes was off screen.

Every check passed. The static CSS guards, the free-variable lint, the load
harness, and thirty destinations walked in a real browser at two viewports —
all green, because **the news feed cannot reach its source in the sandbox**.
The tape had no width to inflate the track with. The layout was tested where
the data was empty and shipped where the data is not.

`test_frame_render.py` now feeds the tape sixty production-shaped headlines
and asserts every part of the frame is inside the viewport, at four sizes.
Verified by reverting the fix: it fails with
`.frame-top runs to x=56634 in a 2160px viewport`.

The lesson worth keeping: *a layout bug that needs real data to appear needs
real-sized data in the test.* The stylesheet already carried this exact
warning for the mobile breakpoint; the new grid needed it too.

## 8. What the frame broke without anyone noticing (v4.93)

Three of these came from Jerry looking at the live app. They are all the same
kind of mistake: something kept working as *code* and stopped working as a
thing you can see.

**The weather went missing.** It never stopped rendering. It was
`position: absolute; top: 3px; right: 3px`, hung off the sidebar — and the
frame made the sidebar `position: static`, a plain scrolling box. An absolutely
positioned child with no positioned ancestor falls back to the window, so the
pill went to the top-right corner of the *screen* and sat underneath the fixed
52-week-high rail, which is drawn later and on a higher z-index. Every check
passed, because every check asked whether it rendered.

It is now an inline control in the app bar, and in the mobile header on a
phone — mounted in exactly one of the two, chosen by the same 900px line
`useIsPhone()` reads, because `display: none` does not unmount a component and
two of them would be two forecast fetches, two geolocation prompts, and a
"use my location" toggle that only moved one of them.

**Two clocks disagreeing.** The app bar's clock ticked once a minute and showed
no seconds; the LiveClock beside the Schwab badge ticked every second and
showed 3:30:53. Both are on screen at once. It now follows the same rules —
seconds, a one-second tick that stops while the tab is hidden, and green while
the regular session runs.

**"Educational use only" was mine, and it was never asked for.** So was
"Market data may be delayed". Neither existed before the frame; I put them in
the status line as boilerplate. The first is gone. Every panel already says
which source answered it and when, which is the honest version of the same
claim.

**The stripe beside the rails.** The four rails are `position: fixed` to the
window edges. The content column was capped at a fixed 1600px and centred
between them. Those are two independent sums, and the difference shows up as
plain background between the inner rail — Daily Low on the left, Daily High on
the right — and the first card: **34px on each side** at Jerry's 2152×1117,
because 24px of the frame's own padding sat on top of the 10px the centring
left over.

It gets worse on a bigger monitor. The rails auto-size to `(100vw − 1600) / 4`
but stop at 190px, so past roughly 2400px the extra margin has nothing to fill
it: 88px of dead space each side at 2560px, and growing. The cap is now derived
from what the rails leave rather than being a second hard-coded number,
`--rail-w` is defined once so the two formulas cannot drift apart, and the
padding at that width is 10px rather than 24px.

| Window | Gap, rail to first card | Content column |
|---|---|---|
| 2152 px (Jerry's) | 34 px → **12 px** | 1548 px → 1592 px |
| 2560 px | 122 px → **12 px** | 1552 px → 1752 px |
| 3840 px (4K) | 402 px → **12 px** | 1552 px → 3032 px |
| 1900 px (no rails) | — | 1552 px → 1852 px |

`test_frame_render.py` measures that gap in a real browser at all three widths
and fails above 20px. It is a browser test on purpose: the number is the
product of a fixed-position element and a centred one, which is exactly the
kind of arithmetic a stylesheet can get wrong while every rule in it reads
correctly.

**And the fix had the same bug in it.** The first draft capped the content at
`min(2200px, …)` — a chart row stretched across a 4K monitor is not more
readable, only wider. But a cap *is* the second hard-coded number this rule
exists to remove. Above 2988px the cap won over the subtraction, the rails
stayed at 190px, and the band came straight back: **438px on each side at
3840px**, thirteen times the one that was reported. The two browser checks
written alongside the fix — 2152 and 2560 — both sit below the crossover and
could not see it. A review bot found it.

So there is no cap: either the content follows the rails at every width or it
does not follow them at all. If a very wide workspace ever needs reining in,
that belongs on the panels inside it. `test_ui_frame.js` now also fails on any
`min(<number>px, …)` reappearing in that declaration, and the browser check
runs at 3840 as well.

### Not verified here — needs your phone

This was responsive testing in desktop Chromium, **not** iPhone Safari.
Still to check on the physical iPhone 16 Pro Max:

1. `100dvh` against Safari's collapsing toolbar, in the browser **and** in
   home-screen app mode.
2. `env(safe-area-inset-bottom)` under the home indicator, portrait and
   landscape.
3. Momentum scrolling and rubber-banding inside the workspace now that the
   page itself cannot scroll (`overscroll-behavior: contain`).
4. The on-screen keyboard: whether it shrinks the visual viewport and what
   that does to the fixed frame when typing in the tool picker or ticker box.
5. Chart tap targets in the 2×5 grid, and the tabbed Highs & Lows card.
6. Real contrast on the OLED panel — this review did not establish a formal
   WCAG pass.
7. The partner tools with the Site Helper actually installed; the sandbox has
   no extension, so the iframe path was exercised by announcing the helper
   manually.
