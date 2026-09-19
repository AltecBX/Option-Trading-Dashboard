// test_ui_frame.js (v4.92) — the permanent frame, pinned.
//
// Jerry's first requirement is that ten market charts, four high/low rails
// and two bottom feeds are present on every destination. Before the frame
// they WERE all rendered — the whole document scrolled, so on a page twelve
// thousand pixels tall they were gone within one flick of the wheel, and on
// a phone the bottom tapes were the last element of a 22,000-pixel document.
// "It renders" was true and useless. These guards pin the properties that
// make it true in practice:
//
//   1. The shell is a fixed-height three-row grid and the WORKSPACE is the
//      only scrolling box. If the page itself can scroll again, the frame is
//      decorative.
//   2. The ten charts are a real grid at every width — five columns on a
//      desktop, two in portrait — and never the carousel that showed three
//      of ten.
//   3. Nothing reads window.scrollY for the workspace any more. That call
//      would return zero forever and silently move nothing.
//   4. The four rails have a mount point at BOTH widths. Below 2080px they
//      used to be display:none — fetched, mounted, and unreachable.
//   5. Panels are sized against the workspace, not the viewport. A vh-sized
//      panel now overflows its own scroll box by the height of the frame.
//   6. The navigation groups are a PARTITION of the thirty destinations.
//      A destination in no group is a destination that vanishes from the bar.
//   7. The version comes from the one existing source, and is shown once.
//
// Run from the repo dir:  node test_ui_frame.js
const fs = require("fs");
const path = require("path");

let passed = 0, failed = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — got: " + extra : "")); }
}
const read = (f) => fs.readFileSync(path.join(__dirname, f), "utf8");

const app = read("app.jsx");
const lib = read("app-lib.jsx");
const cards = read("app-cards.jsx");
const css = read("styles.css");

