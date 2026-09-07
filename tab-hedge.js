(function () {
// tab-hedge.jsx — LAZY CHUNK (v4.86). HEDGE FUNDS: PULSE + NAMED FUND WATCH.
//
// What is VERIFIED about each watched manager, and WHEN. Two dates on every
// fact: the date it describes and the date it became public. Between
// filings a manager's current activity is UNKNOWN, and the card says so in
// that word — a strong industry trend never softens it. The trend lives
// under its own heading, in its own evidence class, and is never inside a
// fund's block.
//
// A multi-strategy or quant book (OPAQUE) is shown as what it is: thousands
// of hedged, fast-turnover lines on one day 45+ days ago. The card refuses
// to summarise it as a view. A concentrated book (READABLE) gets the
// quarter-on-quarter diff, because there a change usually means a decision.
//
// Two layers on one tab, and the rule that separates them is the whole
// feature: THE PULSE is the universe from anonymous official data (CFTC
// Leveraged Funds, FINRA short interest, flows, Form PF), and it never
// becomes part of a fund's record. NAMED FUNDS is what specific managers
// actually filed. The Pulse appears on a fund card only under its own
// heading, in its own evidence class, saying it is not about that fund.
//
// Endpoints: GET /api/hf · /api/hf/fund?key= · /api/hf/pulse ·
//            /api/hf/pulse/history · /api/hf/pulse/week?week= ·
//            /api/hf/watchlist (PUT)
// HEDGE_FUND_INTEL.md §5c, §5d.

const HF_TIP = {
  card: "Named Fund Watch. For each manager on your list: what the SEC filings prove, the date those positions were true, the date the public could see them, and what has been filed or said since. Between filings the answer is UNKNOWN, and the card says exactly that.",
  activity: "What can be said about this manager RIGHT NOW. UNKNOWN means nothing verified has been filed since the last 13F's period — the normal state for about 300 days a year. FILED SINCE means a SCHEDULE 13D (an event filing, made within 5 business days of a transaction) describes a later date. CEASED means the manager no longer files with the SEC at all.",
  unknown_days: "Days since the date the last verified positions were true. A 13F is filed up to 45 days after the quarter it describes, and the next one is up to 135 days later. Everything in that gap is unknown, not stable.",
  as_of: "The date these positions were TRUE — the last day of the quarter the 13F covers. Not the day you read it.",
  public_on: "The date the filing became public on EDGAR. The gap between this and the 'as of' date is how stale the picture already was on the day it arrived.",
  amended: "The manager later filed an amendment. A RESTATEMENT replaces the whole table; NEW HOLDINGS adds positions that were left out. The card shows the amended table.",
  turnover: "READABLE: a concentrated book where a quarter-on-quarter change usually means a decision was made. OPAQUE: a multi-strategy or quant book whose 13F is thousands of hedged, fast-turnover lines — read it as what was hedged on one day, never as a view on a sector.",
  opaque_refusal: "This book turns over in days and its biggest reported lines are index options. Its 13F cannot be read as a view on any stock or sector, so this card does not pretend to.",
  status: "FILING: reports under one EDGAR identity. SUCCESSOR: the book moved to a new EDGAR filer (the old one filed a 13F-NT naming the new one) and the card follows it. CEASED: no longer files.",
  successor: "The old identity filed a 13F-NT — a notice that another manager reports the book — and named who. The card read that notice and followed it to the new filer automatically.",
  positions: "How many lines the latest 13F table holds. Each share position and each listed-option position is one line.",
  value: "The total reported value of the table, in dollars, at prices on the 'as of' date.",
  top10: "How much of the reported value sits in the ten largest lines. High means concentrated. For an OPAQUE book the top lines are usually index options — hedges, not bets.",
  options_share: "How much of the reported value is listed options (calls and puts) rather than shares. A 13F is the one place a manager's options positions are disclosed. Near zero for a stock picker; very high for a hedged or short-biased book.",
  change: "Quarter-on-quarter: positions that are NEW, INCREASED, REDUCED or EXITED versus the prior 13F, by shares held. Value alone would move with price even when nothing was traded, so shares decide.",
  new: "Held now, not held at the prior quarter end.",
  increased: "More shares than at the prior quarter end.",
  reduced: "Fewer shares than at the prior quarter end, but still held.",
  exited: "Held at the prior quarter end, gone now.",
  weight: "This line's share of the table's total reported value.",
  put_call: "A listed-option position from the 13F: Call or Put. Shares are blank. A put on a name is not the same as being short it, and a call is not the same as owning it — they can be hedges of something else in the book.",
  sectors: "The table rolled up by sector. A name's sector comes from your own watchlist first, then from Unusual Whales' label, both folded onto the app's eleven sector names. UNMAPPED is what neither could place — reported, never hidden in an 'Other' bucket.",
  unmapped: "Positions whose CUSIP could not be matched to a symbol, or whose symbol has no sector on file. They are counted here so the sector weights are honest about what they leave out.",
  events: "SCHEDULE 13D filings: an activist stake above 5% with intent, filed within 5 business days of the transaction. These are the fastest VERIFIED signal a named fund gives. 13G filings are passive quarterly notices and are listed separately as such.",
  passive: "A SCHEDULE 13G: a passive holder's quarterly notice of a stake above 5%. It describes a quarter end, usually the one the 13F already covers, so it is not evidence that anything happened since.",
  statements: "What the manager said in their OWN channel (a newsletter, a letter). Shown as a quote with its date. A statement is a claim, not a position, and is never counted as one.",
  press: "What news outlets reported the manager did or said. Weaker than a filing and weaker than the manager's own words: it is attributed to the outlet, with the date.",
  filings: "The filing trail: every 13F-family filing with the period it describes and the day it went public, newest first, including amendments and notices.",
  crosscheck: "Unusual Whales parses the same 13F independently. What is compared is WHICH names are the largest, not how many rows each side used — EDGAR lists a book line by line across subsidiaries, Unusual Whales dedupes to tickers. Seven of the ten largest in common is agreement. Disagreement is shown as a conflict — not averaged, not hidden. Too few mapped names to compare is inconclusive, not a conflict.",
  broader: "THE OTHER LAYER, kept apart on purpose. What the hedge fund universe as a whole appears to be doing, from anonymous aggregate sources (CFTC, short interest, flows, prime-broker quotes). It is NOT about this fund and never becomes part of a fund's block — it is rendered here, under its own heading, in its own evidence class, and nothing on the fund's card is computed from it.",
  new_filings: "Every 13D, 13G and 13F-family filing by any watched manager in the last week, read from EDGAR's daily index. This is the raw stream the cards are built from.",
  evidence: "The evidence class of this row. VERIFIED FUND ACTIVITY is a filing (FILING), the manager's own words (STATEMENT), or an outlet's report (PRESS). The four anonymous classes can never carry a fund's name — the code refuses to store such a row.",
  cusips: "How many CUSIP → symbol pairs are on file, from the SEC's fails-to-deliver list. A 13F names securities by CUSIP, not ticker; this is how a line becomes a clickable symbol.",
  refresh: "Re-read this manager's EDGAR trail now. Normally it is re-read every six hours — a filing trail cannot change faster than that matters.",
  watchlist: "Add or remove managers. A manager needs a name, an EDGAR CIK, and a turnover class. Edits are stored separately from the shipped list, so a rebuild never loses them.",
  sort: "Order the cards by when their latest positions went public, by how many positions changed, by name, or by style.",
  ceased: "This manager no longer files with the SEC. The last filing is shown for the record; anything newer is a public statement, and statements are claims.",
  people: "Who runs the book. Names help when the press reports a person rather than the firm.",
  edgar_name: "The manager's exact registered name on EDGAR, and its CIK. Use this if you want to look the filings up yourself.",
  // ── Hedge Fund Pulse (v4.86) ──
  pulse: "THE OTHER LAYER. What the hedge fund universe as a whole appears to be doing, from sources that are official but anonymous — nobody's name is on any of it. Read once a week, on the CFTC's schedule. It is never about any one fund, and no fund's card is ever built from it.",
  pulse_verdict: "The answer to one weekly question, from every source that had something to say. ADDING or REDUCING is the direction the majority of the deciding inputs moved. MIXED means they genuinely disagreed — the card shows both sides rather than averaging them into a consensus that does not exist. NO DATA means nothing answered.",
  pulse_conf: "Not a probability, and not tunable. It counts how many INDEPENDENT evidence classes agree. One source, however official, is one source (LOW). Two classes agreeing is the first point the answer is not an artefact of one provider (MODERATE). Three with no dissent is HIGH. A prime-broker quote can raise confidence in what the data already says; it can never create a verdict on its own.",
  pulse_streak: "How many consecutive weeks this has moved the same way — the 'fourth consecutive week of selling' shape. A week whose move is inside the noise band ends a streak without starting one the other way, so four weeks means four weeks.",
  pulse_persist: "How much of the recent past agrees with this week, at 2, 4, 8 and 12 weeks. 'Three of the last four weeks' is a trend; 'two of the last twelve' is this week and noise. A window with less history than it needs says nothing rather than padding.",
  pulse_unusual: "Where the current LEVEL sits in its own three-year range. Leveraged funds are structurally net short index futures — they hedge long stock books — so a big net short is normal and only its percentile is informative. The change is the signal; the level is context.",
  pulse_input: "One piece of evidence behind the verdict: what it measures, its evidence class, the weekly change, and where its level sits in its own history. A DECIDING input can set a verdict; a SUPPORTING one (a flow proxy, a prime-broker quote) can only corroborate.",
  pulse_conflict: "An input pointing the opposite way to the verdict. Kept and named, never averaged away — you asked for conflicting signals between data sources by name, and this is where they appear.",
  pulse_missing: "Sources that had nothing to say this week: not yet published, or unreachable. Named so a thin answer reads as thin rather than as a broken scanner.",
  pulse_cftc: "CFTC Traders in Financial Futures — the only WEEKLY, official, hedge-fund-specific positioning data that exists. 'Leveraged Funds' is the category hedge funds report under. Positions are as of Tuesday and published the following Friday at 3:30 PM Eastern, so it is three days old the moment it arrives.",
  pulse_gross: "Long plus short contracts — the size of the book on both sides. This is how leverage reads weekly: a fund that doubles both legs has taken more risk and its NET position would not move at all.",
  pulse_ofr: "Industry leverage from SEC Form PF, via the Treasury's Office of Financial Research. The only official answer to 'are hedge funds levering up?' — and about five months behind, so it is context with its own date on it, never this week's picture.",
  pulse_si: "FINRA consolidated short interest: an official count of shares actually sold short, settled twice a month and published about eight business days later. It is the anchor for the short side; the daily short-volume share fills the fortnight between reports.",
  pulse_shvol: "The share of the day's volume that was sold short. This is PRESSURE, not positions — most of it is market-maker inventory covered the same session — so it corroborates and never decides.",
  pulse_sector: "Each sector's verdict and how many inputs it actually has. Only seven of the eleven sectors have a leveraged-fund futures contract at all; the other four are read from flows alone, and the card says which.",
  pulse_sector_rank: "Sectors are ranked by how big this week's move is IN THEIR OWN TERMS — the change divided by that contract's typical weekly move. The sector contracts differ in size by more than tenfold, so ranking by raw contracts would put Financials on top every week regardless of what happened.",
  pulse_nofutures: "No leveraged-fund futures contract exists for this sector, so there is no weekly regulatory reading for it. Its verdict rests on flows only, which is weaker — and saying so is the point.",
  pulse_crowd: "Crowded means at or beyond the 90th percentile of its OWN history on two or more measures — one extreme reading is a number, two agreeing is a position everyone is in. De-crowding means it was crowded and is moving back toward the middle, which is the part that hurts when it happens quickly.",
  pulse_crowd_names: "The most crowded single-name shorts by days to cover — how many days of ordinary volume the shorts would need to buy back. Filtered to names trading over a million shares a day: FINRA caps the field at 1000 days, and without the filter the list is entirely illiquid tickers whose ratio is arithmetic rather than a trade. This names securities; it names no fund.",
  pulse_dtc: "Days to cover: shares short divided by average daily volume. The measure that separates a crowded short from a merely large one.",
  pulse_changed: "Every verdict that reads differently from the stored reading a week ago. This is the only place a previous conclusion is allowed to matter, and it is what makes the board a record rather than a snapshot.",
  pulse_history: "Every week ever read, kept forever, so you can watch positioning evolve rather than only see today.",
  pulse_week: "The ISO week this reading belongs to. The CFTC publishes once a week, so a week is the natural unit of the record; re-reading within the same week replaces that week's entry.",
  pulse_dates: "What each source is AS OF. They are not the same date and never will be: futures positions are Tuesday's, short interest is a fortnight old, Form PF is a quarter old. Every figure carries the date it describes.",
  view: "Three panels. THE PULSE is the whole universe from anonymous data. NAMED FUNDS is what specific managers have actually filed. THE WEEKLY REPORT assembles both into one document and keeps it forever. The Pulse never becomes part of a fund's record — that is the rule the whole feature is built on.",
  // ── The weekly report (v4.87) ──
  report: "One document a week, built from the Pulse, the Named Fund Watch and the prime-broker headlines. Nothing in it is measured here: every figure was computed by one of those three and carried across with its date and its evidence class. Each build is kept forever, so you can read any past week and compare two of them.",
  report_week: "The ISO week this report covers. The CFTC publishes once a week and that sets the rhythm. Rebuilding inside the same week ADDS a revision rather than replacing one — a report records what was known when it was written, so an older build is still true about its own moment.",
  report_revision: "Which build of this week you are reading. Revision 1 is the first time the report was assembled that week; later revisions saw more filings or more headlines. Older revisions are never deleted.",
  report_built: "When this revision was assembled. Different from the 'as of' dates inside it — those belong to the sources, and every one of them is older than this.",
  report_summary: "The short version. Every line here is a verdict reached in a section below, at the confidence stated there, and nothing in this summary is stronger than the section it came from.",
  report_conclusion: "One of the four weekly questions, carried across from the Pulse whole: the verdict, how many independent evidence classes agree, how long it has read this way, and every input behind it.",
  report_trend: "How persistent each answer has been over the last 2, 4, 8 and 12 weeks. A verdict in its eighth straight week is a different statement from the same verdict in its first, and both are shown.",
  report_filings: "The filings worth a heading this week. Every SCHEDULE 13D by a watched manager, because that is an event with a five-business-day clock. Every amendment to a holdings report, because a restatement changes a number already shown to you. Passive 13G notices are not activity and are not listed here.",
  report_activity: "Watched managers who filed something VERIFIED during THIS report's week. When the list is empty that is the ordinary state, not a failure — a 13F describes one day and arrives 45 days later.",
  x_handle: "Optional. The manager's own account on X, without the @ — letters, digits and underscore only. The search is composed from it on the server and never sent from this form, so a handle cannot smuggle in a second account. Only fill this in for an account you know is theirs — a guessed handle would attribute words to a manager who never said them, so nothing is derived from the name. Posts are read only when the deployment has an X bearer token, and they are rendered as STATEMENTS: a claim, never a position.",
  report_filled_in: "This report was stored before the card wrote this sentence, so it was composed just now from the counts the report does carry — using the wording that was true when it was written, which is not always today's wording. The stored file itself is untouched: a report is a record of a moment and is never rewritten.",
  report_carrying: "Managers whose most recent verified filing is newer than their last quarterly holdings report. That state lasts until the next quarterly report arrives, which can be months, so most of these managers did not file anything this week. It is counted here and kept out of the 'this week' list on purpose.",
  report_watchlist: "Who is being watched, and who moved on or off the list since the previous report. Without a previous report there is nothing to compare against, and the section says so rather than showing empty lists that look like 'no changes'.",
  report_conflicts: "Every disagreement in one place, never collapsed: inputs pointing opposite ways inside a question, sectors whose inputs split, and banks quoted this week saying opposite things. Disagreement is a finding — it is not averaged away.",
  report_changed: "What reads differently from the STORED report of the previous week. A diff against a record, not a memory. That is the whole reason every report is kept.",
  report_history: "Every week ever assembled, newest first. Pick one to read it, or compare two to watch positioning evolve.",
  report_compare: "Two stored weeks side by side. Verdicts that match are marked the same; the rest show what moved. Compare uses the same diff as 'what changed', so the two views can never disagree.",
  report_limits: "What this report cannot do, stated plainly, so a confident-looking verdict is never read as more than it is.",
  // ── The outcome grader (v4.88) ──
  grade: "The only honest answer to 'has any of this mattered?'. For every past week the board can reconstruct, it looks up what the matching market did over the following 1, 2, 4 and 8 weeks, and keeps the record. It does NOT claim positioning predicts returns — it records what followed, with the sample size and the interval attached so you can see how much to believe.",
  grade_reversal: "A crowded book has a SIDE, so 'reversal' has a meaning: the market moved against the side the crowd was on. That is the one place a hit rate is defined here, and it is the question you asked for in those words.",
  grade_base: "The same market's rate across EVERY week, crowded or not. This is the number that matters. 'Crowded weeks reversed 55% of the time' means nothing until you know an ordinary week reversed 52% of the time — the comparison is the finding, not the raw share.",
  grade_lift: "The crowded rate minus the base rate. Zero means crowding told you nothing you did not already get from the market itself. A market that fell all year would show a high crowded rate and an equally high base rate, which is exactly why both are shown.",
  grade_interval: "The 95% interval around the share. It is wide, and it stays wide for a long time. A share without an interval invites reading three out of four as a finding.",
  grade_episodes: "How many separate EVENTS the crowded weeks are. Crowding arrives in runs — a book stays crowded for a month — so forty crowded weeks can be five episodes. The intervals on this page are computed as though every week were an independent draw, which makes them OPTIMISTIC. This is the number that should temper them.",
  grade_horizon: "How far ahead the return is measured, in weeks. Positioning is said to matter over a month or two, so four horizons are shown rather than one.",
  grade_source: "Crowding is computed from the CFTC series and nothing else, so truncating that series at each past week reproduces exactly what the board would have said then, with no data that arrived later. That is why three years can be graded when the board only started storing readings in September 2026.",
  names: "The long side of 'which stocks are crowded'. The short side above comes from FINRA and names nobody, because short interest belongs to nobody. This comes from the managers' own quarterly filings, so the funds are named beside each stock. It counts each manager's TEN LARGEST reported positions, so it says how many readable books hold a name among their ten largest — never how many own it, which would need whole books.",
  names_name: "The stock. Where the ticker map knew the CUSIP in the filing, this is a ticker you can click to open the stock; where it did not, it is the issuer name exactly as the filing spelled it.",
  names_counted: "How many of the watched managers were counted, out of how many are watched. A consensus of eleven means one thing out of eleven and quite another out of thirty-two, so both numbers are always shown.",
  names_opaque: "Multi-strategy and quant books are left out. Their quarterly filing shows hedging, index exposure and the long leg of trades whose short leg never appears in it — the fund cards already refuse to read those as conviction, and counting them here would be the same fiction at a larger scale.",
  names_unread: "Managers whose filings have not been read yet. They are neither counted nor treated as though they held nothing.",
  names_held: "Stocks that appear among the ten largest positions of two or more readable books. A name held in eleventh place by every manager would not appear here at all — only the ten largest are kept.",
  names_bought: "Stocks two or more readable books either bought for the first time or added to last quarter, from the quarter-on-quarter change in their filings.",
  names_sold: "Stocks two or more readable books trimmed or sold out of entirely last quarter. Selling is not a view about the stock alone — a fund raising cash sells what it can — so this is what happened, not why.",
  names_puts: "Names held as PUT options, which is a bet AGAINST the stock, not ownership of it. They are listed apart on purpose: folding a put into the holdings tables would report the position exactly backwards.",
  names_calls: "Some of these managers hold the name as call options rather than shares. A call is still a long bet, so it is counted, but it is flagged because it is not the same thing as owning the stock.",
  names_who: "The managers holding it. These rows come from filings signed by a named fund, which is the only kind of evidence on this board that may carry a fund's name at all.",
  names_period: "The date each manager's position was true. A quarterly filing describes ONE day and arrives about 45 days later, and managers do not all file for the same quarter — so where two dates are shown, this row mixes one manager's book with another's from an earlier quarter.",
  names_unmapped: "The ticker map did not know this position's CUSIP, so it is shown under the issuer name from the filing. It is still counted — a position is never dropped just because the map has a gap.",
  grade_replay: "Most of these weeks were not recorded at the time — the board only began keeping its weekly record in September 2026. They were recomputed afterwards from the data as it stood in each past week: the futures report published that week, the short-interest reading already public, the daily files already out. Nothing that arrived later is allowed in.",
  grade_recorded: "Weeks the board actually stored at the time. These are answers it really published, not reconstructions.",
  grade_replayed: "Weeks recomputed afterwards. A question is only recomputed for a week when every input that decides it today was public that week — rebuilt from fewer inputs it would be a different answer wearing the same name, and grading it would tell you nothing about the answers the board really gives.",
  grade_replay_span: "The oldest week this question could be recomputed for. The four do not reach equally far back, because they do not read the same things: two are decided by the futures report alone and reach as far as it goes, while the others wait for short interest, the daily short-volume share, or ETF creations.",
  grade_replay_skipped: "The input that was missing for the weeks this question could not reach, and how many weeks it cost. The daily short-volume history deepens by a few dozen sessions on every rebuild, so that answer reaches further back each week rather than all at once.",
  grade_verdicts: "The four weekly questions are NOT scored as right or wrong. 'Hedge funds reduced exposure' is a fact about positioning and implies nothing about what the market does next; scoring it as a forecast would put a claim in the board's mouth. What is shown is the returns that followed each answer, beside the returns across every week.",
  grade_not_graded: "A market with no honest tradable proxy is not graded at all. VIX is the case: its listed funds roll a futures curve, so an eight-week return measures the roll rather than the index.",
  grade_proxy: "A futures position cannot be priced from the CFTC report, so the grade is measured on the fund that market's participants actually track — the S&P 500 contract against SPY, the Financials contract against XLF, and so on.",
  press: "PRIME BROKER AGGREGATE DATA. Goldman Sachs, Morgan Stanley and JPMorgan tell their prime brokerage clients each week what hedge funds did; the wires quote those notes. Secondhand by definition — a bank's summary of its own clients, retold by a reporter. It can raise confidence in what the measured data already says and can never create a verdict alone.",
  press_quote: "A quoted claim that survived every filter: the sentence names hedge funds, cites a bank as the SOURCE (not merely mentions one), is about positioning rather than returns, is not about a foreign market, and points unambiguously one way.",
  press_carried: "How many outlets carried this same claim. When five outlets repeat one Goldman note, that is one note — the claim counts once, and this is how widely it travelled.",
  press_period: "Prime-broker headlines say 'last week' or 'for a fourth consecutive week' rather than giving dates, so the period is not machine-readable and is never guessed. Only the publication date is claimed here.",
  press_captured: "Headlines that were read and NOT counted as evidence, with the reason. Shown because 'we saw this and did not use it' is worth as much as the list of what was used.",
  press_outlet: "Which outlet carried it. Only wire services and the banks' own publications count as evidence; anything else is captured and shown but raises no confidence."
};
const hfDate = s => {
  if (!s) return "—";
  const d = new Date(String(s).slice(0, 10) + "T12:00:00");
  return isNaN(d) ? String(s) : d.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric"
  });
};
const hfDateTime = s => {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d)) return String(s);
  return `${d.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric"
  })} at ${d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit"
  })}`;
};
// "2023-W35" is how the record keys a week, and it is never what a reader
// should see. This turns it into the Monday that opened it, spelled out.
const hfWeekLabel = w => {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(String(w || ""));
  if (!m) return w ? String(w) : "—";
  const jan4 = new Date(Date.UTC(Number(m[1]), 0, 4));
  // ISO week 1 is the one containing January 4th; Monday is day 1.
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - (jan4.getUTCDay() + 6) % 7 + (Number(m[2]) - 1) * 7);
  return `week of ${monday.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC"
  })}`;
};
const hfMoney = v => {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(a >= 1e10 ? 0 : 1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${Number(v).toFixed(0)}`;
};
const hfPct = (v, d = 1) => v == null || !isFinite(v) ? "—" : `${Number(v).toFixed(d)}%`;
const hfInt = v => v == null || !isFinite(v) ? "—" : Number(v).toLocaleString("en-US");
const hfSigned = v => v == null || !isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toLocaleString("en-US")}`;

// apiFetch hands back a Response. Read it as JSON, and when it is not JSON
// (the sign-in page after a session expires, a proxy error) say so in words
// instead of throwing "Unexpected token '<'".
async function hfReadJson(r) {
  let text = "";
  try {
    text = await r.text();
  } catch (e) {
    return {
      d: null,
      err: String(e && e.message || e)
    };
  }
  try {
    return {
      d: JSON.parse(text),
      err: null
    };
  } catch (e) {
    return {
      d: null,
      err: r && r.status === 401 ? "The app answered 401 — the API key the page holds is not the one the server expects." : "The app answered with a page instead of data — usually the sign-in screen after the session expires. Reload the page to sign back in."
    };
  }
}
function HfTag({
  cls,
  sub,
  title
}) {
  const short = {
    "VERIFIED FUND ACTIVITY": "VERIFIED",
    "PRIME BROKER AGGREGATE DATA": "PRIME BROKER",
    "REGULATORY POSITIONING DATA": "REGULATORY",
    "INSTITUTIONAL FLOW PROXY": "FLOW PROXY",
    "MODEL INFERENCE": "INFERENCE"
  }[cls] || cls;
  const k = (short || "").toLowerCase().replace(/[^a-z]+/g, "-");
  return /*#__PURE__*/React.createElement("span", {
    className: `hf-tag hf-tag-${k}`,
    title: title || HF_TIP.evidence
  }, short, sub ? ` · ${sub}` : "");
}
function HfActivity({
  activity,
  size
}) {
  const st = activity && activity.state || "NOT READ YET";
  const k = st.toLowerCase().replace(/[^a-z]+/g, "-");
  return /*#__PURE__*/React.createElement("div", {
    className: `hf-activity hf-activity-${k} ${size === "big" ? "hf-activity-big" : ""}`,
    title: HF_TIP.activity
  }, /*#__PURE__*/React.createElement("span", {
    className: "hf-activity-label"
  }, "Current activity"), /*#__PURE__*/React.createElement("span", {
    className: "hf-activity-state"
  }, st), st === "UNKNOWN" && activity.days_since != null ? /*#__PURE__*/React.createElement("span", {
    className: "hf-activity-sub",
    title: HF_TIP.unknown_days
  }, "nothing verified for ", activity.days_since, " days") : null, st === "FILED SINCE" && activity.items && activity.items.length ? /*#__PURE__*/React.createElement("span", {
    className: "hf-activity-sub"
  }, activity.items.length, " event filing", activity.items.length === 1 ? "" : "s", " after ", hfDate(activity.since)) : null, st === "CEASED" ? /*#__PURE__*/React.createElement("span", {
    className: "hf-activity-sub",
    title: HF_TIP.ceased
  }, "no longer files with the SEC") : null);
}
function HfDates({
  m
}) {
  return /*#__PURE__*/React.createElement("dl", {
    className: "hf-dates"
  }, /*#__PURE__*/React.createElement("dt", {
    title: HF_TIP.as_of
  }, "Positions true as of"), /*#__PURE__*/React.createElement("dd", {
    title: HF_TIP.as_of
  }, hfDate(m.last_as_of)), /*#__PURE__*/React.createElement("dt", {
    title: HF_TIP.public_on
  }, "Public on"), /*#__PURE__*/React.createElement("dd", {
    title: HF_TIP.public_on
  }, hfDate(m.last_public_on)), m.amended_on ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("dt", {
    title: HF_TIP.amended
  }, "Amended on"), /*#__PURE__*/React.createElement("dd", {
    title: HF_TIP.amended
  }, hfDate(m.amended_on))) : null);
}
function HfPositionsTable({
  rows,
  kind,
  onOpenTicker,
  showDelta
}) {
  if (!rows || !rows.length) return /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "None.");
  return /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.positions
  }, "Name"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.put_call
  }, "Option"), showDelta ? /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.change
  }, "Shares before") : null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.positions
  }, "Shares now"), showDelta ? /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.change
  }, "Change") : null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.value
  }, "Value"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.weight
  }, "Weight"))), /*#__PURE__*/React.createElement("tbody", null, rows.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: `${kind}|${r.cusip}|${r.put_call || ""}|${r.title || ""}|${i}`
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Name"
  }, r.symbol ? /*#__PURE__*/React.createElement("button", {
    className: "hf-sym",
    title: `Open ${r.symbol} on the Trade tab`,
    onClick: () => onOpenTicker && onOpenTicker(r.symbol)
  }, r.symbol) : /*#__PURE__*/React.createElement("span", {
    className: "hf-sym hf-sym-none",
    title: "No symbol could be matched to this CUSIP"
  }, "\u2014"), /*#__PURE__*/React.createElement("span", {
    className: "hf-issuer"
  }, " ", r.issuer)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Option",
    title: HF_TIP.put_call
  }, r.put_call || ""), showDelta ? /*#__PURE__*/React.createElement("td", {
    "data-label": "Shares before"
  }, hfInt(r.shares_prev)) : null, /*#__PURE__*/React.createElement("td", {
    "data-label": "Shares now"
  }, hfInt(r.shares_now != null ? r.shares_now : r.shares)), showDelta ? /*#__PURE__*/React.createElement("td", {
    "data-label": "Change",
    className: r.delta_shares > 0 ? "up" : r.delta_shares < 0 ? "down" : ""
  }, hfSigned(r.delta_shares), r.delta_pct != null && isFinite(r.delta_pct) ? ` (${r.delta_pct > 0 ? "+" : ""}${r.delta_pct.toFixed(0)}%)` : "") : null, /*#__PURE__*/React.createElement("td", {
    "data-label": "Value"
  }, hfMoney(r.value_now != null ? r.value_now : r.value)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Weight"
  }, hfPct(r.weight_now != null ? r.weight_now : r.weight)))))));
}
function HfBroader({
  bt
}) {
  return /*#__PURE__*/React.createElement("section", {
    className: "hf-broader",
    title: HF_TIP.broader
  }, /*#__PURE__*/React.createElement("h4", null, "Broader hedge fund trend ", /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "(not about any one fund)"), " ", /*#__PURE__*/React.createElement(HfTag, {
    cls: bt && bt.class || "MODEL INFERENCE"
  })), bt && bt.available ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("p", null, bt.text), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.pulse_dates
  }, bt.as_of_text ? `Futures positions as of ${bt.as_of_text}` : null, bt.week ? ` · week ${bt.week}` : null, bt.confidence ? ` · confidence ${bt.confidence}` : null), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, bt.note)) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, bt && bt.note || "Not available."));
}
function FundDetail({
  d,
  onOpenTicker
}) {
  if (!d) return null;
  const opaque = d.turnover === "OPAQUE";
  const ch = d.change || {};
  const sec = d.sectors || {};
  const events = (d.events || []).filter(e => e.kind === "EVENT");
  const passive = (d.events || []).filter(e => e.kind !== "EVENT");
  return /*#__PURE__*/React.createElement("div", {
    className: "hf-detail"
  }, d.describe ? /*#__PURE__*/React.createElement("p", {
    className: "hf-describe",
    title: HF_TIP.turnover
  }, d.describe) : null, d.edgar_name ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.edgar_name
  }, "EDGAR: ", d.edgar_name, " \xB7 CIK ", d.cik) : null, d.holdings ? /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.positions
  }, "What the latest filing shows ", /*#__PURE__*/React.createElement(HfTag, {
    cls: "VERIFIED FUND ACTIVITY",
    sub: "FILING"
  })), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, hfInt(d.holdings.n), " positions worth ", hfMoney(d.holdings.value_total), ", true as of ", /*#__PURE__*/React.createElement("b", null, d.dates.last_as_of), ", public on ", /*#__PURE__*/React.createElement("b", null, d.dates.last_public_on), d.holdings.amended_on ? /*#__PURE__*/React.createElement(React.Fragment, null, ", amended on ", /*#__PURE__*/React.createElement("b", null, d.dates.amended_on), " (", (d.holdings.amendments || []).join(", ").toLowerCase(), ")") : null, ".", d.concentration ? /*#__PURE__*/React.createElement(React.Fragment, null, " ", "Top ten lines are ", /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.top10
  }, hfPct(d.concentration.top10_weight)), " of the value; listed options are ", /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.options_share
  }, hfPct(d.concentration.options_value_share)), ".") : null), opaque ? /*#__PURE__*/React.createElement("p", {
    className: "hf-refusal",
    title: HF_TIP.opaque_refusal
  }, "Read this as what was hedged on one day, not as a view on any stock or sector. This card will not summarise it as one.") : null, /*#__PURE__*/React.createElement("h5", {
    title: HF_TIP.top10
  }, "Largest reported lines"), /*#__PURE__*/React.createElement(HfPositionsTable, {
    rows: d.top,
    kind: "top",
    onOpenTicker: onOpenTicker
  })) : null, d.change && !opaque ? /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.change
  }, "Changes since ", d.dates.prior_as_of || "the prior quarter", " ", /*#__PURE__*/React.createElement(HfTag, {
    cls: "VERIFIED FUND ACTIVITY",
    sub: "FILING"
  })), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, ch.new.length, " new \xB7 ", ch.increased.length, " increased \xB7 ", ch.reduced.length, " reduced \xB7 ", ch.exited.length, " exited \xB7 ", ch.unchanged, " unchanged"), ch.new.length ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("h5", {
    title: HF_TIP.new
  }, "New"), /*#__PURE__*/React.createElement(HfPositionsTable, {
    rows: ch.new,
    kind: "new",
    onOpenTicker: onOpenTicker,
    showDelta: true
  })) : null, ch.increased.length ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("h5", {
    title: HF_TIP.increased
  }, "Increased"), /*#__PURE__*/React.createElement(HfPositionsTable, {
    rows: ch.increased,
    kind: "inc",
    onOpenTicker: onOpenTicker,
    showDelta: true
  })) : null, ch.reduced.length ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("h5", {
    title: HF_TIP.reduced
  }, "Reduced"), /*#__PURE__*/React.createElement(HfPositionsTable, {
    rows: ch.reduced,
    kind: "red",
    onOpenTicker: onOpenTicker,
    showDelta: true
  })) : null, ch.exited.length ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("h5", {
    title: HF_TIP.exited
  }, "Exited"), /*#__PURE__*/React.createElement(HfPositionsTable, {
    rows: ch.exited,
    kind: "exit",
    onOpenTicker: onOpenTicker,
    showDelta: true
  })) : null) : null, d.change && opaque ? /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.change
  }, "Line count since ", d.dates.prior_as_of || "the prior quarter"), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.opaque_refusal
  }, ch.new.length, " new \xB7 ", ch.increased.length, " increased \xB7 ", ch.reduced.length, " reduced \xB7 ", ch.exited.length, " exited \xB7 ", ch.unchanged, " unchanged \u2014 counts only. For a book like this the lines are hedges that turn over in days; listing them as decisions would be fiction.")) : null, sec.sectors ? /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.sectors
  }, "Sector exposure of the reported table"), sec.sectors.length ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-sectors"
  }, sec.sectors.map(s => /*#__PURE__*/React.createElement("li", {
    key: s.sector,
    title: HF_TIP.sectors
  }, /*#__PURE__*/React.createElement("b", null, s.sector), " ", hfPct(s.weight), " ", /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "(", s.n, ")")))) : null, /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.unmapped
  }, "Unmapped: ", sec.unmapped ? `${sec.unmapped.n} positions, ${hfPct(sec.unmapped.weight)} of value` : "—")) : null, /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.events
  }, "Event filings (13D) in the last ", d.event_window_days || 180, " days ", /*#__PURE__*/React.createElement(HfTag, {
    cls: "VERIFIED FUND ACTIVITY",
    sub: "FILING"
  })), events.length ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-events"
  }, events.map(e => /*#__PURE__*/React.createElement("li", {
    key: e.accession,
    title: HF_TIP.events
  }, /*#__PURE__*/React.createElement("b", null, e.form), " \xB7 ", e.issuer || "issuer not stated", e.percent != null ? ` · ${hfPct(e.percent)} of class` : "", " \xB7 event ", hfDate(e.as_of), " \xB7 public ", hfDate(e.public_on), e.purpose ? /*#__PURE__*/React.createElement("div", {
    className: "hf-quote"
  }, String(e.purpose).slice(0, 320), String(e.purpose).length > 320 ? "…" : "") : null))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "None."), passive.length ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.passive
  }, passive.length, " passive notice", passive.length === 1 ? "" : "s", " (13G) in the window \u2014 not evidence of activity since the last 13F.") : null), /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.statements
  }, "In their own words ", /*#__PURE__*/React.createElement(HfTag, {
    cls: "VERIFIED FUND ACTIVITY",
    sub: "STATEMENT"
  })), d.statements && d.statements.length ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-statements"
  }, d.statements.map(s => /*#__PURE__*/React.createElement("li", {
    key: s.link || s.title,
    title: HF_TIP.statements
  }, /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, hfDateTime(s.published), " \xB7 ", s.channel), /*#__PURE__*/React.createElement("div", null, d.people && d.people[0] || d.name, " wrote: ", /*#__PURE__*/React.createElement("a", {
    href: s.link,
    target: "_blank",
    rel: "noopener noreferrer"
  }, s.title)), s.summary ? /*#__PURE__*/React.createElement("div", {
    className: "hf-quote"
  }, s.summary) : null))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "No feed on file, or nothing in the window. A statement is a claim, not a position.")), /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.press
  }, "Reported by the press ", /*#__PURE__*/React.createElement(HfTag, {
    cls: "VERIFIED FUND ACTIVITY",
    sub: "PRESS"
  })), d.press && d.press.length ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-press"
  }, d.press.map(p => /*#__PURE__*/React.createElement("li", {
    key: p.link || p.title,
    title: HF_TIP.press
  }, /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, hfDateTime(p.published), p.outlet ? ` · ${p.outlet}` : ""), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("a", {
    href: p.link,
    target: "_blank",
    rel: "noopener noreferrer"
  }, p.title))))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "Nothing in the window.")), /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.filings
  }, "Filing trail"), d.filings && d.filings.length ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Form"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.as_of
  }, "Describes"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.public_on
  }, "Public on"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.amended
  }, "Amendment"), /*#__PURE__*/React.createElement("th", null, "Lines"), /*#__PURE__*/React.createElement("th", null, "Value"))), /*#__PURE__*/React.createElement("tbody", null, d.filings.map(f => /*#__PURE__*/React.createElement("tr", {
    key: f.accession
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Form"
  }, f.form), /*#__PURE__*/React.createElement("td", {
    "data-label": "Describes"
  }, hfDate(f.as_of)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Public on"
  }, hfDate(f.public_on)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Amendment"
  }, f.amendment_type || ""), /*#__PURE__*/React.createElement("td", {
    "data-label": "Lines"
  }, hfInt(f.entries)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Value"
  }, hfMoney(f.value_total))))))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "None read.")), d.crosscheck ? /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.crosscheck
  }, "Cross-check against Unusual Whales"), d.crosscheck.available ? /*#__PURE__*/React.createElement("p", {
    className: d.crosscheck.agree === false ? "hf-conflict" : "hf-muted",
    title: HF_TIP.crosscheck
  }, "Largest positions in common: ", hfInt(d.crosscheck.top_overlap), " of ", hfInt(d.crosscheck.top_compared), " \xB7 EDGAR ", hfInt(d.crosscheck.n_edgar), " positions (", hfInt(d.crosscheck.n_edgar_lines), " lines) \xB7 Unusual Whales ", hfInt(d.crosscheck.n_uw), " rows \xB7", " ", d.crosscheck.agree === true ? "agree" : d.crosscheck.agree === false ? "CONFLICT — shown, not resolved" : "too few mapped names to compare") : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "Unusual Whales had no parse of this filing.")) : null, d.notes && d.notes.length ? /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", null, "Notes"), /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, d.notes.map((n, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, n)))) : null, /*#__PURE__*/React.createElement(HfBroader, {
    bt: d.broader_trend
  }));
}
function FundCard({
  m,
  open,
  onToggle,
  detail,
  loading,
  onOpenTicker,
  onRefresh
}) {
  const changed = (m.n_new || 0) + (m.n_increased || 0) + (m.n_reduced || 0) + (m.n_exited || 0);
  return /*#__PURE__*/React.createElement("div", {
    className: `hf-card ${open ? "hf-card-open" : ""} hf-turnover-${(m.turnover || "").toLowerCase()}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "hf-card-head",
    onClick: onToggle,
    role: "button",
    tabIndex: 0,
    onKeyDown: e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onToggle();
      }
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hf-card-title"
  }, /*#__PURE__*/React.createElement("h3", null, m.name), /*#__PURE__*/React.createElement("div", {
    className: "hf-badges"
  }, /*#__PURE__*/React.createElement("span", {
    className: "hf-badge",
    title: HF_TIP.people
  }, m.style || "—"), /*#__PURE__*/React.createElement("span", {
    className: `hf-badge hf-badge-${(m.turnover || "").toLowerCase()}`,
    title: HF_TIP.turnover
  }, m.turnover || "—"), /*#__PURE__*/React.createElement("span", {
    className: `hf-badge hf-badge-status-${(m.status || "").toLowerCase()}`,
    title: HF_TIP.status
  }, m.status || "—"))), /*#__PURE__*/React.createElement(HfActivity, {
    activity: m.activity
  })), /*#__PURE__*/React.createElement(HfDates, {
    m: m
  }), m.last_as_of ? /*#__PURE__*/React.createElement("p", {
    className: "hf-facts"
  }, /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.positions
  }, hfInt(m.n_positions), " positions"), " \xB7 ", /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.value
  }, hfMoney(m.value_total)), " \xB7 ", /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.top10
  }, "top 10 ", hfPct(m.top10_weight)), " \xB7 ", /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.options_share
  }, "options ", hfPct(m.options_value_share)), m.turnover === "READABLE" ? /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 ", /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.change
  }, changed, " lines changed")) : null, m.n_events ? /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 ", /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.events
  }, m.n_events, " 13D/13G")) : null, m.n_statements ? /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 ", /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.statements
  }, m.n_statements, " statements")) : null) : null, m.turnover === "OPAQUE" && m.last_as_of ? /*#__PURE__*/React.createElement("p", {
    className: "hf-refusal hf-refusal-small",
    title: HF_TIP.opaque_refusal
  }, "Hedged, fast-turnover book \u2014 not read as a view.") : null, m.successor ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.successor
  }, "Book moved to ", m.successor.name, " (CIK ", m.successor.cik, ") from ", hfDate(m.successor.as_of), "; followed.") : null, m.notes && m.notes.length > 0 && !open ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted hf-note-line"
  }, m.notes[0]) : null, /*#__PURE__*/React.createElement("div", {
    className: "hf-card-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "sl-mode",
    onClick: onToggle,
    title: "Show or hide the full record"
  }, open ? "Hide detail" : "Show detail"), onRefresh ? /*#__PURE__*/React.createElement("button", {
    className: "sl-mode",
    onClick: () => onRefresh(m.key),
    title: HF_TIP.refresh
  }, "Re-read EDGAR") : null), open ? loading ? /*#__PURE__*/React.createElement("div", {
    className: "st-loading",
    "aria-busy": "true"
  }, /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "60%"
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "90%"
    }
  })) : /*#__PURE__*/React.createElement(FundDetail, {
    d: detail,
    onOpenTicker: onOpenTicker
  }) : null);
}
function HfNewFilings({
  rows
}) {
  return /*#__PURE__*/React.createElement("section", {
    className: "hf-newfilings"
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.new_filings
  }, "Filings by watched managers this week ", /*#__PURE__*/React.createElement(HfTag, {
    cls: "VERIFIED FUND ACTIVITY",
    sub: "FILING"
  })), rows && rows.length ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Public on"), /*#__PURE__*/React.createElement("th", null, "Manager"), /*#__PURE__*/React.createElement("th", null, "Form"), /*#__PURE__*/React.createElement("th", null, "Filed as"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => /*#__PURE__*/React.createElement("tr", {
    key: `${r.accession}|${r.cik}`
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Public on"
  }, hfDate(r.filed)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Manager"
  }, r.manager), /*#__PURE__*/React.createElement("td", {
    "data-label": "Form"
  }, r.form), /*#__PURE__*/React.createElement("td", {
    "data-label": "Filed as"
  }, r.company)))))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "None found in the last week, or the daily index has not been read yet."));
}
function HfWatchlistEditor({
  apiFetch,
  onSaved
}) {
  const [openEd, setOpenEd] = React.useState(false);
  const [reg, setReg] = React.useState(null);
  const [form, setForm] = React.useState({
    name: "",
    cik: "",
    style: "",
    turnover: "READABLE"
  });
  const [msg, setMsg] = React.useState(null);
  const load = React.useCallback(async () => {
    try {
      const {
        d,
        err
      } = await hfReadJson(await apiFetch("/api/hf/watchlist"));
      if (d) setReg(d);else setMsg(err);
    } catch (e) {
      setMsg(String(e && e.message || e));
    }
  }, [apiFetch]);
  React.useEffect(() => {
    if (openEd && !reg) load();
  }, [openEd, reg, load]);
  const save = async overlay => {
    setMsg(null);
    try {
      const {
        d,
        err
      } = await hfReadJson(await apiFetch("/api/hf/watchlist", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(overlay)
      }));
      if (d && d.ok) {
        setMsg("Saved.");
        setReg(null);
        onSaved && onSaved();
      } else setMsg("Not saved: " + (err || JSON.stringify(d && (d.problems || d.error) || d)));
    } catch (e) {
      setMsg("Not saved: " + String(e && e.message || e));
    }
  };
  const overlay = reg && reg.overlay || {
    managers: [],
    removed: []
  };
  const add = () => {
    const cik = parseInt(form.cik, 10);
    if (!form.name || !cik) {
      setMsg("A name and a CIK are required.");
      return;
    }
    const key = form.name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    const handle = String(form.x || "").trim().replace(/^@/, "");
    // A handle reaches an X search, so anything but a real one is refused
    // here rather than sanitised later. "BillAckman OR from:someone_else"
    // would have returned a second account's posts and filed them under
    // this manager's own words. The search itself is composed on the
    // server from the handle; no query is ever sent from here.
    if (handle && !/^[A-Za-z0-9_]{1,15}$/.test(handle)) {
      setMsg("That X handle is not valid. Use letters, digits and underscore only, up to 15.");
      return;
    }
    const entry = {
      key,
      name: form.name,
      style: form.style || "fund",
      turnover: form.turnover,
      ciks: [{
        cik,
        from: null
      }],
      feeds: [],
      news_query: form.name
    };
    // Only written when the user supplies one. A guessed handle would put
    // words in a manager's mouth, so nothing is derived from the name.
    if (handle) entry.x_handle = handle;
    save({
      managers: [...(overlay.managers || []).filter(m => m.key !== key), entry],
      removed: (overlay.removed || []).filter(k => k !== key)
    });
  };
  const remove = key => save({
    managers: (overlay.managers || []).filter(m => m.key !== key),
    removed: [...new Set([...(overlay.removed || []), key])]
  });
  const restore = key => save({
    managers: overlay.managers || [],
    removed: (overlay.removed || []).filter(k => k !== key)
  });
  return /*#__PURE__*/React.createElement("section", {
    className: "hf-editor"
  }, /*#__PURE__*/React.createElement("button", {
    className: "sl-mode",
    onClick: () => setOpenEd(!openEd),
    title: HF_TIP.watchlist
  }, openEd ? "Close watchlist editor" : "Edit watchlist"), openEd ? /*#__PURE__*/React.createElement("div", {
    className: "hf-editor-body"
  }, /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.watchlist
  }, "Add a manager by EDGAR CIK (find it at sec.gov/cgi-bin/browse-edgar). Remove one to hide it; removed managers can be restored."), /*#__PURE__*/React.createElement("div", {
    className: "hf-editor-row"
  }, /*#__PURE__*/React.createElement("input", {
    placeholder: "Name",
    value: form.name,
    onChange: e => setForm({
      ...form,
      name: e.target.value
    }),
    title: "The manager's name as you want it shown"
  }), /*#__PURE__*/React.createElement("input", {
    placeholder: "CIK",
    value: form.cik,
    onChange: e => setForm({
      ...form,
      cik: e.target.value
    }),
    title: "EDGAR Central Index Key \u2014 digits only"
  }), /*#__PURE__*/React.createElement("input", {
    placeholder: "Style",
    value: form.style,
    onChange: e => setForm({
      ...form,
      style: e.target.value
    }),
    title: "activist, quant, macro\u2026 free text"
  }), /*#__PURE__*/React.createElement("select", {
    value: form.turnover,
    onChange: e => setForm({
      ...form,
      turnover: e.target.value
    }),
    title: HF_TIP.turnover
  }, /*#__PURE__*/React.createElement("option", {
    value: "READABLE"
  }, "READABLE"), /*#__PURE__*/React.createElement("option", {
    value: "OPAQUE"
  }, "OPAQUE")), /*#__PURE__*/React.createElement("input", {
    placeholder: "X handle (optional)",
    value: form.x || "",
    onChange: e => setForm({
      ...form,
      x: e.target.value
    }),
    title: HF_TIP.x_handle
  }), /*#__PURE__*/React.createElement("button", {
    className: "sl-mode",
    onClick: add
  }, "Add")), reg ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-editor-list"
  }, (reg.registry.managers || []).map(m => /*#__PURE__*/React.createElement("li", {
    key: m.key
  }, /*#__PURE__*/React.createElement("b", null, m.name), " ", /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "CIK ", (m.ciks || []).map(c => c.cik).join(" → "), " \xB7 ", m.turnover), " ", /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: () => remove(m.key)
  }, "remove"))), (overlay.removed || []).map(k => /*#__PURE__*/React.createElement("li", {
    key: `removed-${k}`,
    className: "hf-muted"
  }, k, " (removed) ", /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: () => restore(k)
  }, "restore")))) : null, msg ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, msg) : null) : null);
}

// ══════════════════════════════════════════════════════════════════════════
// THE PULSE — the aggregate layer. Anonymous sources only; no fund is named
// anywhere in this half of the tab.
// ══════════════════════════════════════════════════════════════════════════

const HF_CONF_ORDER = {
  HIGH: 3,
  MODERATE: 2,
  LOW: 1,
  NONE: 0
};
function HfConfidence({
  c
}) {
  if (!c) return null;
  const k = String(c.level || "NONE").toLowerCase();
  return /*#__PURE__*/React.createElement("span", {
    className: `hf-conf hf-conf-${k}`,
    title: `${HF_TIP.pulse_conf}\n\n${c.why || ""}`
  }, "confidence ", c.level, c.classes ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, " \xB7 ", c.classes, " class", c.classes === 1 ? "" : "es") : null);
}
function HfInputs({
  inputs,
  open
}) {
  if (!open || !inputs || !inputs.length) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_input
  }, "Measure"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.evidence
  }, "Evidence"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_input
  }, "Weekly change"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_unusual
  }, "Level percentile"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.as_of
  }, "As of"))), /*#__PURE__*/React.createElement("tbody", null, inputs.map(i => /*#__PURE__*/React.createElement("tr", {
    key: i.key
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Measure",
    title: i.note || ""
  }, i.label, i.weight < 1 ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted",
    title: HF_TIP.pulse_input
  }, " \xB7 supporting") : null), /*#__PURE__*/React.createElement("td", {
    "data-label": "Evidence"
  }, /*#__PURE__*/React.createElement(HfTag, {
    cls: i.class
  })), /*#__PURE__*/React.createElement("td", {
    "data-label": "Weekly change",
    className: i.direction > 0 ? "up" : i.direction < 0 ? "down" : ""
  }, i.change == null ? "—" : hfSigned(Math.round(i.change)), i.direction === 0 && i.change != null ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted",
    title: "Inside this measure's own noise band"
  }, " (no direction)") : null), /*#__PURE__*/React.createElement("td", {
    "data-label": "Level percentile",
    title: HF_TIP.pulse_unusual
  }, i.percentile == null ? "—" : `${i.percentile}th`, i.n ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, " of ", hfInt(i.n)) : null), /*#__PURE__*/React.createElement("td", {
    "data-label": "As of"
  }, hfDate(i.as_of)))))));
}
function HfQuestion({
  q
}) {
  const [open, setOpen] = React.useState(false);
  if (!q) return null;
  const v = String(q.verdict || "NO DATA");
  const k = v.toLowerCase().replace(/[^a-z]+/g, "-");
  const st = q.streak || {};
  return /*#__PURE__*/React.createElement("div", {
    className: `hf-q hf-q-${k}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "hf-q-head"
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.pulse_verdict
  }, q.question), /*#__PURE__*/React.createElement("span", {
    className: `hf-q-verdict hf-q-verdict-${k}`,
    title: HF_TIP.pulse_verdict
  }, v)), /*#__PURE__*/React.createElement("p", {
    className: "hf-q-meta"
  }, /*#__PURE__*/React.createElement(HfConfidence, {
    c: q.confidence
  }), st.weeks && st.direction ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.pulse_streak
  }, " \xB7 ", st.word) : null, q.unusual && q.unusual.percentile != null ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.pulse_unusual
  }, " \xB7 level at the ", q.unusual.percentile, "th percentile of ", hfInt(q.unusual.n), " weeks") : null), q.persistence ? /*#__PURE__*/React.createElement("p", {
    className: "hf-q-persist",
    title: HF_TIP.pulse_persist
  }, ["2", "4", "8", "12"].map(w => {
    const p = q.persistence[w];
    return /*#__PURE__*/React.createElement("span", {
      key: w,
      className: "hf-persist-cell"
    }, w, "w: ", /*#__PURE__*/React.createElement("b", null, p ? `${p.same}/${p.of}` : "—"));
  })) : null, q.note ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, q.note) : null, q.conflicts && q.conflicts.length ? /*#__PURE__*/React.createElement("p", {
    className: "hf-conflict",
    title: HF_TIP.pulse_conflict
  }, "Disagreeing: ", q.conflicts.map(c => c.label).join("; ")) : null, q.missing && q.missing.length ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.pulse_missing
  }, "No answer from: ", q.missing.join("; ")) : null, q.context && q.context.length ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-context",
    title: HF_TIP.pulse_ofr
  }, q.context.map(c => /*#__PURE__*/React.createElement("li", {
    key: c.label
  }, c.label, ": ", /*#__PURE__*/React.createElement("b", null, c.value == null ? "—" : Math.abs(c.value) > 1e6 ? hfMoney(c.value) : Number(c.value).toFixed(2)), " ", /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "as of ", hfDate(c.as_of), " \u2014 ", c.note)))) : null, q.inputs && q.inputs.length ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: () => setOpen(!open),
    title: HF_TIP.pulse_input
  }, open ? "Hide" : "Show", " the ", q.inputs.length, " input", q.inputs.length === 1 ? "" : "s"), /*#__PURE__*/React.createElement(HfInputs, {
    inputs: q.inputs,
    open: open
  })) : null);
}
function HfSectorStrip({
  sec
}) {
  if (!sec || !sec.rows) return null;
  return /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.pulse_sector
  }, "Which sectors are being bought, and which sold"), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.pulse_sector_rank
  }, sec.ranked_by), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Sector"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_verdict
  }, "Verdict"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_sector_rank
  }, "Size of this week's move"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_streak
  }, "Streak"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_sector
  }, "Inputs"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_conf
  }, "Confidence"))), /*#__PURE__*/React.createElement("tbody", null, sec.rows.map(r => {
    const k = String(r.verdict || "").toLowerCase().replace(/[^a-z]+/g, "-");
    return /*#__PURE__*/React.createElement("tr", {
      key: r.sector
    }, /*#__PURE__*/React.createElement("td", {
      "data-label": "Sector"
    }, r.sector, !r.has_futures ? /*#__PURE__*/React.createElement("span", {
      className: "hf-muted",
      title: HF_TIP.pulse_nofutures
    }, " \xB7 flows only") : null), /*#__PURE__*/React.createElement("td", {
      "data-label": "Verdict"
    }, /*#__PURE__*/React.createElement("span", {
      className: `hf-q-verdict hf-q-verdict-${k}`
    }, r.verdict)), /*#__PURE__*/React.createElement("td", {
      "data-label": "Size of this week's move",
      title: HF_TIP.pulse_sector_rank
    }, r.move_size_text || "—"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Streak"
    }, r.streak && r.streak.weeks && r.streak.direction ? r.streak.word : "—"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Inputs",
      title: HF_TIP.pulse_sector
    }, r.inputs_available), /*#__PURE__*/React.createElement("td", {
      "data-label": "Confidence"
    }, (r.confidence || {}).level || "—"));
  })))), sec.no_futures && sec.no_futures.length ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.pulse_nofutures
  }, "No leveraged-fund futures contract exists for ", sec.no_futures.join(", "), " \u2014 those four are read from flows alone.") : null);
}
function HfCrowding({
  cr,
  onOpenTicker
}) {
  if (!cr) return null;
  return /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.pulse_crowd
  }, "Where trades are crowded, and where crowding is unwinding"), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.pulse_crowd
  }, cr.rule), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Market"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_crowd
  }, "State"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_unusual
  }, "Net"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_gross
  }, "Gross"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_crowd
  }, "One-sidedness"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.as_of
  }, "As of"))), /*#__PURE__*/React.createElement("tbody", null, cr.markets.map(r => {
    const k = String(r.state || "").toLowerCase().replace(/[^a-z]+/g, "-");
    return /*#__PURE__*/React.createElement("tr", {
      key: r.key
    }, /*#__PURE__*/React.createElement("td", {
      "data-label": "Market"
    }, r.market), /*#__PURE__*/React.createElement("td", {
      "data-label": "State"
    }, /*#__PURE__*/React.createElement("span", {
      className: `hf-crowd hf-crowd-${k}`
    }, r.state)), /*#__PURE__*/React.createElement("td", {
      "data-label": "Net"
    }, r.percentiles.net == null ? "—" : `${r.percentiles.net}th`), /*#__PURE__*/React.createElement("td", {
      "data-label": "Gross"
    }, r.percentiles.gross == null ? "—" : `${r.percentiles.gross}th`), /*#__PURE__*/React.createElement("td", {
      "data-label": "One-sidedness"
    }, r.percentiles.one_sided == null ? "—" : `${r.percentiles.one_sided}th`), /*#__PURE__*/React.createElement("td", {
      "data-label": "As of"
    }, hfDate(r.as_of)));
  })))), cr.names && cr.names.length ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("h5", {
    title: HF_TIP.pulse_crowd_names
  }, "Most crowded single-name shorts ", /*#__PURE__*/React.createElement(HfTag, {
    cls: "REGULATORY POSITIONING DATA"
  })), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Symbol"), /*#__PURE__*/React.createElement("th", null, "Name"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_dtc
  }, "Days to cover"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_si
  }, "Shares short"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_si
  }, "Change"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.as_of
  }, "Settled"))), /*#__PURE__*/React.createElement("tbody", null, cr.names.map(n => /*#__PURE__*/React.createElement("tr", {
    key: n.symbol
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Symbol"
  }, /*#__PURE__*/React.createElement("button", {
    className: "hf-sym",
    onClick: () => onOpenTicker && onOpenTicker(n.symbol),
    title: `Open ${n.symbol} on the Trade tab`
  }, n.symbol)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Name",
    className: "hf-issuer"
  }, n.name), /*#__PURE__*/React.createElement("td", {
    "data-label": "Days to cover",
    title: HF_TIP.pulse_dtc
  }, Number(n.days_to_cover).toFixed(1)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Shares short"
  }, hfInt(Math.round(n.short))), /*#__PURE__*/React.createElement("td", {
    "data-label": "Change",
    className: n.change_pct > 0 ? "up" : n.change_pct < 0 ? "down" : ""
  }, n.change_pct == null ? "—" : `${n.change_pct > 0 ? "+" : ""}${n.change_pct}%`), /*#__PURE__*/React.createElement("td", {
    "data-label": "Settled"
  }, hfDate(n.settlement)))))))) : null);
}
function PulsePanel({
  apiFetch,
  onOpenTicker
}) {
  const [d, setD] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const load = React.useCallback(async () => {
    setBusy(true);
    try {
      const {
        d: got,
        err: readErr
      } = await hfReadJson(await apiFetch("/api/hf/pulse", {
        noCache: true
      }));
      if (!got) throw new Error(readErr || "no data");
      setD(got);
      setErr(got.error || null);
    } catch (e) {
      setErr(String(e && e.message || e));
    } finally {
      setBusy(false);
    }
  }, [apiFetch]);
  React.useEffect(() => {
    load();
  }, [load]);
  React.useEffect(() => {
    if (!(d && d.refreshing)) return;
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [d && d.refreshing, load]);
  if (busy && !d) {
    return /*#__PURE__*/React.createElement("div", {
      className: "st-loading",
      "aria-busy": "true"
    }, /*#__PURE__*/React.createElement("div", {
      className: "skel skel-line",
      style: {
        width: "45%"
      }
    }), /*#__PURE__*/React.createElement("div", {
      className: "skel skel-line",
      style: {
        width: "90%"
      }
    }));
  }
  if (err && !d) {
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: "research-error"
    }, err), /*#__PURE__*/React.createElement("button", {
      className: "card-error-btn st-retry",
      onClick: load
    }, "Try again"));
  }
  if (d && !d.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "hf-pulse"
    }, /*#__PURE__*/React.createElement("p", {
      className: "hf-muted",
      title: HF_TIP.pulse
    }, d.note, d.refreshing ? " Reading now — this takes about a minute the first time." : ""), /*#__PURE__*/React.createElement("button", {
      className: "sl-mode",
      onClick: load,
      disabled: busy
    }, busy ? "Loading…" : "Check again"));
  }
  if (!d) return null;
  const dates = d.dates || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "hf-pulse",
    title: HF_TIP.pulse
  }, /*#__PURE__*/React.createElement("p", {
    className: "sl-status"
  }, /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.pulse_week
  }, "Week ", d.week), dates.cftc_as_of ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.pulse_cftc
  }, " \xB7 futures positions as of ", dates.cftc_as_of) : null, dates.short_interest ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.pulse_si
  }, " \xB7 short interest settled ", dates.short_interest) : null, dates.short_volume ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.pulse_shvol
  }, " \xB7 short volume ", dates.short_volume) : null, dates.ofr ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.pulse_ofr
  }, " \xB7 Form PF ", dates.ofr) : null, d.refreshing ? /*#__PURE__*/React.createElement("span", {
    className: "sl-live"
  }, " \xB7 reading") : null, /*#__PURE__*/React.createElement("span", null, " \xB7 pulse ", d.pulse_version || d.version || ""), " ", /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: load,
    disabled: busy,
    title: "Read the sources again"
  }, "reload")), d.changed ? /*#__PURE__*/React.createElement("section", {
    className: "hf-changed",
    title: HF_TIP.pulse_changed
  }, /*#__PURE__*/React.createElement("h4", null, "What changed since last week"), d.changed.available ? d.changed.n ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, d.changed.changes.map((c, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, /*#__PURE__*/React.createElement("b", null, c.what), ": ", c.from, " \u2192 ", /*#__PURE__*/React.createElement("b", null, c.to)))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "Nothing changed since ", d.changed.since, ". That is itself a finding.") : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, d.changed.note)) : null, /*#__PURE__*/React.createElement("div", {
    className: "hf-questions"
  }, (d.questions || []).map((q, i) => /*#__PURE__*/React.createElement(HfQuestion, {
    key: i,
    q: q
  }))), /*#__PURE__*/React.createElement(HfSectorStrip, {
    sec: d.sectors
  }), /*#__PURE__*/React.createElement(HfCrowding, {
    cr: d.crowding,
    onOpenTicker: onOpenTicker
  }), /*#__PURE__*/React.createElement(HfNames, {
    apiFetch: apiFetch,
    onOpenTicker: onOpenTicker
  }), d.unavailable && d.unavailable.length ? /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.pulse_missing
  }, "Sources that had nothing this week"), /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, d.unavailable.map((u, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, u)))) : null, d.history && d.history.length ? /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.pulse_history
  }, "Every week read so far"), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_week
  }, "Week"), /*#__PURE__*/React.createElement("th", null, "Exposure"), /*#__PURE__*/React.createElement("th", null, "Leverage"), /*#__PURE__*/React.createElement("th", null, "Longs"), /*#__PURE__*/React.createElement("th", null, "Shorts"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_crowd
  }, "Crowded"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_changed
  }, "Changes"))), /*#__PURE__*/React.createElement("tbody", null, d.history.map(h => /*#__PURE__*/React.createElement("tr", {
    key: h.week
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Week"
  }, h.week), /*#__PURE__*/React.createElement("td", {
    "data-label": "Exposure"
  }, (h.verdicts || {}).exposure || "—"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Leverage"
  }, (h.verdicts || {}).leverage || "—"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Longs"
  }, (h.verdicts || {}).longs || "—"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Shorts"
  }, (h.verdicts || {}).shorts || "—"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Crowded"
  }, (h.crowded || []).join(", ") || "—"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Changes"
  }, h.n_changes == null ? "—" : h.n_changes))))))) : null);
}
function HfPressQuotes({
  press
}) {
  const [open, setOpen] = React.useState(false);
  if (!press) return null;
  const quotes = press.quotes || [];
  const captured = press.captured || [];
  return /*#__PURE__*/React.createElement("section", {
    className: "hf-press",
    title: HF_TIP.press
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.press
  }, "What the banks were quoted saying"), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.press
  }, press.note), quotes.length ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.press_quote
  }, "Claim"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.report_conclusion
  }, "Question"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.press_outlet
  }, "Bank and outlet"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.press_period
  }, "Published"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.press_carried
  }, "Outlets carrying it"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.evidence
  }, "Evidence"))), /*#__PURE__*/React.createElement("tbody", null, quotes.map((q, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Claim",
    title: q.note || ""
  }, /*#__PURE__*/React.createElement("span", {
    className: q.direction > 0 ? "up" : "down"
  }, q.label), /*#__PURE__*/React.createElement("div", {
    className: "hf-muted"
  }, "\u201C", q.text, "\u201D")), /*#__PURE__*/React.createElement("td", {
    "data-label": "Question"
  }, q.about), /*#__PURE__*/React.createElement("td", {
    "data-label": "Bank and outlet",
    title: HF_TIP.press_outlet
  }, q.bank, /*#__PURE__*/React.createElement("div", {
    className: "hf-muted"
  }, q.outlet || "—", q.tier ? ` · ${q.tier}` : "")), /*#__PURE__*/React.createElement("td", {
    "data-label": "Published",
    title: HF_TIP.press_period
  }, hfDate(q.public_on)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Outlets carrying it",
    title: HF_TIP.press_carried
  }, q.carried_by || 1), /*#__PURE__*/React.createElement("td", {
    "data-label": "Evidence"
  }, /*#__PURE__*/React.createElement(HfTag, {
    cls: q.class || "PRIME BROKER AGGREGATE DATA"
  }))))))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.press_quote
  }, "No headline this week cleared every filter. That is common: most of what the feed returns is about returns rather than positions, cites no bank as the source, or describes a foreign market."), captured.length ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: () => setOpen(!open),
    title: HF_TIP.press_captured
  }, open ? "Hide" : "Show", " the ", captured.length, " headline", captured.length === 1 ? "" : "s", " read but not counted"), open ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes",
    title: HF_TIP.press_captured
  }, captured.filter(c => !c.eligible).map((c, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, c.title, /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, " \u2014 ", c.outlet || "unknown outlet", ", ", hfDate(c.public_on), " \xB7 not counted: ", (c.why_not || []).join("; "))))) : null) : null);
}
function HfReportConflicts({
  rows
}) {
  // Never collapsed by default: the brief asks for conflicting signals to be
  // visible without a click, because a hidden disagreement reads as agreement.
  return /*#__PURE__*/React.createElement("section", {
    className: "hf-conflicts",
    title: HF_TIP.report_conflicts
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_conflicts
  }, "Where the sources disagree"), rows && rows.length ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, rows.map((c, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, /*#__PURE__*/React.createElement("b", null, c.where), " ", /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "\xB7 ", c.kind), c.verdict ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, " \xB7 verdict reads ", c.verdict) : null, /*#__PURE__*/React.createElement("ul", null, (c.rows || []).map((r, j) => /*#__PURE__*/React.createElement("li", {
    key: j
  }, /*#__PURE__*/React.createElement("span", {
    className: r.direction > 0 ? "up" : r.direction < 0 ? "down" : ""
  }, r.label), r.class ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, " \xB7 ", r.class) : null, r.as_of ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, " \xB7 as of ", hfDate(r.as_of)) : null)))))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "Nothing disagreed this week. Every input that spoke pointed the same way as its verdict."));
}
function HfTrendTable({
  trends
}) {
  if (!trends || !trends.length) return null;
  return /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_trend
  }, "How long each answer has read this way"), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.report_conclusion
  }, "Question"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_verdict
  }, "This week"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.pulse_streak
  }, "Streak"), [2, 4, 8, 12].map(w => /*#__PURE__*/React.createElement("th", {
    key: w,
    title: HF_TIP.report_trend
  }, w, " weeks")))), /*#__PURE__*/React.createElement("tbody", null, trends.map(t => /*#__PURE__*/React.createElement("tr", {
    key: t.key
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Question"
  }, t.title), /*#__PURE__*/React.createElement("td", {
    "data-label": "This week"
  }, /*#__PURE__*/React.createElement("b", null, t.verdict)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Streak"
  }, t.streak || "—"), (t.windows || []).map(w => /*#__PURE__*/React.createElement("td", {
    key: w.weeks,
    "data-label": `${w.weeks} weeks`,
    title: HF_TIP.report_trend
  }, w.same == null ? "—" : `${w.same}/${w.of}`))))))));
}
function HfCompare({
  cmp
}) {
  if (!cmp || !cmp.ok) return null;
  return /*#__PURE__*/React.createElement("section", {
    className: "hf-compare",
    title: HF_TIP.report_compare
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_compare
  }, cmp.older.week, " compared with ", cmp.newer.week), /*#__PURE__*/React.createElement("p", {
    className: "sl-status"
  }, /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.as_of
  }, cmp.older.week, ": positions as of ", cmp.older.as_of_text || "—"), /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.as_of
  }, " \xB7 ", cmp.newer.week, ": positions as of ", cmp.newer.as_of_text || "—"), /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.report_compare
  }, " \xB7 ", cmp.n_same, " of 4 answers unchanged")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.report_conclusion
  }, "Question"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.report_week
  }, cmp.older.week), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.report_week
  }, cmp.newer.week), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.report_compare
  }, "Moved"))), /*#__PURE__*/React.createElement("tbody", null, (cmp.conclusions || []).map(c => /*#__PURE__*/React.createElement("tr", {
    key: c.key
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Question"
  }, c.title), /*#__PURE__*/React.createElement("td", {
    "data-label": cmp.older.week
  }, c.older.verdict || "—", c.older.confidence ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, " \xB7 ", c.older.confidence) : null), /*#__PURE__*/React.createElement("td", {
    "data-label": cmp.newer.week
  }, c.newer.verdict || "—", c.newer.confidence ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, " \xB7 ", c.newer.confidence) : null), /*#__PURE__*/React.createElement("td", {
    "data-label": "Moved"
  }, c.same ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "unchanged") : /*#__PURE__*/React.createElement("b", null, "changed"))))))), (cmp.sectors || []).some(s => !s.same) ? /*#__PURE__*/React.createElement("p", {
    className: "hf-notes",
    title: HF_TIP.pulse_sector
  }, "Sectors that moved: ", cmp.sectors.filter(s => !s.same).map(s => `${s.sector} ${s.older || "—"} → ${s.newer || "—"}`).join(" · ")) : null);
}

