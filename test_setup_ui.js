// test_setup_ui.js (v4.58) — source-level guards for the Best Setup card.
//
// This card recommends a real trade with real money, and the one thing that
// would make it dangerous is a recommendation to sell closer to the money
// that reads exactly like a well-supported one. So the guards are weighted
// toward disclosure rather than appearance:
//
//   1. The card must always say whether the strike is the conservative
//      default or a measured widening — that distinction is the feature.
//   2. The evidence must be reachable, including the sample size and the
//      conservative lower bound the strike was actually chosen on.
//   3. The risks must render with the same weight as the reasons.
//   4. Credit must be described as the bid, never the mid.
//   5. Gamma exposure must never be presented as a reason to take a trade.
//
// Run from the repo dir:  node test_setup_ui.js
const fs = require("fs");
const path = require("path");

let passed = 0, failed = 0; const fails = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name + (extra ? " — got: " + extra : "")); }
}
const read = (f) => fs.readFileSync(path.join(__dirname, f), "utf8");

const src = read("tab-setup.jsx");
const appSrc = read("app.jsx");
const css = read("styles.css");
const build = read("build_frontend.js");
const verify = read("verify_frontend.js");
const engine = read("setup_engine.py");
const scan = read("setup_scan.py");

// ── 1. the default-versus-widened distinction is always visible ─────────
ok("the card renders a band notice either way", /su-band/.test(src));
ok("a widened band is visually distinct", /su-band-raised/.test(src)
   && /\.su-band-raised/.test(css));
ok("a default strike says it is the conservative one, not an optimised one",
   /Conservative default strike/.test(src));
ok("a widened strike states the distance floor it rests on",
   /Any strike at least <b>\{suPct\(ceil\.min_distance_pct\)\}<\/b> out qualifies/.test(src));
ok("a widened strike states the sample it rests on",
   /measured over \{ceil\.n\} windows/.test(src));
ok("a widened strike quotes the conservative bound, not the point estimate",
   /suPct\(ceil\.keep_pct_low/.test(src));

// ── 2. the evidence is reachable and complete ───────────────────────────
ok("the evidence can be opened", /Show the evidence/.test(src));
ok("the conditioning rule is shown with the answer", /SU_TIP\.conditioning/.test(src)
   && /cond\.note/.test(src));
ok("how many bars matched the state is shown", /conditioned_bars/.test(src));
ok("the measured table renders every distance", /function SuMeasured/.test(src));
[["the measured rate", /Reached it/], ["the baseline", /From any bar/],
 ["the difference", /Difference/], ["the keep rate", />Kept</],
 ["the lower bound", /Kept, low bound/], ["the sample size", /Windows/]]
  .forEach(([what, re]) => ok(`the measured table shows ${what}`, re.test(src)));
ok("the rows that clear the distance floor are marked",
   /su-row-meets/.test(src) && /\.su-row-meets/.test(css));
ok("what each layer voted is shown", /What each layer said/.test(src));
ok("the volatility context is shown", /Expected realized/.test(src)
   && /Premium over realized/.test(src));
ok("the other side is shown rather than silently discarded",
   /The other side/.test(src) && /data\.alternative/.test(src));

// ── 3. risks carry the same weight as reasons ───────────────────────────
ok("reasons and risks are rendered side by side",
   /Why this trade/.test(src) && /What could go wrong/.test(src));
ok("risks are visually distinct", /su-list-risk/.test(src) && /\.su-list-risk/.test(css));
ok("a refusal is rendered plainly, not hidden", /su-refused/.test(src)
   && /Refused on market structure/.test(src));
ok("a losing trade is refused by name, not merely badged low-confidence",
   /No premium sale worth making here/.test(src) && /rec\.negative_ev/.test(src));
ok("the engine refuses a negative expected value rather than ranking it",
   /"negative_ev": True/.test(engine) && /loses money over repetition/.test(engine));
ok("a refused other side is still shown, not silently dropped",
   /refused\s+outright, not merely outscored/.test(src));
ok("a no-trade day explains itself in the sides' own words",
   /def _no_trade_reason/.test(scan));

// ── 4. every number says what it is ─────────────────────────────────────
const tipBlock = src.slice(src.indexOf("const SU_TIP = {"),
                           src.indexOf("};", src.indexOf("const SU_TIP = {")));
const defined = new Set();
tipBlock.replace(/^\s{2}(\w+):/gm, (m, k) => { defined.add(k); return m; });
const used = new Set();
src.replace(/SU_TIP\.(\w+)/g, (m, k) => { used.add(k); return m; });
const missing = [...used].filter(k => !defined.has(k));
ok("every referenced tooltip key is defined", missing.length === 0, missing.join(","));
ok("a real tooltip vocabulary exists (18+)", defined.size >= 18, String(defined.size));
const titleCount = (src.match(/title=/g) || []).length;
ok("the surface carries tooltips throughout (25+)", titleCount >= 25, String(titleCount));
ok("credit is described as the bid, never the mid",
   /credit at the BID/i.test(tipBlock) && /resting sell order/i.test(tipBlock));
ok("the keep rate names the volatility it is measured at",
   /volatility this stock actually realizes/.test(tipBlock));
ok("the tail loss is described as the average of the bad cases",
   /average of the bad cases/.test(tipBlock));
ok("annualized is labelled a comparison, not a forecast",
   /not a forecast/.test(tipBlock));

// ── 5. gamma never reads as a reason to trade ───────────────────────────
ok("the card says gamma is never a reason to take the trade",
   /never a reason to take this trade/.test(tipBlock));
ok("the engine can only shrink or stop a trade on gamma",
   /GEX_DELTA_ADJUST/.test(engine)
   && /"supports": 1\.0/.test(engine) && /"veto": 0\.0/.test(engine));

// ── 6. the engine's own guarantees, asserted from the UI's side ─────────
ok("the default band is the rule that already works",
   /DEFAULT_DELTA_BAND = \(0\.15, 0\.22\)/.test(engine));
ok("there is a hard cap on how close it may ever go",
   /MAX_DELTA_CEILING = 0\.45/.test(engine));
ok("the target win rate is held constant while the strike moves",
   /TARGET_WIN_PCT/.test(engine));
ok("the engine sizes on the Wilson lower bound",
   /def wilson_low/.test(engine) && /keep_pct_low/.test(engine));
ok("the circularity is gone: a distance is solved for, not a delta",
   /def required_distance/.test(engine)
   && /can only ever\s+return \(1 [-−] target\)/.test(engine));
ok("the conditioning rule is fixed in advance, not chosen after the fact",
   /fixed in advance/i.test(scan) && /picking the winner and calling it evidence/.test(scan));
ok("an incomplete forward window is never counted as a miss",
   /an incomplete window is not a miss/.test(scan));

// ── 7. request hygiene and registration ─────────────────────────────────
ok("a slow response for an older ticker cannot paint over a newer one",
   /mine !== seq\.current/.test(src) && /d\.symbol !== sym/.test(src));
ok("the card follows the app's ticker", /\}, \[load\]\);/.test(src)
   && /\[apiFetch, ticker\]/.test(src));
