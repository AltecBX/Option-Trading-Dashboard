// test_strat_ui.js (v4.56) — source-level guards for the three candle-state
// tabs: Sectors, Market Context and Gamma Exposure.
//
// What a source-level guard can honestly enforce, and why each one is here:
//
//   1. Tooltip coverage. The house rule is a tooltip on everything, and the
//      things that most need one on these screens are the ones a reader
//      would otherwise have to guess at: what a "2U" is, what timeframe a
//      column means, and what gamma exposure's sign convention assumes.
//   2. Dates spell the month out. Never ISO on screen.
//   3. The state vocabulary is exactly four, in one fixed order, defined
//      once. Two orderings would make two stacked bars uncomparable.
//   4. Gamma exposure never presents itself as a measurement. Dealer
//      positioning is not published; the assumption must ride along with
//      the number, on screen, not only in a docstring.
//   5. A development fixture can never render as if it were live.
//   6. The chunk is registered everywhere it has to be — TABS, app.jsx,
//      the build list, the verify list — or a tab renders a dead panel.
//   7. Loading, error and empty states all exist for every panel.
//
// Run from the repo dir:  node test_strat_ui.js
const fs = require("fs");
const path = require("path");

let passed = 0, failed = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — got: " + extra : "")); }
}
const read = (f) => fs.readFileSync(path.join(__dirname, f), "utf8");

const src = read("tab-strat.jsx");
const appSrc = read("app.jsx");
const libSrc = read("app-lib.jsx");
const css = read("styles.css");
const build = read("build_frontend.js");
const verify = read("verify_frontend.js");

// ── 1. the state vocabulary ──────────────────────────────────────────────
ok("the four states are defined once, in one order",
   /const ST_ORDER = \["1", "2U", "2D", "3"\];/.test(src));
ok("there is no second ordering anywhere in the file",
   (src.match(/\["1",\s*"2U",\s*"2D",\s*"3"\]/g) || []).length === 1);
const metaBlock = src.slice(src.indexOf("const ST_META = {"),
                            src.indexOf("};", src.indexOf("const ST_META = {")));
["1", "2U", "2D", "3"].forEach(k => {
  ok(`state ${k} has a label`, new RegExp(`"${k}":\\s*\\{[^}]*label:`).test(metaBlock));
  ok(`state ${k} has a tooltip`, new RegExp(`"${k}":\\s*\\{[^}]*tip:`).test(metaBlock));
});
ok("the tooltips explain what was taken out, not what it predicts",
   /prior high was exceeded while the prior low held/.test(metaBlock)
   && /prior low was broken while the prior high held/.test(metaBlock));
ok("no state is described as bullish or bearish",
   !/bullish|bearish/i.test(metaBlock));

// ── 2. timeframe tooltips ────────────────────────────────────────────────
const tfBlock = src.slice(src.indexOf("const ST_TF_TIP = {"),
                          src.indexOf("};", src.indexOf("const ST_TF_TIP = {")));
["D", "W", "M", "Q", "Y", "60m", "4H"].forEach(k => {
  ok(`timeframe ${k} has a tooltip`,
     new RegExp(`(^|\\s)"?${k}"?:`, "m").test(tfBlock), tfBlock.slice(0, 60));
});
ok("the weekly tooltip says which week convention is used",
   /ISO weeks/.test(tfBlock));