// ── 1. the shell is the frame, and only the workspace scrolls ─────────────
ok("the shell is a three-row grid the height of the viewport",
   /\.shell \{[\s\S]{0,400}?grid-template-rows: auto minmax\(0, 1fr\) auto/.test(css)
   && /\.shell \{[\s\S]{0,400}?height: 100dvh/.test(css));
ok("the page itself no longer scrolls",
   /body \{ overflow: hidden;/.test(css));
ok("the workspace is the scrolling box",
   /\.main \{[\s\S]{0,220}?overflow-y: auto/.test(css));
ok("the sidebar scrolls inside the frame instead of sticking to the page",
   /\.sidebar \{\s*\n\s*position: static;/.test(css));
ok("the section bar rides in the frame, not on a sticky offset",
   /\.tab-bar \{ position: static;/.test(css));
ok("the three frame regions exist in the shell",
   /className="frame-top"/.test(app) && /className="frame-body"/.test(app)
   && /className="frame-bottom"/.test(app));
ok("the ten charts are inside the top frame, not the workspace",
   app.indexOf('className="frame-top"') < app.indexOf("<MarketOverview")
   && app.indexOf("<MarketOverview") < app.indexOf('className="frame-body"'));
// The section bar used to span BOTH columns at the foot of the top frame, so
// the sidebar began below it and lost ~130px to a bar that only ever steers
// the workspace. It now rides in the workspace's own column. What must not
// change is that it is still FRAME: outside `.main`, so it cannot scroll away
// — which is the whole reason it left the document flow in the first place.
ok("the section bar rides in the workspace column, above the workspace",
   /<div className="frame-col">\s*\n\s*<TabBar/.test(app));
ok("and it is still outside the scrolling box, so it cannot scroll away",
   app.indexOf('<div className="frame-col">') < app.indexOf('<main className="main">')
   && app.indexOf("<TabBar") < app.indexOf('<main className="main">'));
ok("the column is a flex column so the workspace keeps the remaining height",
   /\.frame-col \{[^}]*display: flex;[^}]*flex-direction: column;/.test(css)
   && /\.frame-col > \.main \{ flex: 1 1 auto; \}/.test(css));
ok("both bottom feeds are inside the bottom frame",
   app.indexOf('className="frame-bottom"') < app.indexOf("<NewsTicker")
   && /\.mn-stack\.mn-bottom \{\s*\n?\s*position: static;/.test(css));

// ── 2. ten charts, a grid at every width ──────────────────────────────────
ok("wide desktops get five columns",
   /\.mko-grid \{ grid-template-columns: repeat\(5, 1fr\)/.test(css));
ok("portrait phones get two columns — a grid, not a carousel",
   /\.mko-grid, \.mko-grid-skel \{\s*\n\s*display: grid !important;\s*\n\s*grid-template-columns: 1fr 1fr;/.test(css));
ok("the phone rule overrides the older scroll-snap strip",
   css.lastIndexOf("scroll-snap-type: none") > css.lastIndexOf("scroll-snap-type: x mandatory"));
ok("a strip that fails to load keeps its ten tiles rather than vanishing",
   /mko-grid-skel/.test(cards)
   && /Array\.from\(\{ length: 10 \}\)/.test(cards));
ok("the strip says whether it is loading or not answering",
   /Not answering/.test(cards) && /const \[state, setState\] = useState\("loading"\)/.test(cards));
ok("the news tape also keeps its frame and says which",
   /feedState === "down"/.test(cards) && /nt-quiet/.test(css));

// ── 3. the workspace is what scrolls, everywhere ──────────────────────────
ok("tab scroll memory reads the workspace, not the window",
   /tabScroll\.current\[prev\] = workspaceScrollTop\(\)/.test(app)
   && !/tabScroll\.current\[prev\] = window\.scrollY/.test(app));
ok("'back to top' scrolls the workspace",
   /onClick=\{\(\) => scrollWorkspaceTo\(0, true\)\}/.test(app));
ok("no window.scrollTo survives in app.jsx",
   !/window\.scrollTo\(/.test(app), (app.match(/window\.scrollTo\([^)]*\)/g) || []).join(","));

// ── 4. the four high/low lists are reachable at both widths ───────────────
// The rule is that the rails are CONDITIONAL — they mount as fixed columns
// only where a fixed column makes sense. Which predicate answers that is
// section 21b's business; pinning the name here sent this red the moment the
// answer stopped being width alone, which is the third time in this file a
// guard has pinned a spelling instead of a rule.
ok("the rails mount as fixed columns only on a desktop",
   /\{!(isPhone|phoneFrame) && \(\s*\n\s*<React\.Fragment>\s*\n\s*<ExtremeRail kind="high52"/
     .test(app));
ok("on a phone the same four lists mount inside the workspace",
   /<HighLowCard apiFetch=\{apiFetch\}/.test(app) && /function HighLowCard/.test(cards));
ok("all four lists are offered there, not a subset",
   (cards.match(/kind: "(high52|dailyHigh|low52|dailyLow)"/g) || []).length >= 4);
ok("the panel form draws the same rows through the same component",
   /variant === "panel"/.test(cards) && /variant="panel"/.test(cards));
ok("a rail colour is a token, so the card and the rail cannot drift apart",
   /--rail-high:/.test(css) && /--rail-low:/.test(css)
   && /\.hlc-high\s+\{ background: var\(--rail-high\); \}/.test(css));

// ── 5. panels are sized against the workspace ─────────────────────────────
ok("the frame publishes its measured workspace height",
   /setProperty\("--ws-h"/.test(app));
ok("the embedded partner view fills the workspace rather than the viewport",
   /\.fv-card\.fv-live \{[\s\S]{0,200}?height: calc\(var\(--ws-h/.test(css));
ok("the wide-table scrollers are workspace-relative too",
   /\.wl-scroll, \.waa-table-wrap, \.wstk-wrap \{\s*\n\s*max-height: calc\(var\(--ws-h/.test(css));
ok("the rails stop where the feeds start",
   /bottom: calc\(var\(--frame-bottom-h, 92px\) \+ 6px\)/.test(css));

// ── 6. the navigation groups are a partition ──────────────────────────────
const tabsBlock = lib.slice(lib.indexOf("const TABS = ["), lib.indexOf("const TAB_KEY"));
const tabIds = [...tabsBlock.matchAll(/\{ id: "([a-z]+)", label: "/g)].map(m => m[1]);
const groupBlock = lib.slice(lib.indexOf("const TAB_GROUPS"), lib.indexOf("class RootErrorBoundary"));
const grouped = [...groupBlock.matchAll(/"([a-z]+)"/g)].map(m => m[1])
  .filter(x => tabIds.includes(x));
ok("every destination is still declared", tabIds.length === 30, String(tabIds.length));
ok("every destination belongs to exactly one group",
   tabIds.every(id => grouped.filter(g => g === id).length === 1),
   tabIds.filter(id => grouped.filter(g => g === id).length !== 1).join(","));
ok("no group names a destination that does not exist",
   grouped.every(g => tabIds.includes(g)));
ok("the four groups are the ones the reference shows",
   /label: "Workspace"/.test(lib) && /label: "Scan"/.test(lib)
   && /label: "Research"/.test(lib) && /label: "Connected"/.test(lib));
ok("the bar is grouped rows and the selected state is unmistakable",
   /tab-bar-grouped/.test(cards) && /\.tab-bar-grouped \.tab-btn\.active \{/.test(css));
ok("drag-to-reorder survives, within a group",
   /groupOf\(dragId\) === groupOf\(t\.id\)/.test(cards));
// v5.17. Jerry, from his phone: "I can't do anything on my mobile phone.
// Please optimize it so I use it like I use it on my desktop." The bar was
// display:none on phones with the picker in its place — one tap away and
// invisible. The phone now shows the same grouped bar, compact, and keeps
// the picker for search.
ok("the phone shows the desktop bar, compact, and keeps the searchable picker",
   /@media \(max-width: 900px\) \{\s*\.tab-bar, \.tab-bar-grouped \{\s*display: block !important;/.test(css)
   && !/\.tab-bar, \.tab-bar-grouped \{ display: none !important; \}\s*\}\s*\n\s*\/\* The tape/.test(css)
   && /tabsheet-find/.test(app) && /toolFind/.test(app));
ok("on the phone the market band folds so the tool is first, and stays mounted",
   /<details className="card phone-band">/.test(app) && /\{marketBand\}[\s\S]{0,200}<HighLowCard/.test(app)
   && /\.phone-band > summary \{/.test(css));
ok("the phone header carries the desktop's Focus switch",
   /className=\{`mh-btn mh-focus\$\{focusFrame \? " on" : ""\}`\}/.test(app)
   && /body\.focus-frame \.mko-head \{ flex-direction: column/.test(css));
ok("the picker is grouped the same way, so nothing is hidden behind a menu",
   /TAB_GROUPS\.map\(g => \{/.test(app));
// v5.18. Jerry, on v5.17: "But the bottom is still cut off." The body
// carried the iPhone's safe-area insets as padding while the shell was one
// screen tall, so the shell ran past the bottom edge by the top inset and
// the action bar lost its lower half. The insets belong inside the shell.
ok("the body no longer wears the safe-area insets as padding",
   !/body \{\s*padding: env\(safe-area-inset-top\)/.test(css));
ok("the shell absorbs the top inset inside its own height",
   /\.shell \{\s*padding: calc\(8px \+ env\(safe-area-inset-top, 0px\)\)/.test(css)
   && /\.frame-bottom \{[^}]*padding-bottom: env\(safe-area-inset-bottom, 0px\)/.test(css));

// v5.19. Jerry: "The P/E 153.1 · Fwd 76.5 should always be on 1 line. Also
// I want to put the YTD % underneath this." The line sat in the ~120px
// price column of the phone drawer and wrapped; it now runs under the
// whole ticker row, with YTD beneath it from the live price.
ok("the P/E line and the YTD line sit under the ticker row, not in the price column",
   /<\/div>\s*\{\/\* v5\.19[\s\S]{0,1200}className="sb-ratios"/.test(app)
   && /className=\{`sb-ytd \$\{ytd >= 0 \? "up" : "down"\}`\}/.test(app));
ok("neither line may wrap",
   /\.sb-pe \{[^}]*white-space: nowrap/.test(css) && /\.sb-ytd \{[^}]*white-space: nowrap/.test(css));
ok("YTD is the live price against last year's final close from the payload",
   /\(currentPrice - base\) \/ base/.test(app) && /current\.ytd_base/.test(app));

// v5.20. Jerry: "Now make the YTD show on the watchlist chips too." The
// chips read the same thing the ticker card does — the live price against
// last year's final close — from a base the server caches per day.
ok("the watchlist chips carry a year-to-date reading",
   /className=\{`pp-ytd \$\{y >= 0 \? "up" : "down"\}`\}/.test(app)
   && /const y = ytdPctFor\(t\);/.test(app));
ok("a chip's percentage is the live price against the base, with the close as fallback",
   /const px = liveQuotes\[sym\]\?\.last \?\? row\.last;/.test(app)
   && /\(\(px - row\.base\) \/ row\.base\) \* 100/.test(app));
ok("the bases are fetched, not polled like a quote",
   /\/api\/ytd_base\?tickers=/.test(app) && /setInterval\(skipWhenHidden\(fetchBases\), 3600000\)/.test(app));
// Codex on #407: the server leaves out a symbol it has no base for, so
// merging the answer left the OLD base in place — and on the first refresh
// of a new year that old base is last year's, so the chip would report the
// whole of last year as this year's move.
ok("a symbol the server leaves out loses its old base rather than keeping it",
   /for \(const sym of asked\) delete next\[sym\];/.test(app)
   && /const asked = starredSymbols\.slice\(\);/.test(app));
ok("the selected chip's number borrows the chip's colour instead of fighting it",
   /\.preset-pill\.active \.pp-ytd \{ color: inherit/.test(css));

// ── 7. one version, from the one source ───────────────────────────────────
ok("the status line shows the app's real version",
   /className="statusline"/.test(app) && /v\{APP_VERSION\}/.test(app));
ok("the version is shown once, not duplicated into the sidebar as well",
   (app.match(/v\{APP_VERSION\}/g) || []).length === 1,
   String((app.match(/v\{APP_VERSION\}/g) || []).length));
ok("no connection status was invented beside it",
   !/All systems operational/.test(app));

// ── 8. the shared vocabulary exists and is used ───────────────────────────
ok("there is one data-status vocabulary",
   /const DATA_STATUS = \{/.test(lib)
   && ["live", "close", "cached", "modeled", "measured", "loading", "none", "pending"]
        .every(k => new RegExp(`\\b${k}:\\s*\\{ label:`).test(lib)));
// "Nothing has run yet" and "the source refused to answer" are different
// claims, and the second one is an alarm. A board that has simply not been
// scanned yet must not wear the fault wording.
ok("a board that has not run yet is not called unavailable",
   /kind=\{data\.scanning \? "loading" : data\.as_of \? "cached" : "pending"\}/.test(
     read("tab-spike.jsx"))
   && /pending:\s*\{ label: "Not scanned yet"/.test(lib));
ok("a status carries the time the RESULT belongs to",
   /function DataStatus\(\{ kind, at, note, label \}\)/.test(lib));
ok("dates in it are spelled out, never ISO",
   /month: "long", day: "numeric", year: "numeric"/.test(lib));
ok("long explanations are reachable, not deleted",
   /function PanelMethod/.test(lib) && /\.panel-method > summary/.test(css));
ok("a board's answer has one compact shape",
   /function PanelVerdict/.test(lib) && /\.panel-verdict \{/.test(css));

// ── 9. long pages have a local index ──────────────────────────────────────
ok("the index is built from the rendered page, not a hand-kept list",
   /querySelectorAll\(`\.tab-panel\[data-tab="\$\{tab\}"\]`\)/.test(lib));
ok("it walks every panel of a destination, not just the first",
   /roots\.forEach\(/.test(lib));
ok("a long heading is shortened, never dropped",
   /const short = text\.length > 34/.test(lib)
   && !/text\.length > 44/.test(lib));
ok("Trade and Scanners both get one",
   /<SectionNav tab="trade"/.test(app) && /<SectionNav tab="scanners"/.test(app));
ok("the Trade groups match on a prefix, so live detail cannot unfile a panel",
   /h\.startsWith\(p\)/.test(app));

// ── 10. the watchlist keeps all forty-six columns ─────────────────────────
ok("presets are views over the same columns, with an All columns view",
   /const WL_PRESETS = \[/.test(cards) && /id: "all"[\s\S]{0,120}cols: null/.test(cards));
ok("the visible set follows the PRESET, not a stale hidden list",
   /const p = WL_PRESETS\.find\(x => x\.id === wlPreset\);/.test(cards));
ok("symbol cannot be hidden and stays put while scrolling sideways",
   /if \(k === "symbol"\) return;/.test(cards)
   && /\.wl-table th\.wl-pin, \.wl-table td\.wl-sym \{\s*\n\s*position: sticky; left: 0;/.test(css));
ok("the phone summary exposes every field rather than dropping any",
   /All \$\{orderedCols\.length\} fields/.test(cards)
   && /orderedCols\.map\(c => \(\s*\n\s*<tr key=\{c\.k\}>/.test(cards));

// ── 11. no symbol's numbers under another symbol ──────────────────────────
ok("a symbol with no payload yet gets an empty placeholder, not another's data",
   /placeholder: true,/.test(app)
   && !/const fallback = Object\.keys\(window\.MockData\?\.PRESETS \|\| \{\}\)\[0\]/
        .test(app.slice(app.indexOf("const dataset = useMemo"),
                        app.indexOf("const dataPending"))));
ok("the panels are not even built while that is true — JSX children are eager",
   /\{dataPending \? \(/.test(app) && /\) : \(\(\) => \(<React\.Fragment>/.test(app));
ok("the sidebar's identity and price are gated on it too",
   /\{loadError \|\| dataPending/.test(app));
ok("a failed fetch says so in the same place instead of drawing empty panels",
   /`No data for \$\{ticker\}`/.test(app));

// ── 12. nothing in the frame hangs off a position that moved ──────────────
//
// The weather pill was `position: absolute` pinned to the sidebar's corner.
// The frame made the sidebar `position: static`, so the pill had nothing to
// hang from, went to the corner of the WINDOW, and hid under a fixed rail.
// It rendered the whole time; it was simply somewhere nobody looks. The rule
// worth keeping is that it lives in a bar as an ordinary inline control, and
// that exactly one of them is ever mounted — display:none does not unmount a
// component, so two would mean two forecast fetches and two geolocation
// prompts with a toggle that only moved one of them.
ok("the weather is in a bar, not hung off some ancestor's position",
   /<WeatherBadge variant="bar" \/>/.test(app)
   && /\.sb-weather-pill\.wx-inline\s*\{[^}]*position:\s*static/.test(css));
ok("exactly one weather pill is mounted, chosen by the same 900px line",
   /\{!isPhone && <WeatherBadge variant="bar" \/>\}/.test(app)
   && /\{isPhone && <WeatherBadge variant="bar" \/>\}/.test(app)
   && (app.match(/<WeatherBadge/g) || []).length === 2);

// ── 13. the two clocks agree ──────────────────────────────────────────────
//
// The app bar's clock said 3:30 AM while the clock beside the Schwab badge
// said 3:30:53 — two clocks on one screen, disagreeing about what time it is.
// Same seconds, same one-second tick, same green-while-open tell.
ok("the app bar clock shows seconds, like the one beside the Schwab badge",
   /second: "2-digit"/.test(app.slice(app.indexOf("function MarketClock"),
                                      app.indexOf("function MarketClock") + 2200)));
ok("it ticks every second and stops while the tab is hidden",
   /setInterval\(\(\) => \{ if \(!document\.hidden\) setNow\(Date\.now\(\)\); \}, 1000\)/
     .test(app.slice(app.indexOf("function MarketClock"),
                     app.indexOf("function MarketClock") + 2200)));
// v5.11: the app bar's green-while-open tell moved off the time and onto the
// state pill, which is the thing it was describing. The clock beside the
// Schwab badge still greens its own time; both still say the same thing.
// v5.12: the tell is the DOT now, not the label — one coloured thing in
// the row instead of two saying the same thing.
ok("and turns green during the regular session, like the other one",
   /const cls = open \? "open" : \(pre \|\| post\) \? "ext" : "shut";/.test(app)
   && /className=\{`ab-mkt ab-mkt-\$\{cls\}`\}/.test(app)
   && /\.ab-mkt-open \.ab-mkt-dot \{ background: var\(--up\); \}/.test(css)
   && /\.lc-time\.mkt-open\s*\{[^}]*var\(--up\)/.test(css));

// ── 14. the content column uses what the rails leave ──────────────────────
//
// The rails are FIXED to the window edges; the content was capped at a fixed
// 1600px and centred. Those two numbers only line up at one screen width —
// past about 2400px the rails stop growing and the leftover margin is dead.
// The rule: one definition of the rail width, and the content's cap is
// derived from it rather than being a second hard-coded number.
ok("the rail width is defined once, not copied into two formulas",
   (css.match(/--rail-w:\s*min\(calc\(\(100vw - 1600px\)/g) || []).length === 1);
ok("the content column is sized from what the rails leave over",
   /\.frame-top, \.frame-body \{\s*\n?\s*max-width: calc\(100vw - 2 \* \(2 \* var\(--rail-w\)/
     .test(css));
// The first draft wrote `min(2200px, calc(100vw - …))`. Above 2988px the cap
// won, the rails stayed at 190px, and the dead band came straight back — 438px
// on each side at 3840px. A second hard-coded number is the bug, not a
// safeguard against it.
ok("and not re-capped at some other fixed number that would bring the band back",
   !/\.frame-top, \.frame-body \{[^}]*max-width:[^;]*min\(\s*\d+px/.test(css));

// ── 15. the text tiers are readable, by measurement ───────────────────────
//
// "Secondary labels are still faint" is not a matter of taste — it is a
// contrast ratio, and two of the four tiers failed WCAG AA against the
// surfaces they sit on. --fg-4 in particular carries the SMALLEST type in the
// app: the nine- and ten-pixel mono captions. This computes the real ratio
// from the tokens, so the next person to nudge a lightness gets told.
function oklchToSrgb(L, C, H) {
  const h = (H * Math.PI) / 180;
  const a = C * Math.cos(h), b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
  const lin = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ];
  return lin.map(v => Math.min(1, Math.max(0, v)));
}
const relLum = (rgbLinear) =>
  0.2126 * rgbLinear[0] + 0.7152 * rgbLinear[1] + 0.0722 * rgbLinear[2];
function hexLum(hex) {
  const h = hex.replace("#", "");
  const ch = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255);
  return relLum(ch.map(c => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)));
}
const contrast = (a, b) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);

// Read the tokens straight out of the stylesheet — a hard-coded copy here
// would pass while the app got darker.
function token(block, name) {
  const m = block.match(
    new RegExp("--" + name + ":\\s*oklch\\(([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\)"));
  return m ? [parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])] : null;
}
function surfaces(block, names) {
  const out = {};
  for (const n of names) {
    const m = block.match(new RegExp("--" + n + ":\\s*(#[0-9a-fA-F]{6})"));
    if (m) out[n] = m[1];
  }
  return out;
}
// The token blocks, sliced by their opening selector rather than matched with
// a brace-counting regex: styles.css has many `:root` rules, and only the one
// that declares --bg is the palette.
function paletteBlock(selector) {
  let i = -1;
  for (;;) {
    i = css.indexOf(selector + " {", i + 1);
    if (i < 0) return "";
    const end = css.indexOf("\n}", i);
    const block = css.slice(i, end < 0 ? css.length : end);
    if (/--bg:\s*#/.test(block) && /--fg-4:/.test(block)) return block;
  }
}
const LIGHT = paletteBlock(":root");
const DARK = paletteBlock('[data-theme="dark"]');
// Every tier is checked against the WORST surface it lands on. A chip
// (--bg-3) is lower contrast than a card, and the small mono captions sit
// on chips, so checking only the card would miss exactly the text at issue.
for (const [themeName, scope] of [["light", LIGHT], ["dark", DARK]]) {
  const surf = surfaces(scope, ["bg", "bg-2", "bg-3"]);
  const surfLums = Object.values(surf).map(hexLum);
  ok(`${themeName}: the three text surfaces are declared as hex`,
     Object.keys(surf).length === 3);
  for (const tier of ["fg-2", "fg-3", "fg-4"]) {
    const t = token(scope, tier);
    if (!t) { ok(`${themeName}: --${tier} is an oklch token`, false); continue; }
    const lum = relLum(oklchToSrgb(t[0], t[1], t[2]));
    const worst = Math.min(...surfLums.map(s => contrast(lum, s)));
    ok(`${themeName}: --${tier} clears WCAG AA on every surface `
       + `(${worst.toFixed(2)}:1)`, worst >= 4.5);
  }
  // The tiers must still be a LADDER: fixing contrast by flattening them into
  // one colour would pass the check above and destroy the hierarchy it is for.
  const ls = ["fg-2", "fg-3", "fg-4"].map(t => (token(scope, t) || [0])[0]);
  ok(`${themeName}: the quiet tiers stay distinguishable from each other`,
     Math.abs(ls[0] - ls[1]) >= 0.04 && Math.abs(ls[1] - ls[2]) >= 0.04);
}

// ── 16. a panel's four jobs are not four equal voices ─────────────────────
ok("the answer is set larger than the prose around it",
   /\.panel-verdict \{[^}]*font-size: 14px/.test(css)
   && /\.card-sub \{[^}]*font-size: 11\.5px/.test(css));
ok("the heading is the full-strength colour, not a secondary tier",
   /\.card-title \{[^}]*color: var\(--fg\);/.test(css));
ok("methodology is a disclosure, not a permanent paragraph",
   (cards.match(/className="panel-method fv-method"/g) || []).length >= 4
   && !/className="fv-hint"[^>]*title="If TradingView/.test(cards));

// ── 17. the empty analyst board does not own the first screen ─────────────
ok("an analyst board with nothing on it collapses to one line",
   /const quiet = sorted\.length === 0;/.test(cards)
   && /<details className="card waa-card waa-quiet">/.test(cards));
// The first version of that gate was `sorted.length === 0 && !isScanning`, so
// a scan in flight blew the board back up to 403px and put the stocks below
// the fold — and which load you got was a race. Rows or no rows is the whole
// question; the scan is a word in the summary line.
ok("and a scan in flight does not blow it back up",
   !/const quiet = [^;]*isScanning/.test(cards));
// The board is folded on a phone whether or not it has rows: the live one is
// 629px of thirteen-column table on an ordinary morning, and folding only the
// empty case fixed the screenshot and not the destination.
ok("a populated board folds on a phone too, not just an empty one",
   /if \(waaPhone && sorted\.length > 0\) \{/.test(cards)
   && /<details className="card waa-card waa-quiet waa-fold">/.test(cards));
// …and the summary line has to keep showing the COUNT while that happens.
// /api/watchlist_analyst returns `scanning: true` alongside cached rows during
// the scheduled morning scan, and the first draft led with the scan state, so
// a board with six actions on it summarised itself as "scanning…".
ok("a scan qualifies the count rather than replacing it",
   /const quietLine = sorted\.length > 0/.test(cards)
   && /const scanTail = isScanning \? " · scanning…" : "";/.test(cards));
// A card that MOUNTS during someone else's scan has to watch it end too —
// polling used to be created only inside startScan, so such a card said
// "scanning" until it was remounted, long after the rows had changed.
ok("a scan is watched to its end whoever started it",
   /load\(\)\.then\(d => \{ if \(d && d\.scanning\) watchScan\(\); \}\);/.test(cards)
   && /const watchScan = \(\) => \{/.test(cards));
// Folding is allowed to move the scan time; it is not allowed to lose it.
// v4.98 hid `.waa-quiet-scanned` on a phone to keep the summary to one line,
// and that span was the ONLY place `detected_at` was rendered on an empty
// board — so you could not tell a board scanned an hour ago from one last
// scanned on Tuesday. "Nothing found" and "nothing looked recently" are
// different claims, and every other board in this app dates its answer.
ok("an empty board still says WHEN it last looked",
   /className="waa-quiet-when"/.test(cards)
   && /\.waa-quiet-when \{/.test(css)
   && /<DataStatus kind="cached"[\s\S]{0,200}?scanned \{detected\}/.test(cards));
ok("and its controls, filters and history are inside that line, not dropped",
   /<div className="waa-quiet-body">\s*\n\s*\{controls\}/.test(cards)
   && /const controls = \(/.test(cards));

// ── 18. the jump control is reachable without scrolling ───────────────────
//
// It sat below the market band and the Highs & Lows card — about a screen and
// a half on a phone — so the control whose purpose is to save you scrolling
// was itself something you scrolled to find.
ok("section navigation is mounted before the mobile market band",
   app.indexOf('<SectionNav tab="trade"') > -1
   && app.indexOf('<SectionNav tab="trade"')
      < app.indexOf("{bandInWorkspace && activeTab === \"trade\" && ("));
ok("and it names itself on a phone, where it is the first thing you see",
   /className="secnav-lbl-sm"/.test(lib)
   && /\.secnav-lbl-sm \{ display: inline; \}/.test(css));

// ── 19. everything that overlays a phone stops at the frame ───────────────
//
// The tool picker already did. The settings drawer did not: `inset: 0` put it
// over the ten charts the frame exists to keep on screen.
for (const sel of [".tabsheet-overlay", ".mobile-overlay"]) {
  const block = css.match(new RegExp(`\\${sel} \\{[^}]*\\}`, "g")) || [];
  const bounded = block.some(b => /var\(--frame-top-h/.test(b)
                                  && /var\(--frame-bottom-h/.test(b));
  ok(`${sel} is bounded by the frame, not the window`, bounded);
}
ok("the drawer itself is bounded too, not just its backdrop",
   /\.sidebar \{[^}]*top: var\(--frame-top-h[^}]*bottom: var\(--frame-bottom-h/.test(css));
ok("the version is present on a phone, not only on a desktop",
   !/@media \(max-width: 900px\)[\s\S]{0,8000}?\.statusline \{ display: none; \}/.test(css)
   && /\.statusline \{\s*\n\s*display: flex;/.test(css));

// ── 20. the documented contract is the enforced one ───────────────────────
//
// UI_FRAME.md publishes the workspace floors as the frame's acceptance
// criteria; test_frame_render.py enforces them. Those are two copies of the
// same number, and they drifted the first time one of them moved: the
// assertion went to 44% while the document still said 47%, so a regression
// into that band would have passed CI while the document said it should fail.
// A review bot caught it. This makes the next drift fail instead.
const render = read("test_frame_render.py");
const doc = read("UI_FRAME.md");
const asserted = [...render.matchAll(
  /_check_workspace_share\((\d+),\s*(\d+),\s*([\d.]+)\)/g)]
  .map(m => ({ w: +m[1], pct: Math.round(parseFloat(m[3]) * 100) }));
const documented = [...doc.matchAll(/(\d+)% of a (\d+)×\d+/g)]
  .map(m => ({ w: +m[2], pct: +m[1] }));
ok("the workspace floors are actually asserted somewhere",
   asserted.length >= 2, String(asserted.length));
ok("and the document publishes one for each",
   documented.length >= asserted.length,
   `asserted ${asserted.length}, documented ${documented.length}`);
for (const a of asserted) {
  const d = documented.find(x => x.w === a.w);
  ok(`the ${a.w}px floor says the same thing in the test and the document`,
     !!d && d.pct === a.pct,
     d ? `test ${a.pct}%, doc ${d.pct}%` : `nothing documented for ${a.w}px`);
}

// ── 21. a phone on its side is still a phone ──────────────────────────────
//
// Every mobile rule in this stylesheet is written `max-width: 900px`. Rotate
// the phone and 440x956 becomes 956x440 — past that number — so the desktop
// sidebar came back and took a third of a 956-pixel-wide screen to show a
// logo and a green dot. Width alone cannot tell a phone on its side from a
// laptop; the HEIGHT can, and the branch has to be bounded above so a short
// window on a real monitor never takes it.
const landscape = css.match(
  /@media \(max-height: 560px\) and \(max-width: 1180px\) and \(min-width: 901px\) \{[\s\S]*?\n\}/);
ok("there is a phone-landscape branch, keyed on height and bounded on width",
   !!landscape);
if (landscape) {
  const b = landscape[0];
  ok("in it the frame is one column, so the tool gets the whole width",
     /\.frame-top, \.frame-body \{ grid-template-columns: minmax\(0, 1fr\); \}/.test(b));
  ok("the sidebar becomes an off-canvas drawer rather than disappearing",
     /\.sidebar \{[^}]*position: fixed[^}]*transform: translateX\(-/.test(b)
     && /\.sidebar\.nav-open \{ transform: translateX\(0\)/.test(b));
  ok("and the drawer is bounded by the frame here too",
     /\.sidebar \{[^}]*var\(--frame-top-h[\s\S]*?var\(--frame-bottom-h/.test(b));
}
// A drawer with no handle is a drawer you cannot open. The mobile header that
// carries the burger in portrait is not on screen at this width, so the app
// bar has to grow one — and only here, or the desktop gets a button that
// opens a sidebar already sitting beside it.
ok("the app bar carries a door to it, mounted in the markup",
   /className="ab-icon ab-menu"/.test(app));
ok("and that door is hidden everywhere the sidebar is already visible",
   /\.ab-menu \{ display: none; \}/.test(css)
   && !!landscape && /\.ab-menu \{ display: inline-flex/.test(landscape[0]));

// ── 21b. one definition of "phone", not two ───────────────────────────────
//
// The landscape branch above was written in CSS only. The components kept
// asking `useIsPhone()`, which is width-keyed, so at 956x440 the two halves
// disagreed: the stylesheet drawered the sidebar while SectionNav still drew
// the 904px chip strip, and the four high/low rails stayed mounted behind
// display:none with their replacement card never rendered — fetched, polling,
// unreachable, which is the defect the frame was built to end.
//
// There are two legitimate questions ("is it narrow" vs "is the frame in
// phone mode"), so the rule is not "one predicate" — it is that the second
// one EXISTS, is derived from the same two numbers as the CSS branch rather
// than being a third hard-coded copy, and is what decides mount points.
ok("the frame's phone predicate exists alongside the width one",
   /function useIsPhoneFrame\(\)/.test(lib)
   && /const PHONE_FRAME_Q = `\$\{PHONE_Q\}, \$\{SHORT_LANDSCAPE_Q\}`/.test(lib));
ok("and it is built from the same numbers the stylesheet branch uses",
   (() => {
     const m = lib.match(
       /const SHORT_LANDSCAPE_Q = "\(max-height: (\d+)px\) and \(max-width: (\d+)px\)"/);
     if (!m || !landscape) return false;
     return landscape[0].includes(`max-height: ${m[1]}px`)
       && landscape[0].includes(`max-width: ${m[2]}px`);
   })());
ok("mount points ask the frame, not the width",
   /const phoneFrame = useIsPhoneFrame\(\);/.test(app)
   && /\{!phoneFrame && \(\s*\n\s*<React\.Fragment>\s*\n\s*<ExtremeRail/.test(app)
   && /\(phoneFrame \? \(\s*\n[\s\S]{0,500}<details className="card phone-band">[\s\S]{0,600}<CardErrorBoundary label="Highs and lows">/.test(app));
// The weather pill is the deliberate exception, and it has to stay one: the
// mobile header is shown by a width-keyed rule, so in landscape it is off
// screen and the app bar is the only bar there is. Moving this to the frame's
// predicate would mount the pill inside a hidden header — which is exactly
// how the weather vanished in v4.93.
ok("controls inside the width-keyed mobile header still ask the width",
   /\{!isPhone && <WeatherBadge variant="bar" \/>\}/.test(app)
   && /\{isPhone && <WeatherBadge variant="bar" \/>\}/.test(app));

// ── 22. the phone's jump control is a picker ──────────────────────────────
//
// Moving it to the top fixed WHERE it was; it was still a horizontal strip of
// seventeen chips with their labels clipped, so a panel near the bottom of
// the page was a swipe hunt. The tool picker solved the same problem with a
// searchable sheet. Same shape, so one learned gesture works in both places.
ok("a phone gets a picker, not the chip strip",
   /className="secnav secnav-phone"/.test(lib)
   && /className="secnav-open"/.test(lib));
ok("the picker is searchable, and says so when nothing matches",
   /className="secnav-none"/.test(lib)
   && /setFind\(/.test(lib));
ok("it reuses the tool picker's sheet rather than inventing a second one",
   /className="tabsheet secnav-sheet"/.test(lib)
   && /className=\{`tabsheet-btn secnav-pick/.test(lib));
ok("and its rows wrap instead of clipping the labels that started this",
   /\.secnav-sheet \.secnav-pick \{[^}]*white-space: normal/.test(css));
// The desktop keeps the strip: there the labels fit, and the lit chip tells
// you where you are as the page scrolls under it.
ok("the desktop still gets the strip, with its where-am-I highlight",
   /const \[hereId, setHere\] = useState\(null\)/.test(lib));

// ── 23. the watchlist opens on its stocks ─────────────────────────────────
//
// Measured: the first stock card started 798px down a 412px workspace. The
// explanation, the market-flow summary and eight filters were two screens of
// preamble in front of the list the destination is named after. Nothing is
// removed — the rule is that on a phone the everyday controls stay out and
// the rest folds.
ok("the explanation folds on a phone",
   /<details className="panel-method wl-fold">/.test(cards)
   && /ab-status-slim/.test(cards));
ok("the market-flow summary becomes a one-line disclosure on a phone",
   /<details className="wl-market wl-market-fold"/.test(cards));
// The phone filter row has to hold its three everyday controls on ONE line,
// and the thing that decides that is the search box's flex BASIS, not its
// growth and not an auto margin. `flex: 1 1 auto` sizes it from its content
// when the lines are formed — ~224px on the live board — which filled the row
// with the search and the Filters button and pushed the "N shown" count onto a
// third line. Auto margins are zero during line breaking, so `margin-left:
// auto` could only right-align the count on the line it had already landed on.
//
// This is pinned as a rule rather than a measurement because the sandbox
// cannot reproduce it: its stub count reads "12 shown" and fits either way.
// Verified by injecting the rule into the live page against the real
// 1265-name board — 96px tall to 76px, all three controls on one line.
ok("the phone search box grows from a zero basis, so the row stays two lines",
   /\.ab-filters-phone \.ab-search \{ flex: 1 1 0; min-width: 0; \}/.test(css)
   && !/\.ab-filters-phone > \.muted \{[^}]*margin-left: auto/.test(css));
ok("the advanced filters fold, and the everyday ones do not",
   /ab-filters-lite/.test(cards)
   && /\.ab-filters-lite \.wl-adv \{ display: none; \}/.test(css)
   && (cards.match(/wl-adv/g) || []).length >= 5);
// Folding a filter hides the fact that it is NARROWING the list — you would
// see six stocks, not know why, and conclude the scanner is broken. The count
// on the toggle is the safety catch, so it has to be derived from the filters
// rather than being a static label.
ok("a folded filter that is still narrowing the list says so on the toggle",
   /const wlNarrowing = \(primeOnly \? 1 : 0\)/.test(cards)
   && /Filters\{wlNarrowing \? ` \(\$\{wlNarrowing\}\)` : ""\}/.test(cards));

// ── 24. an empty board tells the truth about the clock ────────────────────
//
// At 7:26 on a trading morning the app bar said "Pre-market" and the panel
// two inches below it said "The market is closed for the day". Both came from
// the same process. The fix is one function that names the phase, used by the
// wording AND by the panel's verdict, so the two cannot drift again.
const spike = read("spike_scan.py");
const spikeTab = read("tab-spike.jsx");
ok("the backend names the phase rather than only 'open or not'",
   /def market_phase\(/.test(spike)
   && /return "pre" if n < o else \("open" if n < c else "post"\)/.test(spike));
ok("and ships it in the payload the panel reads",
   /"phase": market_phase\(\),/.test(spike));
ok("before the bell the wording is pre-market, not closed-for-the-day",
   /Pre-market — the session has not opened yet/.test(spike));
ok("the panel's verdict follows the same phase, not a second opinion",
   /SK_PHASE_VERDICT\[data\.phase\]/.test(spikeTab)
   && /pre: "Pre-market"/.test(spikeTab));
ok("and an empty board's status is a disclosure, not a wall of paragraphs",
   /<details className="panel-method sl-status-fold">/.test(spikeTab)
   && /function skStatusFacts\(data\)/.test(spikeTab));

// ── 25. focus: the frame gives the tool the screen ────────────────────────
//
// Measured at 1900×1200: 449px of permanent frame before the workspace, 649px
// of window for the tool. Focus is an opt-in, remembered body class; the CSS
// folds the frame rather than removing anything, and the phone frame is
// untouched because every rule sits under the desktop breakpoint.
const shellSrc = read("app.jsx");
// v5.17: the remembered switch wins; with nothing remembered, a phone
// starts in focus and a desktop does not.
ok("focus is a body class with a remembered switch, and a phone default",
   /const saved = localStorage\.getItem\("jerry_focus_frame_v1"\);\s*\n\s*if \(saved === "1" \|\| saved === "0"\) return saved === "1";\s*\n\s*return !!\(window\.matchMedia && window\.matchMedia\(PHONE_Q\)\.matches\);/.test(shellSrc)
   && /classList\.toggle\("focus-frame", focusFrame\)/.test(shellSrc));
// Bare F only: Ctrl+F and Cmd+F are the browser's Find, and a shortcut that
// also flipped the layout would be a surprise on every search.
ok("F toggles it when you are not typing and not holding a modifier, and the sheet says so",
   /\(e\.key === "f" \|\| e\.key === "F"\) && !e\.metaKey && !e\.ctrlKey && !e\.altKey/.test(shellSrc)
   && /\["F", "Focus/.test(cards));
// The switch, the key and the CSS all read ONE breakpoint. From 901 to
// 1080px the app bar is on screen but the frame has its own shape; a button
// that changed its icon and nothing else would be a lie.
ok("the switch, the key and the rules share one breakpoint",
   /const FOCUS_FRAME_Q = "\(min-width: 1081px\)"/.test(lib)
   && /const focusWide = useMediaQuery\(FOCUS_FRAME_Q\)/.test(shellSrc)
   && /window\.matchMedia\(FOCUS_FRAME_Q\)\.matches/.test(shellSrc)
   && /@media \(min-width: 1081px\) \{\n  \/\*[^]*?\*\/\n  body\.focus-frame \.mko-grid/.test(css));
ok("there is a door in the app bar, only where the rules apply, and it says which way it is set",
   /\{focusWide && <button className=\{`ab-icon ab-focus\$\{focusFrame \? " on" : ""\}`\}/.test(shellSrc)
   && /aria-pressed=\{focusFrame \? "true" : "false"\}/.test(shellSrc));
// Ten across only where the band is wide enough: at 1081px it is ~705px, and
// ten columns would be 65px tiles with the price clipped. Narrower desktops
// keep five columns and get two compact rows — 82px, not 178.
ok("in focus the instruments are compact numbers: two short rows on a narrow desktop, one row on a wide one",
   /body\.focus-frame \.mko-grid \{ grid-template-columns: repeat\(5, minmax\(0, 1fr\)\)/.test(css)
   && /@media \(min-width: 1601px\) \{\n  body\.focus-frame \.mko-grid \{ grid-template-columns: repeat\(10, minmax\(0, 1fr\)\); \}/.test(css)
   && /body\.focus-frame \.mko-spark, body\.focus-frame \.mko-pts, body\.focus-frame \.mko-proxy \{ display: none; \}/.test(css));
ok("the posture card keeps its verdict and folds its lists",
   /body\.focus-frame \.pc-stats, body\.focus-frame \.pc-rot, body\.focus-frame \.pc-picks/.test(css)
   && !/body\.focus-frame \.pc-badge/.test(css));
// The empty daily rails: 380px of "no names yet" through the whole of
// pre-market. The component puts the fact on <body>; the CSS does the width
// arithmetic, next to the full-rail arithmetic it mirrors.
ok("an empty daily rail is a 30px label on its side, not a column of apology",
   /\.lrail\.lrail--empty \{ width: 30px; \}/.test(css)
   && /\.lrail--empty \.lrail-title \{[^}]*writing-mode: vertical-rl/.test(css));
// "No rows yet" before the source has answered is not "empty". Calling it
// empty would collapse both rails on every page load and snap the frame open
// again when the first answer came — a layout jump on every visit.
ok("the component reports emptiness on <body> only once its source has answered",
   /const railEmpty = !asPanel && !!cfg\.emptyNote && loaded && rows\.length === 0;/.test(cards)
   && /if \(d && Array\.isArray\(d\.rows\)\) setLoaded\(true\)/.test(cards)
   && /className=\{`\$\{cfg\.wrapCls\}\$\{railEmpty \? " lrail--empty" : ""\}`\}/.test(cards)
   && /classList\.toggle\(cls, railEmpty\)/.test(cards));
ok("and when both are empty the frame takes the width back",
   /body\.rail-empty-dailyHigh\.rail-empty-dailyLow \.frame-top,\n  body\.rail-empty-dailyHigh\.rail-empty-dailyLow \.frame-body \{\n    max-width: calc\(100vw - 2 \* \(var\(--rail-w\) \+ 44px\)\);/.test(css));

// ── 26. one row of navigation, and a floor under the type ─────────────────
//
// Four rows were 100px of the workspace column on every destination. One
// row: the four group names, then the OPEN group's tools; the open group
// follows the active tab and a dot marks home when you are looking at
// another group. Nothing is behind a menu: two clicks reach any tool.
ok("the navigation is one row: four group names, then the open group's tools",
   /className="tab-bar tab-bar-grouped tab-bar-one"/.test(cards)
   && /className=\{`tab-row tab-row-one tab-row-\$\{open\.id\}`\}/.test(cards)
   && /list\.filter\(t => open\.ids\.includes\(t\.id\)\)\.map\(renderBtn\)/.test(cards));
// …on every TAB change, not only when the group changes: with Scan open
// while on Trade, `]` to Ask AI stays in Workspace, and keying on the group
// left Scan's tools on the row over the tool you had just moved to.
ok("the open group follows the active tab on every tab change, and home is marked",
   /useEffect\(\(\) => \{ setOpenGroup\(activeGroup\); \}, \[active\]\);/.test(cards)
   && /className=\{`tab-grp\$\{isOpen \? " open" : ""\}\$\{isHere \? " here" : ""\}`\}/.test(cards)
   && /\.tab-grp\.here:not\(\.open\)::after \{\n  content: "●"/.test(css));
ok("the row never wraps: a wide group scrolls sideways",
   /\.tab-row-one \.tab-row-btns \{ flex-wrap: nowrap; overflow-x: auto;/.test(css));
// The type floor. Measured by a probe listing every visible text element
// under 10.5px band by band; these are the captions it found.
ok("the eight- and nine-pixel captions are ten or more",
   /\n\.pc-src, \.mko-proxy \{ font-size: 10px; \}/.test(css)
   && /\n\.pc-stats span, \.pc-picks-h, \.pc-rot-lbl, \.opp-title, \.opp-chip em, \.mctx-rlbl \{ font-size: 10px; \}/.test(css)
   && /\n\.secnav-lbl, \.secnav-lbl-sm, \.pcalc-label, \.pcalc-from-meta, \.pick-label \{ font-size: 10\.5px; \}/.test(css)
   // …and the three the live site showed that the stub could not: the
   // sidebar's sector tag and the earnings panel's labels.
   && /\n\.sb-symtag, \.emx-pill-dte \{ font-size: 10\.5px; \}/.test(css)
   && /\n\.em-stat-lbl \{ font-size: 10px; \}/.test(css));
ok("and the group names in the navigation are 11.5 — the four words read most often",
   /\.tab-grp \{\n  font-family: var\(--font-mono\); font-size: 11\.5px;/.test(css));
// v5.14: the divider between the group names and the open group's tools was
// one pixel of --line, the hairline that edges a card, ending 2px short of
// the row. Two pixels of --line-2, stretched, with 14px of air either side.
ok("the divider between the groups and the tools is two pixels, brighter, and full height",
   /\.tab-groups \{\n  display: flex; align-items: center; gap: 2px; align-self: stretch;\n  padding-right: 14px; border-right: 2px solid var\(--line-2\);/.test(css)
   && /\.tab-row-one \{ grid-template-columns: auto minmax\(0, 1fr\) auto; gap: 14px; \}/.test(css));
// v5.04: the floor is not gated to the desktop any more. The phone showed
// the same classes plus two of its own, and every phone floor was
// re-measured with these sizes before the gate came off.
ok("the floor applies on the phone too, with the two captions only a phone shows",
   !/@media \(min-width: 1081px\) \{\n  \.pc-src, \.mko-proxy/.test(css)
   && /\n\.mko-pts \{ font-size: 10px; \}/.test(css)
   && /\.secnav-lbl-sm, /.test(css));
// `.term` is a wrapper that inherits its size from its context — 17px
// strategy names, 14px subtitles. A size on the class itself is a size on
// all of them; the sidebar's term carries .sb-label, which is its floor.
ok("the floor never sizes the glossary-term wrapper itself",
   !/\n[^\n]*\.term\b[^\n]*\{ font-size: 10/.test(css.slice(css.indexOf("The type floor (v5.03 desktop"))));
ok("the floor is the last block in the stylesheet",
   /\.secnav-lbl, \.secnav-lbl-sm, \.pcalc-label, \.pcalc-from-meta, \.pick-label \{ font-size: 10\.5px; \}\n$/.test(css));


// ── 27. v5.10: the strike engine's panel, and the chain-order chart ───────
// The panel used to headline the worst weekly low and best weekly high of
// the lookback — the two most extreme things the stock ever did — and the
// side columns described whatever strike the 0.20-delta picker had landed
// on. Both are now the engine's answer for the expiry on screen.
ok("the panel reads the server's plan and refuses one built for another symbol",
   /function _wosPlan\(ticker\)/.test(cards)
   && /String\(L\.ticker \|\| ""\)\.toUpperCase\(\) !== String\(ticker\)\.toUpperCase\(\)/.test(cards));
ok("the sell zone replaces the all-time extremes as the headline",
   /SELL PUTS BELOW/.test(cards) && /SELL CALLS ABOVE/.test(cards)
   && /SELL ZONE · NEXT \{plan\.sessions\} SESSION/.test(cards));
ok("both assignment reads are printed, so a refusal is legible",
   /\{pct0\(p\.itm_pct\)\} hist · \{p\.p_otm == null \? "—" : pct0\(100 - p\.p_otm\)\} mkt/.test(cards));
ok("the recommendation is also said once in plain words",
   /className="wos-plain"/.test(cards) && /Sell the <b>/.test(cards));
// A cash-secured put's loss is cash out the door. A covered call's is
// upside given up on shares already owned. Same arithmetic, different
// sentence — and the panel must never print the put's sentence for a call.
ok("a call's expected value is never described as cash the way a put's is",
   /instead of simply holding the shares/.test(cards)
   && /lead === "call" \? " against just holding the shares" : " on the cash it ties up"/.test(cards)
   && /"ev_basis": "cash secured" if put else "vs holding the shares"/.test(read("weekly_sell.py")));
ok("the user's own strike is measured on the same numbers, not hidden",
   /className=\{`wos-mine/.test(cards) && /YOUR PICK/.test(cards));
ok("the panel still draws when the server sent no plan",
   /function LegacyRange\(\)/.test(cards) && /WEEK RANGE LOCATION/.test(cards));
ok("the server ships the plan with the symbol payload",
   /"sellPlan": sell_plan,/.test(read("options_dashboard.py")));
// The day-of-week line was nowrap above 1100px while living in one column
// of a three-column card, so its tail was cut off on a wide desktop.
ok("the day-of-week line wraps instead of running off the card",
   /\.wos-dayctx \{ white-space: normal; overflow-wrap: anywhere; \}/.test(css)
   && !/@media \(min-width: 1101px\) \{\n  \.wos-dayctx \{ white-space: nowrap; \}/.test(css));
// Calls left, puts right — the option-chain convention. The DOM order and
// the CSS have to agree, or the bars grow away from the strike axis.
ok("the chart draws calls on the left and puts on the right",
   /oi-bar-side call[\s\S]{0,320}oi-bar-strike[\s\S]{0,320}oi-bar-side put/.test(app));
ok("each side's bar grows out of the strike column, not into the margin",
   /\.oi-bar-side\.call \{ justify-content: flex-end; \}/.test(css)
   && /\.oi-bar-side\.put \{ justify-content: flex-start; \}/.test(css));
ok("the busiest strikes are named above the chart, not found by scrolling",
   /className="oi-hot"/.test(app)
   && /HEAVIEST OPEN INTEREST/.test(app) && /HEAVIEST VOLUME TODAY/.test(app));
// A centred nowrap label at the end of a scale hangs past its track and
// the card gains sideways scroll. The marker stays exact; the label's
// centre is clamped.
ok("the live-price label can never hang off the end of the scale",
   /const labelAt = v => `\$\{Math\.min\(80, Math\.max\(20, v\)\)\}%`;/.test(cards)
   && !/className="wos-now-label" style=\{\{ left: `\$\{/.test(cards));
ok("the heaviest open interest is placed relative to the chosen strike",
   /Open-interest wall/.test(cards)
   && /const past = side === "put" \? w\.strike <= p\.strike : w\.strike >= p\.strike;/.test(cards));
ok("nothing the engine's panel adds is under the type floor",
   /\.oi-hot-grp em \{[\s\S]{0,150}?font-size: 10px;/.test(css)
   && /\.wos-alts em \{[\s\S]{0,150}?font-size: 10px;/.test(css)
   && /\.wos-mine em \{[\s\S]{0,150}?font-size: 10px;/.test(css)
   && /\.wos-relaxed \{[\s\S]{0,120}?font-size: 10\.5px;/.test(css));


// ── 28. v5.11: one way into the shortcuts, and a clock that leads with
// whether you can trade ───────────────────────────────────────────────────
// The app bar's "?" and the status line's "Shortcuts" opened the same sheet.
// Two controls for one thing is one too many; the status line keeps it,
// where it already sits beside Search.
ok("the app bar no longer carries its own shortcuts button",
   !/<button className="ab-icon" onClick=\{\(\) => setHelpOpen\(true\)\}/.test(app));
ok("the status line is still the way in, and the key still works",
   /<button className="sl-link" onClick=\{\(\) => setPalOpen\(true\)\}/.test(app)
   && /className="sl-link" onClick=\{\(\) => setHelpOpen\(true\)\}/.test(app)
   && /e\.key === "\?"/.test(app));
// Whether the market is shut is the thing you glance up for, so it leads the
// clock and carries the colour. Grey read as "nothing to report".
ok("the market state comes before the date, not after it",
   /<span className=\{`ab-mkt ab-mkt-\$\{cls\}`\}>[\s\S]{0,220}?ab-clock-sep[\s\S]{0,120}?className="ab-when"/.test(app));
ok("a closed market is red, not grey",
   /\.ab-mkt-shut \.ab-mkt-dot \{ background: var\(--down\); \}/.test(css));
ok("open stays green and the sessions either side stay amber",
   /\.ab-mkt-open \.ab-mkt-dot \{ background: var\(--up\); \}/.test(css)
   && /\.ab-mkt-ext  \.ab-mkt-dot \{ background: var\(--warn\); \}/.test(css));
// "Sun, September 13, 2026" spent the bar's width on the least useful part.
ok("the date is short and the month is capitalised, with no year",
   /dpart\("month"\)\.toUpperCase\(\)/.test(app)
   && /weekday: "short", month: "short", day: "numeric",\n    \}\)\.formatToParts\(d\)/.test(app)
   && !/month: "long"[\s\S]{0,80}year: "numeric"/.test(app));
ok("the time keeps its seconds and says which zone it is",
   /second: "2-digit", hour12: true,/.test(app)
   && /\{dateFmt\}, \{timeFmt\} ET/.test(app));
ok("the state reads as a market, not a switch",
   /"Markets Open"[\s\S]{0,80}"Markets Closed"/.test(app));
// The dot and the separator sit on the text's centre line; a baseline row
// drops both of them below it.
ok("the clock's row is centred so the dot and the divider line up",
   /\.ab-clock \{\n  display: inline-flex; align-items: center;/.test(css));


// ── 29. v5.12: one signal in the clock, a night sky, and a week that is
// not an earnings week ────────────────────────────────────────────────────
// v5.11 coloured BOTH the dot and the label. Two ways of saying one thing;
// the label goes back to neutral and the dot grows a point, because it is
// now the whole signal.
ok("only the dot carries the market state, and it is a point larger",
   /\.ab-mkt \{[\s\S]{0,160}?color: var\(--fg-2\); \}/.test(css)
   && /\.ab-mkt-dot \{ width: 8px; height: 8px;/.test(css)
   && /\.ab-mkt-shut \.ab-mkt-dot \{ background: var\(--down\); \}/.test(css)
   && !/\.ab-mkt-shut \{ color:/.test(css));
ok("the open and extended sessions keep their own dot colours",
   /\.ab-mkt-open \.ab-mkt-dot \{ background: var\(--up\); \}/.test(css)
   && /\.ab-mkt-ext  \.ab-mkt-dot \{ background: var\(--warn\); \}/.test(css));
// A sun at 11:15 PM. The icon only ever read the weather code.
ok("the weather asks the API whether it is day or night",
   /&current=temperature_2m,weather_code,is_day/.test(read("weather.js")));
ok("and the pill passes that through to the glyph",
   /WeatherUtil\.wxFromCode\(wx\.code, wx\.isDay\)/.test(cards));
ok("a clear night is a moon, a clear day is a sun",
   /if \(c === 0\) return \{ icon: night \? "\u{1F319}" : "\u2600\ufe0f"/u.test(read("weather.js")));
// Every past earnings week is shaded amber; so was the week in progress.
ok("the week in progress has a colour of its own",
   /--now: oklch\(/.test(css)
   && /const nowC = colors\.now \|\| colors\.accent;/.test(read("charts.jsx")));
// Two ways a raw (un-hydrated) row breaks these charts: a string comparator
// returns NaN and the sort silently no-ops, and fmtDate calls
// toLocaleDateString, which a string does not have, so the render throws.
// Normalizing once at the boundary is what fixes both; a tolerant
// comparator alone fixed the silent half and left the loud one.
ok("both weekly charts normalize their rows before sorting or formatting",
   /function _weekRows\(rows\) \{/.test(read("charts.jsx"))
   && /r\.week_start instanceof Date/.test(read("charts.jsx"))
   && /\.sort\(\(a, b\) => a\.week_start - b\.week_start\);/.test(read("charts.jsx"))
   && (read("charts.jsx").match(/_weekRows\(rows\)/g) || []).length >= 3);
// new Date("2026-09-07") is midnight UTC, which is the day BEFORE anywhere
// west of Greenwich. Every Monday the server sent rendered as the Sunday
// before it in New York — on the chart axis, the recap strip and both
// tooltips. A calendar date with no time in it is a LOCAL calendar date.
ok("a bare calendar date is parsed as a local day, not as midnight UTC",
   /function parseDay\(v\) \{/.test(read("data.js"))
   && /new Date\(\+m\[1\], \+m\[2\] - 1, \+m\[3\]\)/.test(read("data.js"))
   && /week_start: parseDay\(r\.week_start\)/.test(read("data.js"))
   && /week_start: parseDay\(payload\.current\.week_start\)/.test(read("data.js")));
ok("and the charts' own fallback uses that same rule, not the constructor",
   /window\.MockData\.parseDay\(v\)/.test(read("charts.jsx"))
   && /new Date\(\+m\[1\], \+m\[2\] - 1, \+m\[3\]\)/.test(read("charts.jsx")));
// Two SVGs stacked in one card are read as one picture. The strip shipped
// with its own x geometry and its bars sat up to 72px off the chart's.
ok("the chart and the strip under it share one weekly grid",
   /const WEEK_VB_W = 720;/.test(read("charts.jsx"))
   && /function weekX\(i, n\) \{ return WEEK_PAD_L \+ weekSlot\(n\) \* \(i \+ 0\.5\); \}/.test(read("charts.jsx"))
   && /const xCenter = \(i\) => weekX\(i, data\.length\);/.test(read("charts.jsx"))
   && /const x = weekX\(i, n\) - barW \/ 2;/.test(read("charts.jsx")));
ok("a row with an unreadable date is dropped, not drawn at the epoch",
   /\.filter\(r => !Number\.isNaN\(\+r\.week_start\)\)/.test(read("charts.jsx")));
ok("and the chart and the legend both use it",
   /fill=\{nowC\} opacity="0\.10"/.test(read("charts.jsx"))
   && /style=\{\{background: chartColors\.now\}\}><\/span>This week/.test(app));
ok("the earnings shading is still the amber it was",
   /fill=\{earningsByWeek\[i\] \? colors\.warn : "transparent"\}/.test(read("charts.jsx")));
// The returns card drew its chart and stopped, ~200px short of its own
// bottom edge while the card beside it ran the full height.
// v5.15: five lines cross the returns chart. The dotted extremes were
// labelled on the axis from the first version; the three dashed medians —
// the typical week's high, low and close — were not, so the only place
// their values appeared was a card above that never mentions them.
ok("every dashed line on the returns chart carries its value in the gutter",
   /const medianLines = useMemo\(\(\) => \{/.test(read("charts.jsx"))
   && /\{medianLines\.map\(\(m, i\) => \(/.test(read("charts.jsx"))
   && /\{m\.v >= 0 \? "\+" : ""\}\{m\.v\.toFixed\(1\)\}%/.test(read("charts.jsx")));
// …and a label may not land on the label above it, so the generic ticks
// step aside for the medians the same way they do for the extremes —
// from where the label ENDED UP, not from the line it belongs to.
ok("the generic ticks make room for the median labels",
   /medianLines\.some\(m => Math\.abs\(yScale\(t\) - m\.y\) < 12\)\) \? null : \(/.test(read("charts.jsx")));
// A daily bar's date is a CALENDAR date — mock bars are built at local
// midnight, live ones hydrated from a bare YYYY-MM-DD. Projecting either into
// New York lands on the previous evening, so "does the series already end with
// today?" never matched and today's live bar was appended BESIDE today's real
// one. Two bars at one time is a series lightweight-charts cannot index.
ok("today's live bar replaces today's bar instead of doubling it",
   /const dateKey = \(d\) => \{/.test(read("app.jsx"))
   && /return `\$\{d\.getFullYear\(\)\}-\$\{pad2\(d\.getMonth\(\) \+ 1\)\}-\$\{pad2\(d\.getDate\(\)\)\}`;/.test(read("app.jsx"))
   && !/timeZone: "America\/New_York",\n *year: "numeric", month: "2-digit", day: "2-digit",\n *\}\)\.format\(d\)/.test(read("app.jsx")));
// …and the boundary that stops any such mistake from taking the page down,
// on every series that draws bars.
ok("a repeated or backwards bar time never reaches the chart library",
   /function ascendingByTime\(points\) \{/.test(read("charts.jsx"))
   && /if \(p\.time === prev\.time\) out\[out\.length - 1\] = p;/.test(read("charts.jsx"))
   && (read("charts.jsx").match(/ascendingByTime\(/g) || []).length >= 5
   && /ascendingByTime\(drawable\.map/.test(read("app-cards.jsx")));
// Codex, P2: dropping a colliding label leaves its dashed line drawn and
// unexplained, and makes the legend's promise false. Move it instead.
ok("a colliding gutter label is moved, never dropped",
   /function placeGutterLabels\(fixed, movers, minGap, lo, hi\) \{/.test(read("charts.jsx"))
   && /y = y >= hit \? hit \+ minGap : hit - minGap;/.test(read("charts.jsx"))
   && /return placeGutterLabels\(fixed, movers, 11, padT \+ 6, H - padB - 2\);/.test(read("charts.jsx"))
   && !/if \(m\.v == null \|\| !Number\.isFinite\(m\.v\) \|\| !clear\(m\.v\)\) continue;/.test(read("charts.jsx")));
ok("and the legend says what the dashed lines are",
   /<span className="swatch dashed" style=\{\{borderColor: "var\(--fg-3\)"\}\}><\/span>Typical week/.test(read("app.jsx")));
ok("the returns card carries a recap under its chart",
   /<WeeklyRecap rows=\{rows\} colors=\{chartColors\} \/>/.test(app)
   && /function WeeklyRecap\(\{ rows, colors \}\)/.test(read("charts.jsx")));
ok("the recap is four tiles and a range strip, all above the type floor",
   /\.wrc-tiles \{ display: grid; grid-template-columns: repeat\(4/.test(css)
   && /\.wrc-tile em \{[\s\S]{0,140}?font-size: 10px;/.test(css)
   && /\.wrc-strip-lbl \{[\s\S]{0,180}?font-size: 10px;/.test(css));
// open_return is 0 by construction in Monday-open mode, so an "average
// weekend gap" there would be a row of zeros dressed up as a finding.
ok("the weekend-gap tile stands down when the baseline cannot produce one",
   /const gapReal = gaps\.length >= 3 && gaps\.some\(v => Math\.abs\(v\) > 0\.01\);/
     .test(read("charts.jsx"))
   && /lbl="WIDEST WEEK"/.test(read("charts.jsx")));
ok("the range trend refuses to split a window too short to split",
   /if \(n >= 8\) \{/.test(read("charts.jsx"))
   && /needs 8 weeks, have \$\{n\}/.test(read("charts.jsx")));

console.log(`\n${passed}/${passed + failed} passed`
  + (failed ? ` — FAILED: ${fails.join(", ")}` : ""));
process.exit(failed ? 1 : 0);
