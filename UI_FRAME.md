# The permanent frame (v4.99)

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
├── .frame-top             app bar · market band · ten charts · opportunities
├── .frame-body            sidebar │ .frame-col
│                                     ├── .tab-bar   (the section switcher)
│                                     └── .main      ← THE ONLY SCROLLING BOX
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

## 9. The frame was overhead too (v4.94)

A permanent frame is permanent **overhead**. At 1440×900 it had grown to 526 of
the 900 pixels — app bar, regime line, ten charts, context strip, opportunity
ribbon, four navigation rows — leaving 374 for the tool you came to use, and an
embedded partner chart got 278 of those. Keeping the ten charts is the point of
the frame. Letting everything *around* them outweigh the workspace is not.

Nothing was removed. The chrome was measured and trimmed: a 34px app bar
instead of 46 plus a 10px margin, tiles at 74px instead of 88 with a 26px
sparkline instead of 30, one line per context strip, navigation rows at 23px
instead of 34, and 10px of frame padding instead of 24.

The context strips are worth a note. The rotation ribbon draws a chip per
sector with three or more names — eleven on a busy day — and it wrapped onto a
third row, which is the extra line visible in the review screenshot. The first
fix put `nowrap` on `.mctx-line`, the gamma-and-catalysts row; a review bot
pointed out that the chips are in the sibling `.mctx-ribbon`, which still
wrapped. **And the test could not have caught it**: with no watchlist data the
sandbox draws *"rotation pending scan…"*, one short line that can never wrap,
so a height check on that element would have passed with the bug in place —
the same shape of blindness as §7's news tape. `test_frame_render.py` now
serves a populated board (eleven sectors, three names each), asserts the chip
count **first** so an empty ribbon cannot make the check vacuous, and then
asserts the ribbon is one row. Reverting the fix gives
`43 not less than or equal to 32 : the rotation ribbon is 43px tall with 11
chips`.

And then a third pass, found by checking the merged build on the live site
rather than trusting the sandbox: the strip still measured **82px live against
44 here**. `nowrap` on `.mctx-line` stops its *direct* children being pushed to
a new line; it does nothing about a child that grows taller by wrapping inside
itself. `.mctx-events` is exactly that — one flex item holding every catalyst
and the whole earnings list. Fixed, and `/api/market_context` is now stubbed
too, with **eight** catalysts rather than a quiet day's four: a row that fits
inside the column cannot wrap, so a payload that fits is a payload that proves
nothing. The first version of that stub used four and passed with the bug
reverted.

Three elements of this frame take their height from data — the news tape, the
rotation ribbon, and the catalysts row — and all three were found the same way:
not by a test, but by looking at the running app. The tests now feed all three.

The short-viewport ladder was re-tuned at the same time: three of its steps had
been written against the old, taller base and were now *larger* than it, so a
1440×900 laptop was being given a **bigger** app bar than a 2560×1440 monitor.

Measured by `test_frame_render.py`, which feeds the news tape a
production-sized payload — so these are the numbers with the frame at its real
height, not its empty-sandbox one:

| | Before | After (sandbox) | After (live) |
|---|---|---|---|
| Workspace, 2152×1117 | 481 px (43%) | **566 px (51%)** | **521 px (47%)** |
| Workspace, 1440×900 | 340 px (38%) | **404 px (45%)** | — |
| Frame top, 2152×1117 | 522 px | **425 px** | — |

Three different numbers for the same commit, and the spread is the point. The
frame's height is a sum of type, padding and *data*: the CI runner's fonts
differ from this sandbox's by a percent or two, and the live app's real chart
tiles and real rails are a few pixels taller than stubbed ones — about four
points at 2152. A floor set just under one reading is a floor that fails
against the others, which is exactly what happened: the first version went red
on CI, and the second would not have been true of the running app. The floors
now sit under every reading, and the 1440×900 check — which has four points of
clearance on both sides — is the one carrying the regression-catching weight.

Inside a partner panel, two toolbar rows and a permanent three-line paragraph
about signing in sat between the heading and the chart. The paragraph is now a
`<details>` — same words, keyboard-reachable, findable by browser search, one
line instead of three. All four partner panels got the same treatment.

`test_frame_render.py` asserts the workspace is at least **42% of a 1440×900
window and 44% of a 2152×1117 one**, and in the same check asserts the ten
charts are still all there — so a floor can never be met by dropping them.
Those two numbers are the contract; the table above is the measurement they
were derived from, and the 1440×900 one is the load-bearing check. If you
change either, change it here too: the first version of this paragraph kept
saying 47% after the assertion had moved to 44%, which would have let a
regression into that band pass CI while the document said it should fail.