// The record keys the four weekly questions by a short word. Spelled out
// wherever a reader sees them, the way every other header on this board is.
const HF_QUESTION_NAME = {
  exposure: "Increasing or reducing exposure",
  leverage: "Increasing or reducing leverage",
  longs: "Reducing longs",
  shorts: "Adding shorts, or covering"
};
// "1 week(s)" is not English. Counts of weeks are spelled the way a
// person would say them.
const hfWeeks = n => `${hfInt(n)} ${Number(n) === 1 ? "week" : "weeks"}`;
const hfShare = v => v == null || !isFinite(v) ? "—" : `${(Number(v) * 100).toFixed(0)}%`;
const hfLift = v => v == null || !isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${(Number(v) * 100).toFixed(1)}%`;

// The long side of "which stocks are crowded". The short side lives in the
// crowding block above and is anonymous, because FINRA's short interest
// belongs to nobody. This one is attributable: every row is a named
// manager's own 13F, so the funds are named beside it.
function HfNames({
  apiFetch,
  onOpenTicker
}) {
  const [d, setD] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const load = React.useCallback(async () => {
    setBusy(true);
    try {
      const {
        d: got,
        err: readErr
      } = await hfReadJson(await apiFetch("/api/hf/names", {
        noCache: true
      }));
      if (!got) throw new Error(readErr || "no data");
      setD(got);
      setErr(null);
    } catch (e) {
      setErr(String(e && e.message || e));
    } finally {
      setBusy(false);
    }
  }, [apiFetch]);
  React.useEffect(() => {
    load();
  }, [load]);
  if (err) return /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.names
  }, "Which names the readable books agree on"), /*#__PURE__*/React.createElement("p", {
    className: "research-error"
  }, err));
  if (!d) return /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.names
  }, "Which names the readable books agree on"), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, busy ? "Reading the filings…" : "—"));
  const b = d.basis || {};
  const table = (rows, heading, tip) => !rows || !rows.length ? null : /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h5", {
    title: tip
  }, heading, " ", /*#__PURE__*/React.createElement(HfTag, {
    cls: "VERIFIED FUND ACTIVITY"
  })), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.names_name
  }, "Stock"), /*#__PURE__*/React.createElement("th", {
    title: tip
  }, "Managers"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.names_who
  }, "Which managers"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.names_period
  }, "Positions true as of"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.name
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Stock",
    title: HF_TIP.names_name
  }, r.symbol ? /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: () => onOpenTicker && onOpenTicker(r.symbol),
    title: `Open ${r.symbol}`
  }, r.symbol) : /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.names_unmapped
  }, r.name), r.n_calls ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted",
    title: HF_TIP.names_calls
  }, " \xB7 ", r.n_calls, " as call options") : null), /*#__PURE__*/React.createElement("td", {
    "data-label": "Managers",
    title: tip
  }, /*#__PURE__*/React.createElement("b", null, hfInt(r.n_managers)), " of ", hfInt(b.n_counted)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Which managers",
    title: HF_TIP.names_who
  }, (r.managers || []).join(", ")), /*#__PURE__*/React.createElement("td", {
    "data-label": "Positions true as of",
    title: HF_TIP.names_period
  }, r.as_of_first === r.as_of_last ? hfDate(r.as_of_last) : `${hfDate(r.as_of_first)} – ${hfDate(r.as_of_last)}`)))))));
  return /*#__PURE__*/React.createElement("section", {
    className: "hf-names"
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.names
  }, "Which names the readable books agree on"), /*#__PURE__*/React.createElement("p", {
    className: (d.headline || {}).available ? "" : "hf-muted",
    title: HF_TIP.names
  }, (d.headline || {}).text), /*#__PURE__*/React.createElement("p", {
    className: "sl-status"
  }, /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.names_counted
  }, hfInt(b.n_counted), " of ", hfInt(b.n_managers), " watched managers counted"), b.n_opaque ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.names_opaque
  }, " \xB7 ", hfInt(b.n_opaque), " left out as not readable") : null, b.n_not_read ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.names_unread
  }, " \xB7 ", hfInt(b.n_not_read), " not read yet") : null, " ", /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: load,
    disabled: busy,
    title: "Read the filings again"
  }, "reload")), table(d.held, "Held among the ten largest", HF_TIP.names_held), table(d.bought, "Added or newly bought last quarter", HF_TIP.names_bought), table(d.sold, "Trimmed or sold out of last quarter", HF_TIP.names_sold), d.puts && d.puts.length ? /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h5", {
    title: HF_TIP.names_puts
  }, "Held as put options \u2014 a bet against ", /*#__PURE__*/React.createElement(HfTag, {
    cls: "VERIFIED FUND ACTIVITY"
  })), /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, d.puts.map(r => /*#__PURE__*/React.createElement("li", {
    key: r.name,
    title: HF_TIP.names_puts
  }, /*#__PURE__*/React.createElement("b", null, r.name), " \u2014 ", (r.managers || []).join(", "))))) : null, b.n_opaque ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.names_opaque
  }, b.why_opaque, " Left out: ", (b.opaque || []).join(", "), ".") : null, d.unmapped && d.unmapped.n ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.names_unmapped
  }, hfInt(d.unmapped.n), " of ", hfInt(d.unmapped.of), " positions had no ticker in the map. ", d.unmapped.note) : null, /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.names
  }, d.note), /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes",
    title: HF_TIP.names
  }, (d.limitations || []).map((l, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, l))));
}
function HfGrades({
  apiFetch
}) {
  const [d, setD] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const load = React.useCallback(async () => {
    setBusy(true);
    try {
      const {
        d: got,
        err: readErr
      } = await hfReadJson(await apiFetch("/api/hf/grades", {
        noCache: true
      }));
      if (!got) throw new Error(readErr || "no data");
      setD(got);
      setErr(got.error || null);
    } catch (e) {
      setErr(String(e && e.message || e));
    } finally {
      setBusy(false);
    }
  }, [apiFetch]);
  React.useEffect(() => {
    load();
  }, [load]);
  React.useEffect(() => {
    if (!(d && d.refreshing)) return;
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [d && d.refreshing, load]);
  if (!d) {
    return /*#__PURE__*/React.createElement("section", {
      title: HF_TIP.grade
    }, /*#__PURE__*/React.createElement("h4", {
      title: HF_TIP.grade
    }, "Has any of this mattered yet?"), err ? /*#__PURE__*/React.createElement("p", {
      className: "research-error"
    }, err) : /*#__PURE__*/React.createElement("p", {
      className: "hf-muted"
    }, busy ? "Grading the record…" : "—"));
  }
  if (!d.available) {
    return /*#__PURE__*/React.createElement("section", {
      title: HF_TIP.grade
    }, /*#__PURE__*/React.createElement("h4", {
      title: HF_TIP.grade
    }, "Has any of this mattered yet?"), /*#__PURE__*/React.createElement("p", {
      className: "hf-muted"
    }, d.note, d.refreshing ? " Working on it now." : ""), /*#__PURE__*/React.createElement("button", {
      className: "sl-mode",
      onClick: load,
      disabled: busy
    }, busy ? "Loading…" : "Check again"));
  }
  const crowd = d.crowding || {};
  const eps = crowd.episodes || {};
  const head = d.headline || {};
  return /*#__PURE__*/React.createElement("section", {
    className: "hf-grade",
    title: HF_TIP.grade
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.grade
  }, "Has any of this mattered yet?"), /*#__PURE__*/React.createElement("p", {
    className: head.available ? "" : "hf-muted",
    title: HF_TIP.grade_reversal
  }, head.text), /*#__PURE__*/React.createElement("p", {
    className: "sl-status"
  }, /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.grade_source
  }, d.n_market_weeks, " market-weeks reconstructed"), /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.grade_episodes
  }, " \xB7 ", eps.total_weeks || 0, " crowded weeks in ", eps.total_episodes || 0, " episodes"), /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.grade_proxy
  }, " \xB7 ", d.n_proxies, " proxies priced"), /*#__PURE__*/React.createElement("span", null, " \xB7 grader ", d.version), " ", /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: load,
    disabled: busy,
    title: "Grade the record again"
  }, "reload")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_horizon
  }, "Weeks ahead"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_reversal
  }, "Crowded reversed"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_interval
  }, "95% interval"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_base
  }, "Ordinary week"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_lift
  }, "Difference"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_episodes
  }, "Weeks \xB7 episodes"))), /*#__PURE__*/React.createElement("tbody", null, (d.horizons || []).map(h => {
    const o = (crowd.overall || {})[String(h)] || {};
    const c = o.crowded,
      b = o.base;
    return /*#__PURE__*/React.createElement("tr", {
      key: h
    }, /*#__PURE__*/React.createElement("td", {
      "data-label": "Weeks ahead"
    }, h), /*#__PURE__*/React.createElement("td", {
      "data-label": "Crowded reversed",
      title: HF_TIP.grade_reversal
    }, c ? hfShare(c.share) : "—", c ? /*#__PURE__*/React.createElement("span", {
      className: "hf-muted"
    }, " (", c.k, " of ", c.n, ")") : null), /*#__PURE__*/React.createElement("td", {
      "data-label": "95% interval",
      title: HF_TIP.grade_interval
    }, c ? `${hfShare(c.low)} – ${hfShare(c.high)}` : "—"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Ordinary week",
      title: HF_TIP.grade_base
    }, b ? hfShare(b.share) : "—", b ? /*#__PURE__*/React.createElement("span", {
      className: "hf-muted"
    }, " (", hfInt(b.n), " weeks)") : null), /*#__PURE__*/React.createElement("td", {
      "data-label": "Difference",
      title: HF_TIP.grade_lift,
      className: o.lift > 0.02 ? "up" : o.lift < -0.02 ? "down" : ""
    }, hfLift(o.lift)), /*#__PURE__*/React.createElement("td", {
      "data-label": "Weeks \xB7 episodes",
      title: HF_TIP.grade_episodes
    }, c ? c.n : "—", " \xB7 ", o.episodes == null ? "—" : o.episodes, !o.enough ? /*#__PURE__*/React.createElement("span", {
      className: "hf-muted",
      title: HF_TIP.grade_interval
    }, " \xB7 too few") : null));
  })))), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.grade_episodes
  }, eps.note), Object.keys(crowd.not_graded || {}).length ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.grade_not_graded
  }, "Not graded: ", Object.entries(crowd.not_graded).map(([k, why]) => `${k} — ${why}`).join(" ")) : null, /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: () => setOpen(!open),
    title: HF_TIP.grade_proxy
  }, open ? "Hide" : "Show", " each market"), open ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Market"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_proxy
  }, "Priced on"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_episodes
  }, "Crowded episodes"), (d.horizons || []).map(h => /*#__PURE__*/React.createElement("th", {
    key: h,
    title: HF_TIP.grade_reversal
  }, h, "w reversed")))), /*#__PURE__*/React.createElement("tbody", null, Object.entries(crowd.markets || {}).map(([key, m]) => /*#__PURE__*/React.createElement("tr", {
    key: key
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Market"
  }, m.market), /*#__PURE__*/React.createElement("td", {
    "data-label": "Priced on"
  }, m.proxy), /*#__PURE__*/React.createElement("td", {
    "data-label": "Crowded episodes"
  }, m.episodes == null ? "—" : m.episodes), (d.horizons || []).map(h => {
    const c = ((m.horizons || {})[String(h)] || {}).crowded;
    return /*#__PURE__*/React.createElement("td", {
      key: h,
      "data-label": `${h}w reversed`
    }, c && c.n ? `${hfShare(c.share)}` : "—", c && c.n ? /*#__PURE__*/React.createElement("span", {
      className: "hf-muted"
    }, " (", c.n, ")") : null);
  })))))) : null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.grade_verdicts
  }, "What followed each weekly answer"), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.grade_verdicts
  }, (d.verdicts || {}).note), d.readings_from ? /*#__PURE__*/React.createElement("p", {
    className: "sl-status",
    title: HF_TIP.grade_replay
  }, /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.grade_recorded
  }, hfWeeks(d.readings_from.recorded), " the board recorded"), /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.grade_replayed
  }, " \xB7 ", hfWeeks(d.readings_from.replayed), " recomputed from data as it stood then"), d.readings_from.replay_version ? /*#__PURE__*/React.createElement("span", null, " \xB7 replay ", d.readings_from.replay_version) : null) : null, (d.replay_coverage || {}).by_question ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_verdicts
  }, "Question"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_replayed
  }, "Weeks recomputed"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_replay_span
  }, "Reaches back to"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.grade_replay_skipped
  }, "Why it stops there"))), /*#__PURE__*/React.createElement("tbody", null, Object.entries(d.replay_coverage.by_question).map(([q, c]) => /*#__PURE__*/React.createElement("tr", {
    key: q
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Question"
  }, HF_QUESTION_NAME[q] || q), /*#__PURE__*/React.createElement("td", {
    "data-label": "Weeks recomputed",
    title: HF_TIP.grade_replayed
  }, hfInt(c.n_weeks)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Reaches back to",
    title: HF_TIP.grade_replay_span
  }, c.first ? hfWeekLabel(c.first) : /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "not yet")), /*#__PURE__*/React.createElement("td", {
    "data-label": "Why it stops there",
    title: HF_TIP.grade_replay_skipped
  }, (c.why_skipped || []).length ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, c.why_skipped[0].reason, " (", hfWeeks(c.why_skipped[0].weeks), ")") : /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "nothing was missing"))))))) : null, (d.replay_coverage || {}).note ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.grade_replay
  }, d.replay_coverage.note) : null, (() => {
    const v = d.verdicts || {};
    const rows = [];
    Object.entries(v.by_question || {}).forEach(([q, byVerdict]) => {
      Object.entries(byVerdict || {}).forEach(([verdict, byH]) => rows.push({
        q,
        verdict,
        byH
      }));
    });
    if (!rows.length) {
      return /*#__PURE__*/React.createElement("p", {
        className: "hf-muted",
        title: HF_TIP.grade_verdicts
      }, "Nothing yet. The four weekly questions are graded from ", d.n_readings === 1 ? "one week" : `${d.n_readings} weeks`, " of record \u2014 the weeks the board stored, plus every past week it could recompute from the data as it stood then. A question is only recomputed when every input that decides it today was public that week, so some reach further back than others. The table below says how far each got.");
    }
    return /*#__PURE__*/React.createElement("div", {
      className: "scan-table-wrap hf-table-wrap"
    }, /*#__PURE__*/React.createElement("table", {
      className: "scan-table mtable hf-table"
    }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
      title: HF_TIP.grade_verdicts
    }, "Question"), /*#__PURE__*/React.createElement("th", {
      title: HF_TIP.pulse_verdict
    }, "Answer"), (d.horizons || []).map(h => /*#__PURE__*/React.createElement("th", {
      key: h,
      title: HF_TIP.grade_horizon
    }, h, "w median \xB7 weeks")))), /*#__PURE__*/React.createElement("tbody", null, rows.map((r, i) => /*#__PURE__*/React.createElement("tr", {
      key: i
    }, /*#__PURE__*/React.createElement("td", {
      "data-label": "Question"
    }, HF_QUESTION_NAME[r.q] || r.q), /*#__PURE__*/React.createElement("td", {
      "data-label": "Answer"
    }, /*#__PURE__*/React.createElement("b", null, r.verdict)), (d.horizons || []).map(h => {
      const s = (r.byH || {})[String(h)] || {};
      return /*#__PURE__*/React.createElement("td", {
        key: h,
        "data-label": `${h}w`,
        title: HF_TIP.grade_verdicts,
        className: s.median > 0 ? "up" : s.median < 0 ? "down" : ""
      }, s.median == null ? "—" : `${(s.median * 100).toFixed(1)}%`, /*#__PURE__*/React.createElement("span", {
        className: "hf-muted"
      }, " \xB7 ", s.n || 0), s.n && !s.enough ? /*#__PURE__*/React.createElement("span", {
        className: "hf-muted",
        title: HF_TIP.grade_interval
      }, " \xB7 too few") : null);
    }))), /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
      "data-label": "Question",
      title: HF_TIP.grade_base
    }, /*#__PURE__*/React.createElement("b", null, "Every week")), /*#__PURE__*/React.createElement("td", {
      "data-label": "Answer",
      className: "hf-muted"
    }, "base"), (d.horizons || []).map(h => {
      const s = (v.base || {})[String(h)] || {};
      return /*#__PURE__*/React.createElement("td", {
        key: h,
        "data-label": `${h}w`,
        title: HF_TIP.grade_base
      }, s.median == null ? "—" : `${(s.median * 100).toFixed(1)}%`, /*#__PURE__*/React.createElement("span", {
        className: "hf-muted"
      }, " \xB7 ", s.n || 0));
    })))));
  })(), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.grade_proxy
  }, "Measured on ", (d.verdicts || {}).proxy || "SPY", ", the broad-market proxy."), d.limitations && d.limitations.length ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes",
    title: HF_TIP.grade
  }, d.limitations.map((l, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, l))) : null);
}
function ReportPanel({
  apiFetch
}) {
  const [d, setD] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [week, setWeek] = React.useState("");
  const [cmp, setCmp] = React.useState(null);
  const [cmpA, setCmpA] = React.useState("");
  const [cmpB, setCmpB] = React.useState("");
  const load = React.useCallback(async wk => {
    setBusy(true);
    try {
      const q = wk ? `?week=${encodeURIComponent(wk)}` : "";
      const {
        d: got,
        err: readErr
      } = await hfReadJson(await apiFetch(`/api/hf/report${q}`, {
        noCache: true
      }));
      if (!got) throw new Error(readErr || "no data");
      setD(got);
      setErr(got.error || null);
    } catch (e) {
      setErr(String(e && e.message || e));
    } finally {
      setBusy(false);
    }
  }, [apiFetch]);
  React.useEffect(() => {
    load(week);
  }, [load, week]);
  React.useEffect(() => {
    if (!(d && d.refreshing)) return;
    const t = setInterval(() => load(week), 20000);
    return () => clearInterval(t);
  }, [d && d.refreshing, load, week]);
  const runCompare = React.useCallback(async () => {
    if (!cmpA || !cmpB) return;
    try {
      const {
        d: got
      } = await hfReadJson(await apiFetch(`/api/hf/report/compare?a=${encodeURIComponent(cmpA)}&b=${encodeURIComponent(cmpB)}`, {
        noCache: true
      }));
      setCmp(got || null);
    } catch (e) {
      setCmp({
        ok: false,
        error: String(e && e.message || e)
      });
    }
  }, [apiFetch, cmpA, cmpB]);
  if (busy && !d) {
    return /*#__PURE__*/React.createElement("div", {
      className: "st-loading",
      "aria-busy": "true"
    }, /*#__PURE__*/React.createElement("div", {
      className: "skel skel-line",
      style: {
        width: "45%"
      }
    }), /*#__PURE__*/React.createElement("div", {
      className: "skel skel-line",
      style: {
        width: "90%"
      }
    }));
  }
  if (err && !d) {
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: "research-error"
    }, err), /*#__PURE__*/React.createElement("button", {
      className: "card-error-btn st-retry",
      onClick: () => load(week)
    }, "Try again"));
  }
  if (d && !d.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "hf-report"
    }, /*#__PURE__*/React.createElement("p", {
      className: "hf-muted",
      title: HF_TIP.report
    }, d.note || d.error, d.refreshing ? " Assembling now — this takes about a minute the first time." : ""), /*#__PURE__*/React.createElement("button", {
      className: "sl-mode",
      onClick: () => load(week),
      disabled: busy
    }, busy ? "Loading…" : "Check again"));
  }
  if (!d) return null;
  const history = d.history || [];
  const weeks = history.map(h => h.week);
  return /*#__PURE__*/React.createElement("div", {
    className: "hf-report",
    title: HF_TIP.report
  }, /*#__PURE__*/React.createElement("p", {
    className: "sl-status"
  }, /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.report_week
  }, "Week ", d.week), d.dates && d.dates.as_of ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.pulse_cftc
  }, " \xB7 futures positions as of ", d.dates.as_of) : null, d.built_at ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.report_built
  }, " \xB7 assembled ", hfDateTime(d.built_at)) : null, d.revisions && d.revisions.length > 1 ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.report_revision
  }, " \xB7 revision ", d.revisions.length, " of this week") : null, d.refreshing ? /*#__PURE__*/React.createElement("span", {
    className: "sl-live"
  }, " \xB7 assembling") : null, /*#__PURE__*/React.createElement("span", null, " \xB7 report ", d.version || ""), " ", /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: () => load(week),
    disabled: busy,
    title: "Read it again"
  }, "reload"), week ? /*#__PURE__*/React.createElement("span", null, " \xB7 ", /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: () => setWeek("")
  }, "back to the current week")) : null), d.summary ? /*#__PURE__*/React.createElement("section", {
    className: "hf-summary",
    title: HF_TIP.report_summary
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_summary
  }, "The short version"), /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, (d.summary.bullets || []).map((b, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, b))), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, d.summary.note)) : null, d.changed ? /*#__PURE__*/React.createElement("section", {
    className: "hf-changed",
    title: HF_TIP.report_changed
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_changed
  }, "What changed since the last stored report"), d.changed.comparable ? d.changed.n ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, d.changed.changes.map((c, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, /*#__PURE__*/React.createElement("b", null, c.what), ": ", c.from, " \u2192 ", /*#__PURE__*/React.createElement("b", null, c.to), " ", /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "\xB7 ", c.kind)))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "Nothing changed since ", d.changed.since, ". That is itself a finding.") : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, d.changed.note)) : null, /*#__PURE__*/React.createElement("div", {
    className: "hf-questions"
  }, (d.conclusions || []).map(c => /*#__PURE__*/React.createElement(HfQuestion, {
    key: c.key,
    q: {
      ...c,
      question: c.title
    }
  }))), /*#__PURE__*/React.createElement(HfTrendTable, {
    trends: d.trends
  }), /*#__PURE__*/React.createElement(HfGrades, {
    apiFetch: apiFetch
  }), /*#__PURE__*/React.createElement(HfSectorStrip, {
    sec: d.sectors
  }), /*#__PURE__*/React.createElement(HfPressQuotes, {
    press: d.press
  }), /*#__PURE__*/React.createElement(HfReportConflicts, {
    rows: d.conflicts
  }), /*#__PURE__*/React.createElement("section", {
    title: HF_TIP.report_activity
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_activity
  }, "Named fund activity this week"), (d.funds || {}).sentence_filled_in && (d.funds || {}).n_acted ? /*#__PURE__*/React.createElement("p", {
    className: "hf-conflict",
    title: HF_TIP.report_filled_in
  }, d.funds.sentence, " ", /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "\xB7 written for this report on reading it")) : null, d.funds && d.funds.n_acted ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, d.funds.acted.map(f => /*#__PURE__*/React.createElement("li", {
    key: f.key
  }, /*#__PURE__*/React.createElement("b", null, f.name), " ", /*#__PURE__*/React.createElement(HfTag, {
    cls: f.class
  }), " \u2014 filed something describing a date after ", hfDate(f.since), (f.items || []).length ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, " \xB7 ", f.items.length, " filing", f.items.length === 1 ? "" : "s") : null))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted",
    title: HF_TIP.activity
  }, (d.funds || {}).sentence, " ", (d.funds || {}).note, (d.funds || {}).sentence_filled_in ? /*#__PURE__*/React.createElement("span", {
    className: "hf-muted",
    title: HF_TIP.report_filled_in
  }, " \xB7 written for this report on reading it") : null), d.funds ? /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.activity
  }, d.funds.n_unknown, " of ", d.funds.n_managers, " watched managers are in the ordinary UNKNOWN state"), d.funds.n_filed_since ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.report_carrying
  }, " \xB7 ", d.funds.n_filed_since, " carry a filing newer than their last holdings report") : null, d.funds.n_ceased ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.ceased
  }, " \xB7 ", d.funds.n_ceased, " ceased filing") : null, d.funds.n_not_read ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.refresh
  }, " \xB7 ", d.funds.n_not_read, " not read yet") : null) : null), d.new_filings ? /*#__PURE__*/React.createElement("section", {
    title: HF_TIP.report_filings
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_filings
  }, "Major new filings"), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, d.new_filings.note), d.new_filings.events.length + d.new_filings.amendments.length + d.new_filings.notices.length ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, [].concat(d.new_filings.events, d.new_filings.amendments, d.new_filings.notices).map((r, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, /*#__PURE__*/React.createElement("b", null, r.form), " \u2014 ", r.manager || r.company, " ", /*#__PURE__*/React.createElement("span", {
    className: "hf-muted"
  }, "\xB7 filed ", hfDate(r.filed))))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, "No watched manager filed a 13D, an amendment or a notice", d.new_filings.since_text ? ` since ${d.new_filings.since_text}` : "", ".")) : null, d.watchlist ? /*#__PURE__*/React.createElement("section", {
    title: HF_TIP.report_watchlist
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_watchlist
  }, "Watchlist"), d.watchlist.comparable ? d.watchlist.added.length + d.watchlist.removed.length + d.watchlist.status_changes.length ? /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, d.watchlist.added.map(m => /*#__PURE__*/React.createElement("li", {
    key: `a${m.key}`
  }, "Added: ", /*#__PURE__*/React.createElement("b", null, m.name || m.key))), d.watchlist.removed.map(m => /*#__PURE__*/React.createElement("li", {
    key: `r${m.key}`
  }, "Removed: ", /*#__PURE__*/React.createElement("b", null, m.name || m.key))), d.watchlist.status_changes.map(m => /*#__PURE__*/React.createElement("li", {
    key: `s${m.key}`
  }, /*#__PURE__*/React.createElement("b", null, m.name), ": ", m.from, " \u2192 ", /*#__PURE__*/React.createElement("b", null, m.to)))) : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, d.watchlist.n, " managers watched, unchanged since the last report.") : /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, d.watchlist.note, " ", d.watchlist.n, " managers are being watched.")) : null, d.unavailable && d.unavailable.length ? /*#__PURE__*/React.createElement("section", null, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.pulse_missing
  }, "Sources that had nothing this week"), /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, d.unavailable.map((u, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, u)))) : null, d.limitations && d.limitations.length ? /*#__PURE__*/React.createElement("section", {
    title: HF_TIP.report_limits
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_limits
  }, "What this report cannot tell you"), /*#__PURE__*/React.createElement("ul", {
    className: "hf-notes"
  }, d.limitations.map((l, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, l)))) : null, history.length ? /*#__PURE__*/React.createElement("section", {
    title: HF_TIP.report_history
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_history
  }, "Every report kept"), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap hf-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable hf-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.report_week
  }, "Week"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.as_of
  }, "Positions as of"), /*#__PURE__*/React.createElement("th", null, "Exposure"), /*#__PURE__*/React.createElement("th", null, "Leverage"), /*#__PURE__*/React.createElement("th", null, "Longs"), /*#__PURE__*/React.createElement("th", null, "Shorts"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.report_conflicts
  }, "Conflicts"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.press_quote
  }, "Bank quotes"), /*#__PURE__*/React.createElement("th", {
    title: HF_TIP.report_revision
  }, "Revisions"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, history.map(h => /*#__PURE__*/React.createElement("tr", {
    key: h.week
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Week"
  }, h.week), /*#__PURE__*/React.createElement("td", {
    "data-label": "Positions as of"
  }, h.as_of_text || hfDate(h.as_of)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Exposure"
  }, (h.verdicts || {}).exposure || "—"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Leverage"
  }, (h.verdicts || {}).leverage || "—"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Longs"
  }, (h.verdicts || {}).longs || "—"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Shorts"
  }, (h.verdicts || {}).shorts || "—"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Conflicts"
  }, h.n_conflicts == null ? "—" : h.n_conflicts), /*#__PURE__*/React.createElement("td", {
    "data-label": "Bank quotes"
  }, h.n_quotes == null ? "—" : h.n_quotes), /*#__PURE__*/React.createElement("td", {
    "data-label": "Revisions"
  }, h.n_revisions || 1), /*#__PURE__*/React.createElement("td", {
    "data-label": ""
  }, /*#__PURE__*/React.createElement("button", {
    className: "hf-link",
    onClick: () => setWeek(h.week),
    title: HF_TIP.report_history
  }, "read")))))))) : null, weeks.length > 1 ? /*#__PURE__*/React.createElement("section", {
    title: HF_TIP.report_compare
  }, /*#__PURE__*/React.createElement("h4", {
    title: HF_TIP.report_compare
  }, "Compare two weeks"), /*#__PURE__*/React.createElement("div", {
    className: "hf-compare-pick"
  }, /*#__PURE__*/React.createElement("label", {
    title: HF_TIP.report_compare
  }, "Earlier week", " ", /*#__PURE__*/React.createElement("select", {
    value: cmpA,
    onChange: e => setCmpA(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "choose a week"), weeks.map(w => /*#__PURE__*/React.createElement("option", {
    key: w,
    value: w
  }, w)))), /*#__PURE__*/React.createElement("label", {
    title: HF_TIP.report_compare
  }, "Later week", " ", /*#__PURE__*/React.createElement("select", {
    value: cmpB,
    onChange: e => setCmpB(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "choose a week"), weeks.map(w => /*#__PURE__*/React.createElement("option", {
    key: w,
    value: w
  }, w)))), /*#__PURE__*/React.createElement("button", {
    className: "sl-mode",
    onClick: runCompare,
    disabled: !cmpA || !cmpB,
    title: HF_TIP.report_compare
  }, "Compare")), cmp && !cmp.ok ? /*#__PURE__*/React.createElement("p", {
    className: "research-error"
  }, cmp.error) : null, /*#__PURE__*/React.createElement(HfCompare, {
    cmp: cmp
  })) : null);
}
const HF_SORTS = {
  public: {
    label: "Newest filing first",
    fn: (a, b) => String(b.last_public_on || "").localeCompare(String(a.last_public_on || ""))
  },
  changes: {
    label: "Most lines changed",
    fn: (a, b) => (b.n_new || 0) + (b.n_increased || 0) + (b.n_reduced || 0) + (b.n_exited || 0) - ((a.n_new || 0) + (a.n_increased || 0) + (a.n_reduced || 0) + (a.n_exited || 0))
  },
  name: {
    label: "Name",
    fn: (a, b) => String(a.name).localeCompare(String(b.name))
  },
  style: {
    label: "Style",
    fn: (a, b) => String(a.style || "").localeCompare(String(b.style || "")) || String(a.name).localeCompare(String(b.name))
  }
};
function HedgeTab({
  apiFetch,
  onOpenTicker
}) {
  const [view, setView] = React.useState("pulse");
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [open, setOpen] = React.useState({});
  const [detail, setDetail] = React.useState({});
  const [loadingKey, setLoadingKey] = React.useState({});
  const [sort, setSort] = React.useState("public");
  const [filter, setFilter] = React.useState("");
  const load = React.useCallback(async () => {
    if (view !== "funds") return; // the fund list is only fetched when it is on screen
    setBusy(true);
    try {
      const {
        d,
        err: readErr
      } = await hfReadJson(await apiFetch("/api/hf", {
        noCache: true
      }));
      if (!d) throw new Error(readErr || "no data");
      if (d.error && !d.managers) throw new Error(d.error);
      setData(d);
      setErr(null);
    } catch (e) {
      setErr(String(e && e.message || e));
    } finally {
      setBusy(false);
    }
  }, [apiFetch, view]);
  React.useEffect(() => {
    load();
  }, [load]);
  // A refresh runs in the background on the server; poll while it does.
  React.useEffect(() => {
    if (!(data && data.refreshing)) return;
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [data && data.refreshing, load]);
  const openFund = async key => {
    const next = !open[key];
    setOpen({
      ...open,
      [key]: next
    });
    if (next && !detail[key]) {
      setLoadingKey(s => ({
        ...s,
        [key]: true
      }));
      try {
        const {
          d,
          err
        } = await hfReadJson(await apiFetch(`/api/hf/fund?key=${encodeURIComponent(key)}`, {
          noCache: true
        }));
        setDetail(s => ({
          ...s,
          [key]: d || {
            ok: false,
            notes: [err || "no data"]
          }
        }));
      } catch (e) {
        setDetail(s => ({
          ...s,
          [key]: {
            ok: false,
            notes: [String(e && e.message || e)]
          }
        }));
      } finally {
        setLoadingKey(s => ({
          ...s,
          [key]: false
        }));
      }
    }
  };
  const refreshFund = async key => {
    setLoadingKey(s => ({
      ...s,
      [key]: true
    }));
    try {
      await apiFetch(`/api/hf/refresh?key=${encodeURIComponent(key)}`, {
        noCache: true
      });
      const {
        d
      } = await hfReadJson(await apiFetch(`/api/hf/fund?key=${encodeURIComponent(key)}`, {
        noCache: true
      }));
      if (d) setDetail(s => ({
        ...s,
        [key]: d
      }));
      load();
    } catch (e) {/* the card keeps what it had */} finally {
      setLoadingKey(s => ({
        ...s,
        [key]: false
      }));
    }
  };
  const managers = (data && data.managers || []).filter(m => !filter || `${m.name} ${m.style || ""} ${(m.people || []).join(" ")}`.toLowerCase().includes(filter.toLowerCase())).sort(HF_SORTS[sort].fn);
  return /*#__PURE__*/React.createElement("div", {
    className: "card hf-root",
    title: HF_TIP.card
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", {
    title: HF_TIP.card
  }, "Hedge Funds"), /*#__PURE__*/React.createElement("p", {
    className: "hf-muted"
  }, view === "pulse" ? "What the whole universe appears to be doing, from anonymous official data. Never about any one fund." : view === "report" ? "Both layers assembled into one document a week, kept forever, with every disagreement printed." : "What is verified about each manager, and when. Between filings: UNKNOWN.")), /*#__PURE__*/React.createElement("div", {
    className: "hf-controls"
  }, /*#__PURE__*/React.createElement("div", {
    className: "hf-views",
    title: HF_TIP.view
  }, /*#__PURE__*/React.createElement("button", {
    className: `sl-mode ${view === "pulse" ? "sl-mode-on" : ""}`,
    onClick: () => setView("pulse")
  }, "The Pulse"), /*#__PURE__*/React.createElement("button", {
    className: `sl-mode ${view === "funds" ? "sl-mode-on" : ""}`,
    onClick: () => setView("funds")
  }, "Named Funds"), /*#__PURE__*/React.createElement("button", {
    className: `sl-mode ${view === "report" ? "sl-mode-on" : ""}`,
    onClick: () => setView("report")
  }, "Weekly Report")), view === "funds" ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("input", {
    className: "hf-filter",
    placeholder: "Filter by name, style, person",
    value: filter,
    onChange: e => setFilter(e.target.value),
    title: "Narrow the cards"
  }), /*#__PURE__*/React.createElement("select", {
    value: sort,
    onChange: e => setSort(e.target.value),
    title: HF_TIP.sort
  }, Object.keys(HF_SORTS).map(k => /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, HF_SORTS[k].label))), /*#__PURE__*/React.createElement("button", {
    className: "sl-mode",
    onClick: load,
    disabled: busy,
    title: "Reload the board"
  }, busy ? "Loading…" : "Reload")) : null)), view === "pulse" ? /*#__PURE__*/React.createElement(PulsePanel, {
    apiFetch: apiFetch,
    onOpenTicker: onOpenTicker
  }) : null, view === "report" ? /*#__PURE__*/React.createElement(ReportPanel, {
    apiFetch: apiFetch
  }) : null, view === "funds" && data ? /*#__PURE__*/React.createElement("p", {
    className: "sl-status"
  }, /*#__PURE__*/React.createElement("span", {
    title: "When the server last finished reading every manager"
  }, data.as_of ? `Read ${hfDateTime(data.as_of)}` : "Not fully read yet"), data.refreshing ? /*#__PURE__*/React.createElement("span", {
    className: "sl-live"
  }, " \xB7 reading EDGAR") : null, /*#__PURE__*/React.createElement("span", null, " \xB7 ", data.n_read, " of ", data.n_managers, " managers read"), /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.cusips
  }, " \xB7 ", hfInt(data.cusips_mapped), " CUSIPs mapped"), data.last_sweep ? /*#__PURE__*/React.createElement("span", {
    title: HF_TIP.new_filings
  }, " \xB7 index swept ", hfDateTime(data.last_sweep)) : null, /*#__PURE__*/React.createElement("span", null, " \xB7 watch ", data.version)) : null, view === "funds" && err ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "research-error"
  }, err), /*#__PURE__*/React.createElement("button", {
    className: "card-error-btn st-retry",
    onClick: load
  }, "Try again")) : null, view === "funds" && busy && !data ? /*#__PURE__*/React.createElement("div", {
    className: "st-loading",
    "aria-busy": "true"
  }, /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "40%"
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "88%"
    }
  })) : null, view === "funds" && data ? /*#__PURE__*/React.createElement(HfBroader, {
    bt: data.broader_trend
  }) : null, view === "funds" && data ? /*#__PURE__*/React.createElement(HfNewFilings, {
    rows: data.new_filings
  }) : null, view === "funds" && data && data.errors && Object.keys(data.errors).length ? /*#__PURE__*/React.createElement("p", {
    className: "hf-conflict"
  }, "Could not read: ", Object.entries(data.errors).map(([k, v]) => `${k} (${v})`).join("; ")) : null, /*#__PURE__*/React.createElement("div", {
    className: "hf-grid"
  }, view !== "funds" ? null : managers.map(m => /*#__PURE__*/React.createElement(FundCard, {
    key: m.key,
    m: m,
    open: !!open[m.key],
    onToggle: () => openFund(m.key),
    detail: detail[m.key],
    loading: !!loadingKey[m.key],
    onOpenTicker: onOpenTicker,
    onRefresh: refreshFund
  }))), view === "funds" ? /*#__PURE__*/React.createElement(HfWatchlistEditor, {
    apiFetch: apiFetch,
    onSaved: load
  }) : null);
}
Object.assign(window, {
  HedgeTab: React.memo(HedgeTab)
});
})();
