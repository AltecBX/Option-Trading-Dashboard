# CURRENT_DESIGN_SYSTEM

Objective description of the visual system as implemented in `styles.css`
(~9,500 lines) and the component markup. No evaluation.

## Themes & color

- **Dual theme** via `data-theme` on the root: **light** (default tokens:
  `--bg #fafaf7`, cards `#ffffff`, warm near-black text in OKLCH) and **dark**
  (`--bg #0e1014`, cards `#161922`/`#1c2030`, near-white text). The user's primary
  daily mode is dark. A theme toggle lives in the sidebar; a density mode
  (`data-density="compact"`) reduces card padding.
- **Accent system**: one OKLCH accent driven by `--accent-h/c/l`, user-selectable
  presets — Emerald (default, h152), Indigo, Amber, Rose, Teal — applied app-wide
  (active tabs, buttons, focus rings, progress bars, highlights).
- **Semantic colors**: `--up` green (oklch 0.62/0.16/152), `--down` red
  (0.62/0.18/25), `--warn` amber (0.72/0.16/65). Gains/losses, deltas, stop/target,
  warn chips consistently use these.
- **Text tiers**: `--fg` primary, `--fg-2` secondary, `--fg-3` tertiary/labels,
  `--fg-4` faintest (captions, em-dashes). Borders: `--line`, `--line-2`.
- Special fixed colors: sites tab row + FV/TV/UW context in `#B4BFCC`; brand
  colors Finviz `#5B8DEF`, TradingView `#00BCD4`, Unusual Whales `#7FB8E6`.

## Typography

- **Body/display**: Inter (fallback Helvetica Neue/system).
- **Mono**: JetBrains Mono for ALL numerals-as-data, labels, chips, kickers,
  tickers — a signature of the app; tabular-nums used in metric tiles/tables.
- Scale: dense — labels/kickers 9–11px uppercase mono with letter-spacing;
  body/cells 11.5–13px; card titles ~15–18px display-weight; sidebar symbol 22px;
  price 17px. Uppercase mono "kicker" over a sentence-case `h2` is the standard
  card header pattern.

## Spacing, cards, borders, shadows

- Card padding `18×20px` (compact `12×14`), grid gap 16px, radii 8/12/16px.
- Cards: `--bg-2` surface, 1px `--line` border, layered soft shadows
  (`--shadow-card`, heavier diffuse elevation in dark), hover shadow lift, subtle
  top sheen in dark. Sections inside cards divide with 1px dashed `--line`.
- Motion: gentle cubic-bezier ease (`--ease`); `prefers-reduced-motion` honored
  (10 media rules); hover translateY(-1px) on pills.

## Navigation

- **Two-row tab bar** at top: row 1 = 15 app tabs (drag-to-reorder, order synced),
  active tab filled with accent; row 2 = dashed-top-bordered "Sites -" row in
  #B4BFCC with the three embedded-site tabs. On ≤760px each row scrolls
  horizontally (scrollbars hidden, edge mask removed).
- **Sidebar** fixed ~304px left column; on mobile it collapses/overlays
  (`nav-open` class). Contains all global controls (ticker, watchlist, presets,
  sliders, selectors).
- **Command palette** overlay for keyboard jumps.

## Components

- **Buttons**: small rounded rects (`rr-btn`, `sb-manage-btn`, chips) — mono
  9.5–12px, 1px border, transparent/bg-3 fill, accent border+text on hover/active;
  brand-colored variants for FV/TV/UW; pill buttons (`emx-chip`, warn variants
  amber/red-bordered).
- **Badges/status chips**: uppercase mono pills — SCHWAB LIVE (green), UW rate,
  version pill, weather pill, earnings chip (amber "soon" state), stale tags,
  "cookies: compat mode", helper-update warnings (⚠ amber), reliability labels
  (green reliable / amber unstable / orange weakening / red likely-random),
  actionability score chip (green ≥70 / amber ≥50 / red <50), DEFINED/UNDEFINED
  risk labels, TRIGGERED (green) vs quiet.
- **Tables**: dense, left-aligned, 1px row borders, sticky mono uppercase headers
  (some sortable via `SortableTh` with sort indicators), tabular numerals,
  green/red value coloring, horizontal scroll wrappers on overflow, max-height
  scroll areas for long logs (trades table 340px).
- **Charts** (custom SVG/canvas in `charts.jsx` + inline SVGs): daily price chart
  with EM band overlay; 1-minute chart with VWAP and level lines; equity curve
  polyline (green/red by outcome, dashed baseline); pattern occurrence
  "spaghetti" charts (grey occurrence lines, bold accent/semantic mean line, thin
  median, dashed p25/p75 band, dashed signal marker, zero line); theta/skew and
  P/L visuals; sparkline-scale mini charts on boards. Axis labels mono 10px.
- **Forms/inputs**: dark-surface inputs with 1px border, accent focus ring
  (`--accent-ring`), mono numerics; textareas for NL strategy/Ask; dropdown
  selects native-styled; sliders native with accent.
- **Modals**: watchlist manager (and pattern/journal dialogs) as centered
  overlays with the card language.
- **Toasts**: bottom overlay stack for radar signals.
- **Tooltips**: native `title` attributes universally, plus a styled `.tip`
  element in places; long-form multi-line explanations are common (methodology
  written into hovers). Cursor `help` marks hoverable stats.

## States

- **Loading**: skeleton/progress patterns vary — top progress bar on main column,
  inline "analyzing…/running…" button labels, animated progress bars with
  phase/count text (backtest, intraday mining), board status lines ("scanning
  n/total").
- **Empty**: explanatory sentences rather than blank space ("No statistically
  supported patterns… that itself is information"), setup panels when the helper
  extension is absent, "No starred tickers" hint.
- **Error**: per-card error boundary cards (red heading, message, Retry), amber
  warn boxes (`bt-warn`) with ⚠ lines, red-bordered variants for hard errors,
  API-level notes ("rate-limited — showing the last scan"), full-app crash screen
  (dark, red title, stack, Reload/Try-again).
- **Stale-not-blank** is a deliberate global pattern: boards keep last-good rows
  with amber notes instead of emptying.

## Responsive behavior

- Breakpoints in active use: **≤700/720/760px** (phone: single column, tab rows
  scroll, sidebar overlay, per-row masks off), **≤800/900px** (touch targets:
  buttons get ≥38px min-height and larger padding; watchlist action row wraps),
  **≤1100px** (grids collapse to one column; roll-grid etc.), **min-width** rules
  add side mini-rails (52-week/daily high-low rails) only on very wide screens.
- Desktop is the design center: multi-card vertical stacks in a single main
  column, sidebar always visible, data-dense tables.
- Tablet: intermediate — sidebar persists, grids mostly single-column.
- Mobile: fully usable for monitoring (boards, cards, alerts); embedded sites
  intentionally unavailable (explained in-place with an external link);
  logo/ticker block stacks vertically.

## Iconography

- No icon library: unicode glyphs (☆ ★ ⚑ ⌕ ↺ ⧉ ▸ ▾ ⚠ ✕ ● ⛏ →) and text chips
  do the work; brand logos only for company logos (Schwab-sourced) and the Jerry
  brand mark.

## Voice

- UI text is first-person-practical, plain-language, and explanatory; warnings are
  blunt ("likely random", "UNDEFINED risk", "modeled premiums — treat as
  estimates"). Numbers dominate; sentences appear in tooltips, notes, and
  pattern/backtest explanations.