## 10. Four more things the review found

**The Watchlist opened on an empty panel.** Analyst Actions renders above the
stocks. With no actions it still drew a heading, a Today/Recent switch, a sort
box, a Scan button, eight filter pills and a centred notice — about 300px, which
on a phone is the whole first screen, so the stocks began below the fold.
Collapsed it is ~34px: one line reading *Analyst Actions · no actions today ·
12 recent*. Open it and it is exactly the panel it was; no control, filter or
history was dropped.

**The jump control was below the things it exists to skip.** On a phone the
section index sat under Market Posture, the catalysts strip and Highs & Lows —
about a screen and a half — so the control whose whole purpose is to save you
scrolling was itself something you scrolled to reach. It is now the first thing
in the workspace, and it says *Jump to* on a phone, where it needs to name
itself.

**The settings drawer ignored the frame.** The tool picker was already bounded
by `--frame-top-h` / `--frame-bottom-h`; the drawer was `inset: 0`, so opening
it covered the ten charts the frame exists to keep on screen. Same two
variables, applied.

**The version was missing on a phone.** The stylesheet said *"the version rides
the sidebar on phones"* — and it did, until §8 removed the duplicate sidebar
pill so the version would appear once. Removing one of two copies left zero.
It is back in the frame, trimmed to the one thing it is for, in every shape
including landscape.

## 11. Visual hierarchy, measured

"Secondary labels are still faint" is not a matter of taste — it is a contrast
ratio. Two of the four text tiers failed WCAG AA against the surfaces they sit
on, and `--fg-4` carries the **smallest type in the app**: the nine- and
ten-pixel mono captions.

| Tier | Light, worst surface | Dark, worst surface |
|---|---|---|
| `--fg-2` | 6.8 → **8.1:1** | 7.9 → **8.7:1** |
| `--fg-3` | **4.0 → 6.0:1** | 4.9 → **6.1:1** |
| `--fg-4` | **2.4 → 4.6:1** | **2.9 → 5.0:1** |

Each tier is checked against all three surfaces it lands on — page, card and
chip — because a chip is the lowest-contrast of the three and is exactly where
the small mono captions sit. `test_ui_frame.js` computes the real ratio from
the tokens (oklch → sRGB → relative luminance), so the next person to nudge a
lightness gets told rather than guessing.

It also asserts the tiers stay **far enough apart to still be a ladder** —
which caught a real regression while this was being written: the first pass
raised light `--fg-3` and `--fg-4` to within 0.03 of each other, which would
have passed a contrast check and flattened the hierarchy it exists for.

And the panel: a heading, an explanation, the controls and the answer were all
set at roughly one size and weight. The answer is now the largest thing
(`.panel-verdict` at 14px with a 4px accent edge and its reason at full
strength), the heading is full-strength colour at 15.5px/650, and the
explanation is 11.5px in the quiet tier, capped at 88 characters a line.
Methodology stays one click away in `<details>`. Nothing was deleted.

## 12. Four more, from a phone in both hands (v4.95)

Each of these was found the same way the v4.94 ones were: by measuring the
running app rather than by reading the code. Three of the four are the *same*
bug — a control was moved or restyled and reported as done, while the thing the
move was supposed to achieve was never measured afterwards.

### The mobile Watchlist opened on everything except stocks

The first stock card began **798 pixels** down a workspace **412 pixels** tall.
Two full screens of preamble — an explanation paragraph, a whole-market flow
summary and eight filter controls — stood in front of the list the destination
is named after. The v4.94 round had collapsed the empty analyst board above it,
which is why this was visible at all: fixing the first obstacle exposed the
second.

Nothing was removed. On a phone the everyday controls stay out — which view,
search, and how many rows — and the rest folds:

| Part | Before | After |
|---|---|---|
| Analyst board | 64 | 64 |
| Card head | 40 | 40 |
| Explanation (`.ab-status`) | ~180 | 48 + a 24px `What the columns mean` fold |
| Market flow summary | ~150 | one summary line, details on tap |
| Filters | ~190 (8 controls, wrapped) | 76 (3 controls + a `Filters (n)` toggle) |
| **First stock card at** | **+798** | **+329** |

Measured at 440×956 with a 457px workspace, so the first card is in the first
view with room under it.

