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
ok("the ten charts and the section bar are inside the top frame, not the workspace",
   app.indexOf('className="frame-top"') < app.indexOf("<MarketOverview")
   && app.indexOf("<MarketOverview") < app.indexOf('className="frame-body"'));
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
ok("the rails mount as fixed columns only on a desktop",
   /\{!isPhone && \(\s*\n\s*<React\.Fragment>\s*\n\s*<ExtremeRail kind="high52"/.test(app));
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
ok("the phone hides the desktop bar but keeps a searchable picker",
   /\.tab-bar, \.tab-bar-grouped \{ display: none !important; \}/.test(css)
   && /tabsheet-find/.test(app) && /toolFind/.test(app));
ok("the picker is grouped the same way, so nothing is hidden behind a menu",
   /TAB_GROUPS\.map\(g => \{/.test(app));

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
ok("and turns green during the regular session, like the other one",
   /className=\{`ab-time\$\{open \? " mkt-open" : ""\}`\}/.test(app)
   && /\.ab-time\.mkt-open\s*\{[^}]*var\(--up\)/.test(css)
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

console.log(`\n${passed}/${passed + failed} passed`
  + (failed ? ` — FAILED: ${fails.join(", ")}` : ""));
process.exit(failed ? 1 : 0);
