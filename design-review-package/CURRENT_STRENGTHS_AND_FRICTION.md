# CURRENT_STRENGTHS_AND_FRICTION

Factual observations about the present interface. No redesign proposals.

## Currently clear and effective

- **One global ticker drives everything**, including the embedded partner sites
  (two-way). Switching context is one click from almost anywhere.
- **Tooltips everywhere**: every metric, chip, and button self-explains; complex
  statistics (q-values, first-touch, folds) carry full methodology in hover text.
- **Honest data states**: stale-not-blank boards, "rate-limited" notes, "likely
  random" labels, modeled-price warnings, explicit can't-test refusals. The user
  can generally trust that what is shown is what is known.
- **Semantic color discipline**: green/red/amber usage is consistent app-wide;
  mono numerals with tabular figures keep tables scannable.
- **Alerting works without watching**: toasts, push, TRIGGERED badges, earnings
  chip — the app is useful even when not looked at.
- **Tabs stay mounted**: switching is instant; embedded sites never reload; the
  drag-to-reorder tab bar lets the user put their daily loop first.
- **Data-integrity guardrails**: add-only stars outside Manage; defined-risk-first
  ordering; the $5B floor enforced in scanners rather than remembered by the user.

## Difficult to understand (first exposure)

- The **difference between Discover, Scanners, Watchlist alerts, and Streaks** —
  four surfaces that all answer "what should I look at?" with different engines.
- **Radar score composition**: five group chips with abbreviated names require
  tooltip reading to interpret.
- **Pattern statistics density**: a single expanded row shows ~15 tiles (p/q,
  CI, folds, EV, MFE/MAE, first-touch…). Meaning is documented but the visual
  hierarchy of "which number matters most" is flat.
- **0DTE Juice vs Flow premium-richness** overlap in purpose; naming doesn't
  disambiguate.
- Some legacy names persist ("CRDO pattern" on the open-reclaim card, "kicker"
  labels like "Premium selling") that assume the owner's context.

## Requires many clicks / tab hops

- Confirming a radar signal typically spans **Scanners → Trade (1-Min) → Flow →
  Patterns** — four tabs for one decision.
- Entry/stop/target/invalidation levels live on **four different surfaces**
  (radar ticket, Current Setup, EM card, 1-min chart levels) with no merged view.
- A full single-stock review touches 4–6 tabs (no consolidated stock summary).
- Options workflow crosses sidebar controls (expiration/strike picker) and Trade
  cards — vertical scrolling plus sidebar interaction.

## Repeated information

- Ticker price/change renders simultaneously in the sidebar, chart header, EM
  card, and scanner rows.
- EM values appear on the EM card, the chart band, juice rows, pattern option
  ideas, and radar context.
- Earnings proximity appears in the tab bar chip, watchlist board, juice boost,
  and pattern Current Setup flag.
- Watchlist status (starred/on-list) appears in sidebar, embed toolbars, and
  boards.

## Possibly missing information

- No merged per-stock "levels" view (all supports/resistances/targets in one
  place).
- No position-size / dollar-risk calculator tied to tickets or first-touch odds.
- Journal entries are not automatically linked back to the signal (radar/pattern)
  that produced the trade idea.
- No cross-stock ranking of Current Setups (each stock's setups are per-ticker;
  the radar covers intraday only).
- Historical earnings/news/IV context is absent by data limitation (documented).

## Competes for attention

- The Trade tab stacks 10+ cards of mixed importance in one scroll column.
- On Scanners, both radar lists, group chips, tickets, and report card all render
  at once.
- The sidebar carries identity, watchlist, presets, and four option-control
  sections simultaneously — always visible regardless of task.
- Toasts, the earnings chip, warn chips, and stale tags can all fire at once
  during volatile periods.

## Visually inconsistent (factual instances)

- Mixed control styles: `rr-btn` vs `sb-manage-btn` vs `emx-chip` vs `fv-chip`
  share a look but differ in size/padding/casing across tabs.
- Chart implementations vary (canvas daily chart, SVG spaghetti/equity charts,
  mini bars) with slightly different axis/label treatments.
- Some cards use kicker+h2 headers; embedded panels intentionally use none;
  boards use status lines — three header conventions.
- Unicode-glyph iconography is inconsistent in weight/optical size (⌕ vs ⚑ vs ↺).

## May overwhelm

- Watchlist board: 1,276 rows × many columns on one screen.
- Pattern rows expose research-grade statistics to a trading-decision context.
- The Backtest rules editor exposes every parameter (good control, high density).

## Hard to use quickly during live trading

- Reading a radar row's ticket, then cross-checking EM room and flow requires
  leaving the row; there is no in-row "everything about this signal" state.
- Long vertical scrolls on Trade to reach the trade builder while watching a
  1-minute chart higher up.
- Text-dense tooltips are the primary explanation mechanism — hovering costs time
  mid-session and doesn't exist on touch.

## Mobile-specific

- Embedded sites are unavailable (by platform limitation; explained in-place).
- Dense tables (watchlist, juice, trades log) require horizontal scrolling.
- Tooltip-based explanations have no touch equivalent.
- The two tab rows consume vertical space on small screens; the sidebar becomes
  an overlay that hides the chart while adjusting controls.
- Radar rows' multi-chip layout wraps heavily on narrow widths.

## Hidden / hard to discover

- The command palette (keyboard-only entry).
- Drag-to-reorder tabs; tap-weather-to-toggle-location; density mode.
- The Ask box's supported vocabulary (discoverable only by trying or hovering).
- Pattern → "⌕ Scan watchlist" inline compare; "→ Options backtest".
- The Tweaks panel's full contents behind a small sidebar icon.
- The Repair-session / Sign-in-popup mechanics on the TradingView tab (necessary
  but esoteric).