A folded filter that is still narrowing the list is a trap: you would see six
stocks, not know why, and conclude the scanner is broken. The toggle therefore
reads `Filters (2)` when two of the hidden controls are active, and that count
is derived from the filter state rather than being a label someone maintains.

**And a fifth thing, found by the test disagreeing with itself.** The new guard
passed, then failed on the next run with identical code — 329px once, 668px the
next time. The analyst board above the stocks collapsed only when
`sorted.length === 0 && !isScanning`, so a scan still in flight blew it back up
to 403 pixels and put the stocks below the fold again. **Which load you got was
a race.** Rows or no rows is the whole question; a scan in progress is a word in
the summary line, not a reason to reopen an empty panel. The test now pins
`scanning: true` in its stub so it exercises the worse of the two every time —
a check whose answer depends on timing is not a check.

**And a sixth, which only the live deployment could show.** Everything above was
measured against a sandbox whose analyst endpoint returns nothing. The real one
does not. Measured on the deployment at 440×956 with Jerry's actual watchlist:

| | Sandbox | Live |
|---|---|---|
| Analyst board | 64px, collapsed | **629px**, a thirteen-column table |
| First stock card | +325 | **+957** in a 415px workspace |

The fix folded the board when it was **empty**, so it fixed the screenshot and
not the destination — on any ordinary morning the board has rows and still owned
the first two screens. Now the populated board folds on a phone too: the same
card, the same table, the same filters, behind a summary that leads with the
count (`6 actions today`), which is the part worth a glance at 7am.

The lesson is the one this document keeps re-learning, in a new place: **a stub
gentler than production is a stub that passes a broken page.** `ANALYST_ACTIONS`
in `test_frame_render.py` is now a populated board *mid-scan* — both hard cases
at once. Reverting the fold against it gives `894 not less than 415`; against
the old empty stub it gave nothing at all.

### Rotating the phone brought the desktop back

Every mobile rule in `styles.css` is written `max-width: 900px`. Rotate a
440×956 phone and it is 956×440 — past that number — so the desktop sidebar
returned and took roughly a third of a 956-pixel-wide screen to show a logo and
a green connection dot.

Width alone cannot tell a phone on its side from a laptop. The **height** can:

```css
@media (max-height: 560px) and (max-width: 1180px) and (min-width: 901px)
```

The 1180px ceiling is the important half — a 500px-tall window on a 1440px
monitor is a deliberate choice and must not take this branch. Inside it the
frame collapses to one column and the sidebar becomes the same off-canvas
drawer it is in portrait, bounded by `--frame-top-h` / `--frame-bottom-h` like
every other overlay. The mobile header that carries the burger in portrait is
not on screen at this width, so the app bar grows a `☰` — and that button is
`display: none` everywhere else, because a door in front of an open room is
worse than no door.

Landscape, 956×440: the workspace went from about **66% to 95%** of the width,
with all ten charts still mounted.

**And that fix had a bug of its own, which a review bot caught.** The branch
above was written in CSS *only*. The components kept asking `useIsPhone()`,
which is width-keyed, so at 956×440 the two halves of the app disagreed about
what a phone is:

| | CSS said | The components said |
|---|---|---|
| Sidebar | drawer | — |
| Jump control | (phone) | the **904px chip strip** the picker replaces |
| Four high/low rails | `display: none` | mount them as fixed columns |

The second row is what the bot found. The third is worse and followed from the
same cause: all four lists were **mounted, polling and unreachable**, with the
tabbed card that replaces them never rendered — which is precisely the defect
§1 says the permanent frame was built to end, reintroduced in landscape by the
fix for a different problem.

Two definitions of "phone" was the bug. There are, however, two legitimate
questions, so the answer is to name both rather than to merge them:

| | Asks | Used by |
|---|---|---|
| `useIsPhone()` | is the viewport **narrow**? | components whose partner CSS is keyed `max-width: 900px` — the mobile header and its controls, the watchlist's one-card-per-row layout |
| `useIsPhoneFrame()` | is the **frame** in phone mode? | mount points: the rails vs. the tabbed card, and the jump control |

`PHONE_FRAME_Q` is composed from `PHONE_Q` and the same two numbers the CSS
branch uses, so it cannot become a third hard-coded copy; a guard checks the
two files still agree. The weather pill is the deliberate exception and is
commented as one: the mobile header is width-keyed, so in landscape it is off
screen and the app bar is the only bar there is — moving the pill to the
frame's predicate would mount it inside a hidden header and the weather would
vanish exactly as it did in v4.93.

### "Jump to" was in the right place and still the wrong shape