ok("the four-hour tooltip names the actual session boundaries",
   /9:30[^"]*1:30/.test(tfBlock));

// ── 3. tooltip coverage across the rendered surface ─────────────────────
const titleCount = (src.match(/title=/g) || []).length;
ok("the surface carries a substantial tooltip vocabulary (60+)",
   titleCount >= 60, String(titleCount));
// Every interactive control the three tabs expose must be explained.
[["sort control", /Sort sectors/], ["constituent filter", /Filter constituents/],
 ["timeframe filter", /Filter timeframe/], ["state filter", /Filter state/],
 ["underlying input", /Underlying symbol/], ["expiration select", /aria-label="Expiration"/],
 ["exposure toggle", /What the bars show/]].forEach(([what, re]) => {
  ok(`the ${what} is labelled for assistive technology`, re.test(src));
});
ok("the exposure/open-interest toggle explains both sides",
   /Draw net gamma exposure per strike/.test(src)
   && /Draw raw open interest per strike/.test(src));

// ── 4. dates spell the month out ────────────────────────────────────────
const dateFn = src.slice(src.indexOf("const stDate ="), src.indexOf("const stTime ="));
ok("stDate uses a long month name", /month: "long"/.test(dateFn));
ok("stDate does not render ISO", !/toISOString/.test(dateFn));
ok("no ISO date template survives in the rendered surface",
   !/\$\{[^}]*\}-\$\{[^}]*\}-\$\{/.test(src));

// ── 5. gamma exposure states its assumptions on screen ──────────────────
ok("the net-exposure tooltip says positioning is not published",
   /not published/.test(src));
ok("the net-exposure tooltip calls it an assumption, not a measurement",
   /modelling assumption, not a measurement|assumption, not a measurement/.test(src));
ok("the convention travels with the payload and is rendered",
   /gx-convention/.test(src) && /data\.convention/.test(src));
ok("the flip tooltip explains it is not the cumulative-sum shortcut",
   /not by cumulatively summing/.test(src));
ok("the flip reports how much open interest it covered",
   /covered_oi_pct/.test(src));
ok("open interest is described as settled, therefore a day behind",
   /settled overnight/.test(src));

// ── 6. a fixture can never look live ────────────────────────────────────
ok("the fixture source is detected", /data\.source === "fixture"/.test(src));
ok("a fixture renders a banner, not a badge alone", /gx-fixture-banner/.test(src));
ok("the banner says plainly the numbers are not real",
   /These are not real quotes/.test(src));
ok("the fixture badge is visually distinct in CSS",
   /\.st-source-fixture/.test(css));
// And the server refuses to serve one unless explicitly asked.
const dash = read("options_dashboard.py");
ok("the server gates fixtures behind an environment flag",
   /GEX_DEV_FIXTURES/.test(dash));
ok("fixtures are off by default", /_gex_dev_fixtures_enabled/.test(dash)
   && /os\.environ\.get\("GEX_DEV_FIXTURES", ""\)/.test(dash));

// ── 7. live versus settled is always stated ─────────────────────────────
ok("the status line distinguishes live from last close",
   /live_ok/.test(src) && /last close/.test(src));
ok("a live chip is visually distinguishable", /st-live/.test(src) && /\.st-chip\.st-live/.test(css));
ok("the pre-market exclusion is explained to the reader",
   /pre-market prints|Pre-market prints/i.test(src));
ok("a stale candle names its own date rather than implying today's",
   /scan is behind/.test(src) && /candle_date/.test(src));

// ── 8. every panel has loading, error and empty states ──────────────────
ok("a loading skeleton exists", /function StLoading/.test(src));
ok("an error state with a retry exists", /function StError/.test(src) && /onRetry/.test(src));
ok("an empty state exists", /function StEmpty/.test(src));
[["SectorsTab", /No sector has any classified names yet/],
 ["the sector modal", /No name in this sector matches that filter/],
 ["the intraday line", /No samples yet today/],
 ["the indices matrix", /No index data came back/],
 ["the market map", /Nothing to map yet/]].forEach(([what, re]) => {
  ok(`${what} has its own empty message`, re.test(src));
});
ok("each tab renders the loading skeleton before data arrives",
   (src.match(/<StLoading/g) || []).length >= 4,
   String((src.match(/<StLoading/g) || []).length));
ok("each tab renders the error state",
   (src.match(/<StError/g) || []).length >= 4,
   String((src.match(/<StError/g) || []).length));

// ── 9. the four Market Context panels all exist ─────────────────────────
[["Market breadth", /kicker">Market breadth/], ["Daily breadth", /kicker">Daily breadth/],
 ["Current candle", /kicker">Current candle/], ["Indices", /kicker">Indices/]]
  .forEach(([what, re]) => ok(`Market Context has the ${what} panel`, re.test(src)));
ok("the indices matrix covers all four index funds",
   /SPY/.test(read("market_state.py")) && /QQQ/.test(read("market_state.py"))
   && /IWM/.test(read("market_state.py")) && /DIA/.test(read("market_state.py")));
ok("the intraday rungs are 60 minutes and 4 hours",
   /"60m"/.test(read("market_state.py")) && /"4H"/.test(read("market_state.py")));

// ── 10. the sector dashboard's own requirements ─────────────────────────
ok("a sector card shows its constituent count",
   /constituents\}/.test(src) && /name" : "names"/.test(src));
ok("a sector card draws a bar per timeframe",
   /timeframes\.map\(tf =>/.test(src) && /<StBar/.test(src));
ok("a sector card is clickable and opens the detail view",
   /onOpen\(card\.etf\)/.test(src) && /<SectorModal/.test(src));
ok("the detail view is a real dialog", /role="dialog"/.test(src) && /aria-modal="true"/.test(src));
ok("the dialog closes on Escape", /e\.key === "Escape"/.test(src));
ok("the dialog goes full-screen on a phone",
   /@media \(max-width: 900px\)[\s\S]{0,900}\.st-modal \{[^}]*height: 100%/.test(css));
ok("the detail view lists a state per timeframe per constituent",
   /tfs\.map\(t => \{[\s\S]{0,700}<StChip state=\{\(r\.states \|\| \{\}\)\[t\.key\]\}/.test(src));
ok("leader and laggard sorting is offered",
   /Leaders first/.test(src) && /Laggards first/.test(src));
ok("membership is described as classification, not ETF holdings",
   /membership_note/.test(src));

// ── 11. the market map ──────────────────────────────────────────────────
ok("the treemap is squarified, not naive", /function squarify/.test(src));
ok("rectangles are sized by market value", /value: s\.market_cap/.test(src));
ok("rectangles are coloured by percentage change", /function mapColour/.test(src));
ok("every rectangle carries a tooltip", /<title>\{`\$\{c\.child\.symbol\}/.test(src));
ok("a rectangle is clickable through to the symbol",
   /onOpenTicker\(c\.child\.symbol\)/.test(src));
ok("a rectangle is reachable by keyboard",
   /onKeyDown=\{e => \{ if \(\(e\.key === "Enter"/.test(src));
ok("what the map leaves out is disclosed", /smaller names are not/.test(src));

// ── 12. registration ────────────────────────────────────────────────────
["sectors", "context", "gex"].forEach(id => {
  ok(`TABS registers the ${id} tab`, new RegExp(`id: "${id}"`).test(libSrc));
  ok(`app.jsx renders a panel for ${id}`, new RegExp(`<TabPanel tab="${id}"`).test(appSrc));
});
["SectorsTab", "MarketContextTab", "GexTab"].forEach(c => {
  ok(`${c} is exported to window`, new RegExp(`${c}: React\\.memo`).test(src));
  ok(`app.jsx lazy-loads ${c}`, new RegExp(`component="${c}"`).test(appSrc));
  ok(`verify_frontend expects ${c}`, verify.indexOf(`"${c}"`) >= 0);
});
ok("all three share one chunk", (appSrc.match(/chunk="tab-strat"/g) || []).length === 3);
ok("the build compiles the chunk", /tab-strat\.jsx/.test(build));
ok("the build minifies the chunk", /tab-strat\.js"/.test(build));
ok("verify_frontend lints the chunk", /tab-strat\.js/.test(verify));
ok("each panel is wrapped in an error boundary",
   (appSrc.match(/CardErrorBoundary label="(Sectors|Market Context|Gamma Exposure)"/g) || []).length === 3);

// ── 13. styling and responsiveness ──────────────────────────────────────
ok("state colours come from the theme tokens, not hard-coded hex",
   /\.st-seg\.st-up \{ background: var\(--up\)/.test(css)
   && /\.st-seg\.st-down \{ background: var\(--down\)/.test(css));
const stratCss = css.slice(css.indexOf("CANDLE STATES — Sectors"));
// A literal belongs in a token DEFINITION and nowhere else — that is what
// a token is for. Rules must reference var(), so the check strips the
// --map-flat definitions (asserted separately to be equal-channel greys)
// and the one contrast colour on the fixture badge, then bans the rest.
const ruleCss = stratCss
  .replace(/--map-flat: #[0-9a-fA-F]{6};/g, "")
  .replace(/#1a1300/g, "");
ok("the new style RULES use tokens, never literal colours",
   !/#[0-9a-fA-F]{6}\b/.test(ruleCss),
   (ruleCss.match(/#[0-9a-fA-F]{6}\b/g) || []).join(","));
ok("the sector grid collapses on a phone",
   /\.st-secgrid \{ grid-template-columns: 1fr; \}/.test(css));
ok("wide tables scroll inside their own container",
   /\.st-scroll \{ overflow-x: auto/.test(css));
ok("motion is reduced when the reader asks for it",
   /@media \(prefers-reduced-motion: reduce\)[\s\S]{0,200}\.st-seccard/.test(css));
ok("the treemap and bars announce themselves to a screen reader",
   /role="img"/.test(src) && /aria-label="Treemap/.test(src));

// ── 14. request hygiene ─────────────────────────────────────────────────
ok("a shared feed hook exists so all three tabs poll identically",
   /function useStFeed/.test(src));
ok("a slow old response cannot overwrite a newer one",
   /mine !== seq\.current/.test(src));
ok("polling pauses while the tab is hidden", /document\.hidden/.test(src));
// useBoundedList returns a PAIR. Destructuring it as an object yields two
// undefineds and the first .map() call throws at render time — which no
// static lint catches, because both names resolve.
ok("useBoundedList is destructured as a pair, not an object",
   !/\{\s*shown\s*,\s*controls\s*\}\s*=\s*useBoundedList/.test(src)
   && /\[shown, controls\] = useBoundedList/.test(src));
ok("changing the underlying resets the chosen expiry",
   /useEffect\(\(\) => \{ setExp\(""\); \}, \[symbol\]\);/.test(src));

// ── 15. the three v4.56a fixes ──────────────────────────────────────────
// The map mixes toward an ACHROMATIC neutral. Mixing toward --bg-3 (a navy
// with real chroma) rotated every hue in oklch: green came out teal, red
// came out purple, and the map read as a chart of something else.
ok("the map mixes toward a neutral, not the panel background",
   /var\(--map-flat\)/.test(src) && !/mapColour[\s\S]{0,400}--bg-3/.test(src));
// The neutral must be an EQUAL-CHANNEL grey and the mix must happen in
// sRGB. Both halves were got wrong before being got right: mixing toward
// --bg-3 (a navy) turned green to teal, and mixing toward oklch(L 0 0) was
// no better because a hue of 0 is the red direction, not a powerless hue —
// a +0.3% gainer came out orange. sRGB toward (k,k,k) scales every channel
// difference equally, and hue depends only on those differences.
const flatLight = /:root \{ --map-flat: (#[0-9a-f]{6}); \}/i.exec(css);
const flatDark = /\[data-theme="dark"\] \{ --map-flat: (#[0-9a-f]{6}); \}/i.exec(css);
const equalChannel = (hex) => hex && hex[1] === hex[3] && hex[3] === hex[5]
  && hex[2] === hex[4] && hex[4] === hex[6];
ok("the map neutral is an equal-channel grey in light mode",
   !!flatLight && equalChannel(flatLight[1]), flatLight && flatLight[1]);
ok("the map neutral is an equal-channel grey in dark mode",
   !!flatDark && equalChannel(flatDark[1]), flatDark && flatDark[1]);
ok("the mix happens in sRGB, which cannot rotate a hue",
   /color-mix\(in srgb, var\(--up\)/.test(src)
   && /color-mix\(in srgb, var\(--down\)/.test(src));
ok("the map never mixes a colour in oklch",
   !/mapColour[\s\S]{0,600}color-mix\(in oklch/.test(src));
ok("the up and down hues still come from the theme tokens",
   /var\(--up\) \$\{/.test(src) && /var\(--down\) \$\{/.test(src));

// Labels are sized to the rectangle. A fixed 11px symbol needs ~42px of
// width, so every smaller rectangle rendered blank and read as missing data.
ok("map labels scale with the rectangle rather than a hard cutoff",
   !/c\.w > 42 && c\.h > 26/.test(src) && /fontSize: `\$\{fs\}px`/.test(src));
ok("the map height is derived from how many rectangles must fit",
   /perSector \* sectorCount/.test(src));
ok("how many names per sector is the reader's choice",
   /MAP_SIZES/.test(src) && /aria-label="How many names per sector"/.test(src));
ok("the dropped-names note quotes the live limit, not a hard-coded forty",
   /\{data\.limit_per_sector\} largest names/.test(src));

// Gamma Exposure follows the app's ticker.
ok("the gamma tab follows the global ticker",
   /useEffect\(\(\) => \{[\s\S]{0,260}setSymbol\(t\);[\s\S]{0,80}\}, \[ticker\]\)/.test(src));

// The chain is fetched in two bounded steps, never one unbounded call.
ok("the server enumerates expirations before fetching a chain",
   /_gex_expirations/.test(dash));
ok("the wide fetch is bounded by a date range",
   /expiration=selected\[0\], to_date=selected\[-1\]/.test(dash));
ok("no code path asks for a chain with no date filter at a wide strike count",
   !/get_option_chain\(symbol, strike_count=200\)/.test(dash));
ok("the SPY header gamma read is bounded the same way",
   /_gex_expirations\(sc, "SPY"\)/.test(dash));
ok("the multi-expiration option is capped and the cap is a constant",
   /_GEX_MAX_EXPIRATIONS/.test(dash));
ok("the UI names the real cap instead of promising every expiration",
   /Nearest \{expCap\} expirations/.test(src) && !/>All expirations</.test(src));

console.log(`\n${passed}/${passed + failed} passed`
            + (failed ? ` — FAILED: ${fails.join(", ")}` : ""));
process.exit(failed ? 1 : 0);