ok("BestSetupCard is exported to window", /BestSetupCard: React\.memo/.test(src));
ok("app.jsx lazy-loads it on the Trade tab",
   /component="BestSetupCard"/.test(appSrc) && /chunk="tab-setup"/.test(appSrc));
ok("it is wrapped in an error boundary",
   /CardErrorBoundary label="Best setup"/.test(appSrc));
ok("the build compiles and minifies the chunk",
   /tab-setup\.jsx/.test(build) && /tab-setup\.js"/.test(build));
ok("verify_frontend lints the chunk and expects the export",
   /tab-setup\.js/.test(verify) && /"BestSetupCard"/.test(verify));

// ── 8. presentation rules ───────────────────────────────────────────────
ok("dates spell the month out, never ISO",
   /month: "long"/.test(src) && !/toISOString/.test(src));
ok("loading, error and empty states all exist",
   /skel skel-line/.test(src) && /research-error/.test(src) && /research-empty/.test(src));
const suCss = css.slice(css.indexOf("Best Setup card (v4.58)"));
ok("the new style rules use tokens, never literal colours",
   !/#[0-9a-fA-F]{6}\b/.test(suCss),
   (suCss.match(/#[0-9a-fA-F]{6}\b/g) || []).join(","));
ok("the two-column reasoning collapses on a phone",
   /\.su-cols \{ grid-template-columns: 1fr; \}/.test(css));

// ── 9. the sell board ───────────────────────────────────────────────────
const board = read("setup_board.py");
ok("the board is registered as its own export",
   /SellBoardCard: React\.memo/.test(src) && /"SellBoardCard"/.test(verify));
ok("app.jsx mounts it on the Trade tab",
   /component="SellBoardCard"/.test(appSrc));
ok("clicking a row loads that symbol in the card above",
   /onPickTicker=\{\(t\) => \{ setTicker\(t\); setTickerInput\(t\); \}\}/.test(appSrc)
   && /onPick && onPick\(r\.symbol\)/.test(src));
ok("earnings inside the option's life EXCLUDES rather than bonuses",
   /earnings inside the option's life/.test(board)
   && /EXCLUSION here,\s*\n?\s*not a bonus/.test(board));
ok("the board does not claim to pick a better strike",
   /does NOT claim to find a better strike/.test(board));
ok("the ranking says what it is ranked on",
   /Ranked by how rich/.test(board) && /SU_TIP\.board/.test(src));
ok("the richness figure discloses which basis it used",
   /richness_basis/.test(src) && /rich_basis/.test(src)
   && /percentile.*ratio|ratio.*percentile/s.test(src));
ok("a thin history never quotes a percentile",
   /too few for a/.test(board) && /basis": "ratio"/.test(board)
   && /MIN_HIST_N/.test(board));
ok("how many were ranked versus measured is shown",
   /names ranked/.test(src) && /had their option/.test(src));
ok("an empty board is stated plainly, not left blank",
   /Nothing qualifies today/.test(src));
ok("refused names are reachable with their reasons",
   /it refused/.test(src) && /su-bskip/.test(src) && /\.su-bskip/.test(css));
ok("the board table scrolls rather than bursting the page",
   /\.su-btable-wrap \{ overflow-x: auto; \}/.test(css));
ok("the board's new styles use tokens, never literal colours",
   !/#[0-9a-fA-F]{6}\b/.test(css.slice(css.indexOf("Worth selling today board"))),
   (css.slice(css.indexOf("Worth selling today board")).match(/#[0-9a-fA-F]{6}\b/g) || []).join(","));

// ── 10. a broker problem must not read as a symbol problem ─────────────
const scan2 = read("setup_scan.py");
ok("the fallback badge says WHY, not just that it fell back",
   /re-authorize under Manage/i.test(appSrc)
   && /sign-in has expired/i.test(appSrc));
ok("an expired sign-in is called out rather than shown as a quiet fallback",
   /Schwab sign-in expired/.test(appSrc) && /urgent/.test(appSrc));
ok("the badge warns before the sign-in expires, not only after",
   /expires in about/.test(appSrc));
ok("the engine reports the broker reason instead of blaming the symbol",
   /def _broker_note/.test(scan2) && /broker_note/.test(scan2));
ok("every chain fetch carries both dates",
   !/get_option_chain\(\s*\n?\s*symbol,\s*to_date=/.test(scan2),
   "an unbounded fetch (to_date without expiration) is back");

ok("a stale scan is called out, not rendered as today's list",
   /This scan is \{staleWord\}, not today/.test(src)
   && /const stale = /.test(src));
ok("the staleness threshold is a real age, not a truthiness check",
   /ageMs > 20 \* 3600 \* 1000/.test(src));
ok("the stale notice says when the scan actually last completed",
   /It last completed \{suDate\(data\.as_of\)\}/.test(src));
ok("it points at the broker, which is the usual cause",
   /re-authorize under Manage/.test(src));

// ── 11. the earnings calendar is keyless now ───────────────────────────
const earn = read("tab-earnops.jsx");
const ewp = read("ewhispers.py");
ok("the calendar banner no longer demands an X API key",
   !/Automatic detection needs an X API key/.test(earn));
ok("it says the lookup needs no key",
   /no API key needed/.test(earn) && /no API key needed/.test(ewp));
ok("the pinned post is found without credentials",
   /def _keyless_weekly_candidate/.test(ewp) && /def _timeline_post_ids/.test(ewp));
ok("discovery is shape-blind, not tied to one JSON layout",
   /shape-blind/.test(ewp) && /_ID_RE/.test(ewp));
ok("a daily post cannot become the weekly card",
   /score_post\(/.test(ewp) && /MIN_CONFIDENCE/.test(ewp));

ok("the board says WHY nothing qualified, counted by reason",
   /refused_by/.test(src) && /su-tally/.test(src) && /refused_by/.test(board));
ok("a systematic failure is distinguishable from a quiet market",
   /SU_TIP\.tally/.test(src) && /points at the data upstream/.test(src));
ok("each refusal carries a machine-readable code",
   /"codes": codes/.test(board));

// ── 12. a broker throttle is temporary, and says so ────────────────────
// Switching symbols quickly makes Schwab answer "too many requests". That
// is a "not now", not a fact about the symbol, and it clears by itself.
ok("the retry button actually retries the request that failed",
   /className="error-retry"[\s\S]{0,200}refreshData\(\)/.test(appSrc),
   "bumping dataVersion does not re-run the /api/ticker effect");
ok("the error banner does not keep the old dataVersion no-op",
   !/className="error-retry" onClick=\{\(\) => setDataVersion/.test(appSrc));
ok("a throttle is recognised by HTTP status, not only by wording",
   /status === 429/.test(appSrc) && /status: r\.status/.test(appSrc));
ok("a non-JSON refusal still reaches the throttle check",
   /\.catch\(\(\) => \(\{ error: r\.statusText/.test(appSrc));
ok("the app waits the throttle out instead of asking the user to",
   /THROTTLE_BACKOFF/.test(appSrc) && /setReloadNonce\(n => n \+ 1\)/.test(appSrc));
ok("the retry is bounded, so a real outage cannot loop forever",
   /throttleTries\.current < THROTTLE_BACKOFF\.length/.test(appSrc)
   && /THROTTLE_GAVE_UP/.test(appSrc));
ok("switching symbols clears the previous symbol's countdown",
   /throttleTries\.current = 0;[\s\S]{0,60}setRetryIn\(null\)/.test(appSrc));
ok("the wait is explained in plain words, not as a broker error code",
   /asking us to slow down/.test(appSrc)
   && !/Too Many Requests/.test(appSrc));
ok("the banner tells the user nothing is wrong with the symbol",
   /Nothing is wrong with this symbol/.test(appSrc));
ok("the countdown banner carries a tooltip like everything else",
   /className="error-banner" title=\{retryIn != null/.test(appSrc));

console.log(`\n${passed}/${passed + failed} passed`
            + (failed ? ` — FAILED: ${fails.join(", ")}` : ""));
process.exit(failed ? 1 : 0);