Moving it to the top of the workspace fixed *where* it was. It was still a
horizontal strip of seventeen chips with their labels clipped, so reaching a
panel near the bottom of the page was a swipe hunt — the control whose job is
to save scrolling, needing a scroll of its own.

The tool picker had solved this exact problem, so the phone now gets that same
sheet: one button showing the section you are in and the count, opening a
searchable list whose rows wrap instead of clipping. The desktop keeps the
strip — there the labels fit, and the lit chip tells you where you are as the
page scrolls under it.

### A panel that disagreed with the clock two inches above it

At 7:26 on a trading morning the app bar said **Pre-market** and *Sold Into
Strength* said **"The market is closed for the day."** Both came from the same
process. The panel had one boolean — is the market open — and rendered the
evening wording for every value of "no".

`spike_scan.market_phase()` now names which of the four the clock is in —
`holiday`, `pre`, `open`, `post` — and both the wording and the panel's verdict
read it, so they cannot drift apart again. Before the bell the board says the
session has not opened yet and when it will fill in; the nine status facts
underneath fold into a disclosure when there is nothing to show.

A test in `test_spike_scan.py` asserts `market_phase()` and `elapsed_fraction()`
agree at eight clock times on both a full session and a half day. They are two
readings of the same bell, and the bug was that nothing made them say so.

## 12e. An auto margin cannot rescue a wrapped flex item (v4.99)

The first attempt at pulling the watchlist's `N shown` count onto the row it
counts was `flex: 0 0 auto; margin-left: auto`. A review bot pointed out that
this cannot work, and it was right:

> Auto margins are treated as **zero while flex lines are formed**, so
> `margin-left: auto` only right-aligns the item on whichever line it already
> occupies.

Measured on the live board, 370px wide:

| | Live v4.98 | With the fix |
|---|---|---|
| Row 1 | view tabs, 370px | view tabs, 370px |
| Row 2 | search **289** + Filters **75** = 370, full | search **210** + Filters **75** + count **73** |
| Row 3 | `1265 shown` | — |
| Height | **96px** | **76px** |

What decides it is the search box's flex **basis**. `flex: 1 1 auto` sizes it
from its content when the lines are formed — about 224px here — which is
already enough that the search and the button fill the row, and everything
after them wraps. `flex: 1 1 0` lets all three onto one line and the search
then grows into what is left.

### How it was verified, since the sandbox cannot see it

The stub's count reads `12 shown` and fits either way, so the test measures
76px with the bug and 76px without it. Widening the stub did not help either:
the board filters its rows against the real watchlist, so invented symbols are
dropped — 121 fake tickers rendered 2 cards.

So the candidate rule was injected into the **live page** through the Access
bridge and measured against the real 1265-name board. That is the honest way to
verify a fix whose trigger only exists in production, and it is why this one is
pinned as a **rule** in `test_ui_frame.js` rather than as a number: a guard that
measures 76 either way proves nothing, while a guard on the basis pins the
mechanism that actually decides it.

## 12d. The navigation was charging the sidebar rent (v4.99)

The section switcher — four rows of destinations, plus the opportunity ribbon
above it — spanned **both columns** at the foot of the top frame. The sidebar
therefore started underneath it, and paid for it: about **130 pixels of
sidebar** spent on a bar that only ever steers the workspace.

Measured at 1900×1200:

| | Before | After |
|---|---|---|
| Section bar | `x=24, w=1852` — the full window | `x=352, w=1524` — over the workspace |
| Sidebar | top `y=437`, **695px** tall | top `y=337`, **795px** tall |
| Workspace | top `y=437`, **695px** tall | top `y=437`, **695px** tall |

The bar now lives in a `.frame-col` alongside the workspace, over the thing it
steers, and the sidebar begins level with it. The opportunity ribbon moved with
it, and the posture card grew to run the top frame's full height.

**The workspace pays exactly what it paid before.** The bar crossed the frame
boundary; it did not take anything new. What changed is which column pays, and
the sidebar — which had been showing TICKER and WATCHLIST and cutting off
before PRESETS — now reaches RETURN BASELINE without scrolling.

What must not change, and is guarded: the bar is still **frame, not
workspace**. It sits outside `.main`, so it cannot scroll away, which is the
whole reason it left the document flow in §1. Reverting `.frame-col` to
`display: block` gives `749 not greater than or equal to 809: the sidebar is
749px against a 749px workspace — it did not gain the bar's height`.

Phone and landscape are untouched: the bar is `display: none` there and the
tool picker replaces it, so the measurements at 440×956 and 956×440 are
identical before and after.

## 12c. "Inside the workspace" is not the same as "visible" (v4.98)

v4.97 shipped the folded analyst board and the live Watchlist measured **409px
down a 415px workspace** — six pixels of a 120px card, a sliver under the filter
row. The guard passed, because it asked `top < height`, and six pixels satisfies
that. The rule it should have asked is *how much of the first card you can
actually read*.

What was still eating the screen, measured live at 440×956:

| | Live v4.97 | Why | After |
|---|---|---|---|
| Status line | 55px | `.wl-rescan-link` is a **38px tap target** inline in an 11.5px sentence, so the line box is 38px tall | 17px |
| Folded summary | 55px | three spans stacking — title, note, scan time | 30px |
| Filter row | 96px | wrapped to **three** rows: tabs, search, then the Filters button alone | 64px |
| Market summary | 36px | `flex-wrap: wrap` on a summary whose whole job is one line | 24px |
| Six margins | ~20px | none wrong alone | ~8px |

The card head already carries a **Scan now** button two rows above, so the
inline one was the same action twice; on a phone the count stays and the
duplicate button goes. `.ab-status` is monospaced, which is right for a column
of figures and wrong for a sentence. The full market wording is still there, one
tap inside.

**First card: 409 → 284, leaving 131px of it on screen.**

### The stub was gentle again — the third time this round

Reverting all of those changed the measurement by **one pixel** in the sandbox,
while live they were worth 125. The stub returned `{"rows": [...]}` and no
`status`, so the board drew "No scan yet …" and never drew the scan line or the
`N unscanned` hint — and the hint is the tallest thing on the real screen. The
stub now carries a `last_scan`, and the same revert then moves the card 38px,
which is exactly the height of the tap target.

§12 recorded this as a lesson about payload *size*. §12b recorded it about the
*clock*. This one is about payload *shape*: a field the stub omits is a branch
the test never renders. **Everything the real endpoint returns is part of the
fixture, not just the part the assertion reads.**

The floor is 64px — about half a card — and deliberately not the 131 now
measured. A floor is a regression line, not a target: it belongs in the gap
between the defect (6px) and the fix (131px), so polish above the line never
turns into a failing build.

## 12b. A bar with a missing price took the whole page down (v4.97)

`lightweight-charts` throws **"Value is null"** out of its Candlestick
constructor if any one of open/high/low/close is missing, and the throw escapes
into the page: one uncaught error per render attempt, a dozen in ten seconds,
and the chart never draws.

Three call sites hand bars to a candlestick series. Two filtered on
`close != null` — the field a forming bar is *least* likely to be missing — and
the third filtered nothing at all:

| | Before | |
|---|---|---|
| `charts.jsx` daily chart | `d.close != null` | one of four |
| `charts.jsx` intraday chart | `b.close != null` | one of four |
| `app-cards.jsx` day-bar chart | *nothing* | none of four |

One predicate now answers it for all three, and the same rule covers the
one-value line and area series, where `!= null` also lets `NaN` through:

```js
function isDrawable(v) { return v != null && typeof v === "number" && isFinite(v); }
function isCompleteBar(o, h, l, c) { return [o, h, l, c].every(isDrawable); }
```

**An incomplete bar is not drawn.** Carrying the previous close forward would
also silence the throw, but this is a trading app: a gap in the series is
honest, and a price nobody printed must never appear on a chart.

### What makes this one worth writing down

Every check in this repository passed at 7am and **twelve of them went red at
9:31** — the same suite, the same commit, the same machine. The bug needed an
incomplete bar to exist, and an incomplete bar only exists while the market is
open. For half an hour it looked like a regression in the change being worked
on; `git stash` and a rebuild proved it was not.

So the guard does not wait for a bell. `MockData.buildDaily` is wrapped before
the app loads and two bars in the middle of the series lose a price — one its
`open`, one its `high`, neither of them the newest row, because a guard that
only skips the last bar would pass while still breaking on a hole anywhere
else. Reverting to the close-only filter reproduces the crash on demand:
`AssertionError: [... 17 × 'pageerror: Value is null'] is not false`.

This is §12's lesson again, from the other direction. There, the sandbox was
*gentler* than production and hid a defect. Here it was gentler for twenty-two
hours a day and hid a crash. **The fixtures have to carry the hard case, because
the clock will not hand it to you on the run that matters.**

## 13. Not verified here — needs your phone

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
