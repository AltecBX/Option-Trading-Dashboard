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
  report_filled_in: "This report was stored before the card wrote this sentence, so it was composed just now from the counts the report does carry — using the wording that was true when it was written, which is not always today's wording. The stored file itself is untouched: a report is a record of a moment and is never rewritten.",
  report_carrying: "Managers whose most recent verified filing is newer than their last quarterly holdings report. That state lasts until the next quarterly report arrives, which can be months, so most of these managers did not file anything this week. It is counted here and kept out of the 'this week' list on purpose.",
  report_watchlist: "Who is being watched, and who moved on or off the list since the previous report. Without a previous report there is nothing to compare against, and the section says so rather than showing empty lists that look like 'no changes'.",
  report_conflicts: "Every disagreement in one place, never collapsed: inputs pointing opposite ways inside a question, sectors whose inputs split, and banks quoted this week saying opposite things. Disagreement is a finding — it is not averaged away.",
  report_changed: "What reads differently from the STORED report of the previous week. A diff against a record, not a memory. That is the whole reason every report is kept.",
  report_history: "Every week ever assembled, newest first. Pick one to read it, or compare two to watch positioning evolve.",
  report_compare: "Two stored weeks side by side. Verdicts that match are marked the same; the rest show what moved. Compare uses the same diff as 'what changed', so the two views can never disagree.",
  report_limits: "What this report cannot do, stated plainly, so a confident-looking verdict is never read as more than it is.",
  press: "PRIME BROKER AGGREGATE DATA. Goldman Sachs, Morgan Stanley and JPMorgan tell their prime brokerage clients each week what hedge funds did; the wires quote those notes. Secondhand by definition — a bank's summary of its own clients, retold by a reporter. It can raise confidence in what the measured data already says and can never create a verdict alone.",
  press_quote: "A quoted claim that survived every filter: the sentence names hedge funds, cites a bank as the SOURCE (not merely mentions one), is about positioning rather than returns, is not about a foreign market, and points unambiguously one way.",
  press_carried: "How many outlets carried this same claim. When five outlets repeat one Goldman note, that is one note — the claim counts once, and this is how widely it travelled.",
  press_period: "Prime-broker headlines say 'last week' or 'for a fourth consecutive week' rather than giving dates, so the period is not machine-readable and is never guessed. Only the publication date is claimed here.",
  press_captured: "Headlines that were read and NOT counted as evidence, with the reason. Shown because 'we saw this and did not use it' is worth as much as the list of what was used.",
  press_outlet: "Which outlet carried it. Only wire services and the banks' own publications count as evidence; anything else is captured and shown but raises no confidence.",
};

const hfDate = (s) => {
  if (!s) return "—";
  const d = new Date(String(s).slice(0, 10) + "T12:00:00");
  return isNaN(d) ? String(s) : d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
};
const hfDateTime = (s) => {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d)) return String(s);
  return `${d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })} at ${d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
};
const hfMoney = (v) => {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(a >= 1e10 ? 0 : 1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${Number(v).toFixed(0)}`;
};
const hfPct = (v, d = 1) => (v == null || !isFinite(v) ? "—" : `${Number(v).toFixed(d)}%`);
const hfInt = (v) => (v == null || !isFinite(v) ? "—" : Number(v).toLocaleString("en-US"));
const hfSigned = (v) => (v == null || !isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toLocaleString("en-US")}`);

// apiFetch hands back a Response. Read it as JSON, and when it is not JSON
// (the sign-in page after a session expires, a proxy error) say so in words
// instead of throwing "Unexpected token '<'".
async function hfReadJson(r) {
  let text = "";
  try { text = await r.text(); } catch (e) { return { d: null, err: String(e && e.message || e) }; }
  try { return { d: JSON.parse(text), err: null }; }
  catch (e) {
    return { d: null, err: r && r.status === 401
      ? "The app answered 401 — the API key the page holds is not the one the server expects."
      : "The app answered with a page instead of data — usually the sign-in screen after the session expires. Reload the page to sign back in." };
  }
}

function HfTag({ cls, sub, title }) {
  const short = { "VERIFIED FUND ACTIVITY": "VERIFIED", "PRIME BROKER AGGREGATE DATA": "PRIME BROKER",
                  "REGULATORY POSITIONING DATA": "REGULATORY", "INSTITUTIONAL FLOW PROXY": "FLOW PROXY",
                  "MODEL INFERENCE": "INFERENCE" }[cls] || cls;
  const k = (short || "").toLowerCase().replace(/[^a-z]+/g, "-");
  return <span className={`hf-tag hf-tag-${k}`} title={title || HF_TIP.evidence}>{short}{sub ? ` · ${sub}` : ""}</span>;
}

function HfActivity({ activity, size }) {
  const st = (activity && activity.state) || "NOT READ YET";
  const k = st.toLowerCase().replace(/[^a-z]+/g, "-");
  return (
    <div className={`hf-activity hf-activity-${k} ${size === "big" ? "hf-activity-big" : ""}`} title={HF_TIP.activity}>
      <span className="hf-activity-label">Current activity</span>
      <span className="hf-activity-state">{st}</span>
      {st === "UNKNOWN" && activity.days_since != null ? (
        <span className="hf-activity-sub" title={HF_TIP.unknown_days}>
          nothing verified for {activity.days_since} days
        </span>
      ) : null}
      {st === "FILED SINCE" && activity.items && activity.items.length ? (
        <span className="hf-activity-sub">{activity.items.length} event filing{activity.items.length === 1 ? "" : "s"} after {hfDate(activity.since)}</span>
      ) : null}
      {st === "CEASED" ? <span className="hf-activity-sub" title={HF_TIP.ceased}>no longer files with the SEC</span> : null}
    </div>
  );
}

function HfDates({ m }) {
  return (
    <dl className="hf-dates">
      <dt title={HF_TIP.as_of}>Positions true as of</dt>
      <dd title={HF_TIP.as_of}>{hfDate(m.last_as_of)}</dd>
      <dt title={HF_TIP.public_on}>Public on</dt>
      <dd title={HF_TIP.public_on}>{hfDate(m.last_public_on)}</dd>
      {m.amended_on ? (
        <React.Fragment>
          <dt title={HF_TIP.amended}>Amended on</dt>
          <dd title={HF_TIP.amended}>{hfDate(m.amended_on)}</dd>
        </React.Fragment>
      ) : null}
    </dl>
  );
}

function HfPositionsTable({ rows, kind, onOpenTicker, showDelta }) {
  if (!rows || !rows.length) return <p className="hf-muted">None.</p>;
  return (
    <div className="scan-table-wrap hf-table-wrap">
      <table className="scan-table mtable hf-table">
        <thead>
          <tr>
            <th title={HF_TIP.positions}>Name</th>
            <th title={HF_TIP.put_call}>Option</th>
            {showDelta ? <th title={HF_TIP.change}>Shares before</th> : null}
            <th title={HF_TIP.positions}>Shares now</th>
            {showDelta ? <th title={HF_TIP.change}>Change</th> : null}
            <th title={HF_TIP.value}>Value</th>
            <th title={HF_TIP.weight}>Weight</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${kind}|${r.cusip}|${r.put_call || ""}|${r.title || ""}|${i}`}>
              <td data-label="Name">
                {r.symbol ? (
                  <button className="hf-sym" title={`Open ${r.symbol} on the Trade tab`} onClick={() => onOpenTicker && onOpenTicker(r.symbol)}>{r.symbol}</button>
                ) : <span className="hf-sym hf-sym-none" title="No symbol could be matched to this CUSIP">—</span>}
                <span className="hf-issuer"> {r.issuer}</span>
              </td>
              <td data-label="Option" title={HF_TIP.put_call}>{r.put_call || ""}</td>
              {showDelta ? <td data-label="Shares before">{hfInt(r.shares_prev)}</td> : null}
              <td data-label="Shares now">{hfInt(r.shares_now != null ? r.shares_now : r.shares)}</td>
              {showDelta ? (
                <td data-label="Change" className={r.delta_shares > 0 ? "up" : r.delta_shares < 0 ? "down" : ""}>
                  {hfSigned(r.delta_shares)}{r.delta_pct != null && isFinite(r.delta_pct) ? ` (${r.delta_pct > 0 ? "+" : ""}${r.delta_pct.toFixed(0)}%)` : ""}
                </td>
              ) : null}
              <td data-label="Value">{hfMoney(r.value_now != null ? r.value_now : r.value)}</td>
              <td data-label="Weight">{hfPct(r.weight_now != null ? r.weight_now : r.weight)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HfBroader({ bt }) {
  return (
    <section className="hf-broader" title={HF_TIP.broader}>
      <h4>Broader hedge fund trend <span className="hf-muted">(not about any one fund)</span> <HfTag cls={(bt && bt.class) || "MODEL INFERENCE"} /></h4>
      {bt && bt.available ? (
        <React.Fragment>
          <p>{bt.text}</p>
          <p className="hf-muted" title={HF_TIP.pulse_dates}>
            {bt.as_of_text ? `Futures positions as of ${bt.as_of_text}` : null}
            {bt.week ? ` · week ${bt.week}` : null}
            {bt.confidence ? ` · confidence ${bt.confidence}` : null}
          </p>
          <p className="hf-muted">{bt.note}</p>
        </React.Fragment>
      ) : <p className="hf-muted">{(bt && bt.note) || "Not available."}</p>}
    </section>
  );
}

function FundDetail({ d, onOpenTicker }) {
  if (!d) return null;
  const opaque = d.turnover === "OPAQUE";
  const ch = d.change || {};
  const sec = d.sectors || {};
  const events = (d.events || []).filter((e) => e.kind === "EVENT");
  const passive = (d.events || []).filter((e) => e.kind !== "EVENT");
  return (
    <div className="hf-detail">
      {d.describe ? <p className="hf-describe" title={HF_TIP.turnover}>{d.describe}</p> : null}
      {d.edgar_name ? <p className="hf-muted" title={HF_TIP.edgar_name}>EDGAR: {d.edgar_name} · CIK {d.cik}</p> : null}

      {d.holdings ? (
        <section>
          <h4 title={HF_TIP.positions}>What the latest filing shows <HfTag cls="VERIFIED FUND ACTIVITY" sub="FILING" /></h4>
          <p className="hf-muted">
            {hfInt(d.holdings.n)} positions worth {hfMoney(d.holdings.value_total)}, true as of <b>{d.dates.last_as_of}</b>, public on <b>{d.dates.last_public_on}</b>
            {d.holdings.amended_on ? <React.Fragment>, amended on <b>{d.dates.amended_on}</b> ({(d.holdings.amendments || []).join(", ").toLowerCase()})</React.Fragment> : null}.
            {d.concentration ? (
              <React.Fragment>
                {" "}Top ten lines are <span title={HF_TIP.top10}>{hfPct(d.concentration.top10_weight)}</span> of the value; listed options are <span title={HF_TIP.options_share}>{hfPct(d.concentration.options_value_share)}</span>.
              </React.Fragment>
            ) : null}
          </p>
          {opaque ? <p className="hf-refusal" title={HF_TIP.opaque_refusal}>Read this as what was hedged on one day, not as a view on any stock or sector. This card will not summarise it as one.</p> : null}
          <h5 title={HF_TIP.top10}>Largest reported lines</h5>
          <HfPositionsTable rows={d.top} kind="top" onOpenTicker={onOpenTicker} />
        </section>
      ) : null}

      {d.change && !opaque ? (
        <section>
          <h4 title={HF_TIP.change}>Changes since {d.dates.prior_as_of || "the prior quarter"} <HfTag cls="VERIFIED FUND ACTIVITY" sub="FILING" /></h4>
          <p className="hf-muted">{ch.new.length} new · {ch.increased.length} increased · {ch.reduced.length} reduced · {ch.exited.length} exited · {ch.unchanged} unchanged</p>
          {ch.new.length ? <React.Fragment><h5 title={HF_TIP.new}>New</h5><HfPositionsTable rows={ch.new} kind="new" onOpenTicker={onOpenTicker} showDelta /></React.Fragment> : null}
          {ch.increased.length ? <React.Fragment><h5 title={HF_TIP.increased}>Increased</h5><HfPositionsTable rows={ch.increased} kind="inc" onOpenTicker={onOpenTicker} showDelta /></React.Fragment> : null}
          {ch.reduced.length ? <React.Fragment><h5 title={HF_TIP.reduced}>Reduced</h5><HfPositionsTable rows={ch.reduced} kind="red" onOpenTicker={onOpenTicker} showDelta /></React.Fragment> : null}
          {ch.exited.length ? <React.Fragment><h5 title={HF_TIP.exited}>Exited</h5><HfPositionsTable rows={ch.exited} kind="exit" onOpenTicker={onOpenTicker} showDelta /></React.Fragment> : null}
        </section>
      ) : null}
      {d.change && opaque ? (
        <section>
          <h4 title={HF_TIP.change}>Line count since {d.dates.prior_as_of || "the prior quarter"}</h4>
          <p className="hf-muted" title={HF_TIP.opaque_refusal}>{ch.new.length} new · {ch.increased.length} increased · {ch.reduced.length} reduced · {ch.exited.length} exited · {ch.unchanged} unchanged — counts only. For a book like this the lines are hedges that turn over in days; listing them as decisions would be fiction.</p>
        </section>
      ) : null}

      {sec.sectors ? (
        <section>
          <h4 title={HF_TIP.sectors}>Sector exposure of the reported table</h4>
          {sec.sectors.length ? (
            <ul className="hf-sectors">
              {sec.sectors.map((s) => (
                <li key={s.sector} title={HF_TIP.sectors}><b>{s.sector}</b> {hfPct(s.weight)} <span className="hf-muted">({s.n})</span></li>
              ))}
            </ul>
          ) : null}
          <p className="hf-muted" title={HF_TIP.unmapped}>Unmapped: {sec.unmapped ? `${sec.unmapped.n} positions, ${hfPct(sec.unmapped.weight)} of value` : "—"}</p>
        </section>
      ) : null}

      <section>
        <h4 title={HF_TIP.events}>Event filings (13D) in the last {d.event_window_days || 180} days <HfTag cls="VERIFIED FUND ACTIVITY" sub="FILING" /></h4>
        {events.length ? (
          <ul className="hf-events">
            {events.map((e) => (
              <li key={e.accession} title={HF_TIP.events}>
                <b>{e.form}</b> · {e.issuer || "issuer not stated"}{e.percent != null ? ` · ${hfPct(e.percent)} of class` : ""} · event {hfDate(e.as_of)} · public {hfDate(e.public_on)}
                {e.purpose ? <div className="hf-quote">{String(e.purpose).slice(0, 320)}{String(e.purpose).length > 320 ? "…" : ""}</div> : null}
              </li>
            ))}
          </ul>
        ) : <p className="hf-muted">None.</p>}
        {passive.length ? <p className="hf-muted" title={HF_TIP.passive}>{passive.length} passive notice{passive.length === 1 ? "" : "s"} (13G) in the window — not evidence of activity since the last 13F.</p> : null}
      </section>

      <section>
        <h4 title={HF_TIP.statements}>In their own words <HfTag cls="VERIFIED FUND ACTIVITY" sub="STATEMENT" /></h4>
        {d.statements && d.statements.length ? (
          <ul className="hf-statements">
            {d.statements.map((s) => (
              <li key={s.link || s.title} title={HF_TIP.statements}>
                <span className="hf-muted">{hfDateTime(s.published)} · {s.channel}</span>
                <div>{(d.people && d.people[0]) || d.name} wrote: <a href={s.link} target="_blank" rel="noopener noreferrer">{s.title}</a></div>
                {s.summary ? <div className="hf-quote">{s.summary}</div> : null}
              </li>
            ))}
          </ul>
        ) : <p className="hf-muted">No feed on file, or nothing in the window. A statement is a claim, not a position.</p>}
      </section>

      <section>
        <h4 title={HF_TIP.press}>Reported by the press <HfTag cls="VERIFIED FUND ACTIVITY" sub="PRESS" /></h4>
        {d.press && d.press.length ? (
          <ul className="hf-press">
            {d.press.map((p) => (
              <li key={p.link || p.title} title={HF_TIP.press}>
                <span className="hf-muted">{hfDateTime(p.published)}{p.outlet ? ` · ${p.outlet}` : ""}</span>
                <div><a href={p.link} target="_blank" rel="noopener noreferrer">{p.title}</a></div>
              </li>
            ))}
          </ul>
        ) : <p className="hf-muted">Nothing in the window.</p>}
      </section>

      <section>
        <h4 title={HF_TIP.filings}>Filing trail</h4>
        {d.filings && d.filings.length ? (
          <div className="scan-table-wrap hf-table-wrap">
            <table className="scan-table mtable hf-table">
              <thead><tr><th>Form</th><th title={HF_TIP.as_of}>Describes</th><th title={HF_TIP.public_on}>Public on</th><th title={HF_TIP.amended}>Amendment</th><th>Lines</th><th>Value</th></tr></thead>
              <tbody>
                {d.filings.map((f) => (
                  <tr key={f.accession}>
                    <td data-label="Form">{f.form}</td>
                    <td data-label="Describes">{hfDate(f.as_of)}</td>
                    <td data-label="Public on">{hfDate(f.public_on)}</td>
                    <td data-label="Amendment">{f.amendment_type || ""}</td>
                    <td data-label="Lines">{hfInt(f.entries)}</td>
                    <td data-label="Value">{hfMoney(f.value_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="hf-muted">None read.</p>}
      </section>

      {d.crosscheck ? (
        <section>
          <h4 title={HF_TIP.crosscheck}>Cross-check against Unusual Whales</h4>
          {d.crosscheck.available ? (
            <p className={d.crosscheck.agree === false ? "hf-conflict" : "hf-muted"} title={HF_TIP.crosscheck}>
              Largest positions in common: {hfInt(d.crosscheck.top_overlap)} of {hfInt(d.crosscheck.top_compared)} ·
              EDGAR {hfInt(d.crosscheck.n_edgar)} positions ({hfInt(d.crosscheck.n_edgar_lines)} lines) · Unusual Whales {hfInt(d.crosscheck.n_uw)} rows ·{" "}
              {d.crosscheck.agree === true ? "agree" : d.crosscheck.agree === false ? "CONFLICT — shown, not resolved" : "too few mapped names to compare"}
            </p>
          ) : <p className="hf-muted">Unusual Whales had no parse of this filing.</p>}
        </section>
      ) : null}

      {d.notes && d.notes.length ? (
        <section>
          <h4>Notes</h4>
          <ul className="hf-notes">{d.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </section>
      ) : null}

      <HfBroader bt={d.broader_trend} />
    </div>
  );
}

function FundCard({ m, open, onToggle, detail, loading, onOpenTicker, onRefresh }) {
  const changed = (m.n_new || 0) + (m.n_increased || 0) + (m.n_reduced || 0) + (m.n_exited || 0);
  return (
    <div className={`hf-card ${open ? "hf-card-open" : ""} hf-turnover-${(m.turnover || "").toLowerCase()}`}>
      <div className="hf-card-head" onClick={onToggle} role="button" tabIndex={0}
           onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}>
        <div className="hf-card-title">
          <h3>{m.name}</h3>
          <div className="hf-badges">
            <span className="hf-badge" title={HF_TIP.people}>{m.style || "—"}</span>
            <span className={`hf-badge hf-badge-${(m.turnover || "").toLowerCase()}`} title={HF_TIP.turnover}>{m.turnover || "—"}</span>
            <span className={`hf-badge hf-badge-status-${(m.status || "").toLowerCase()}`} title={HF_TIP.status}>{m.status || "—"}</span>
          </div>
        </div>
        <HfActivity activity={m.activity} />
      </div>
      <HfDates m={m} />
      {m.last_as_of ? (
        <p className="hf-facts">
          <span title={HF_TIP.positions}>{hfInt(m.n_positions)} positions</span> · <span title={HF_TIP.value}>{hfMoney(m.value_total)}</span> · <span title={HF_TIP.top10}>top 10 {hfPct(m.top10_weight)}</span> · <span title={HF_TIP.options_share}>options {hfPct(m.options_value_share)}</span>
          {m.turnover === "READABLE" ? <React.Fragment> · <span title={HF_TIP.change}>{changed} lines changed</span></React.Fragment> : null}
          {m.n_events ? <React.Fragment> · <span title={HF_TIP.events}>{m.n_events} 13D/13G</span></React.Fragment> : null}
          {m.n_statements ? <React.Fragment> · <span title={HF_TIP.statements}>{m.n_statements} statements</span></React.Fragment> : null}
        </p>
      ) : null}
      {m.turnover === "OPAQUE" && m.last_as_of ? <p className="hf-refusal hf-refusal-small" title={HF_TIP.opaque_refusal}>Hedged, fast-turnover book — not read as a view.</p> : null}
      {m.successor ? <p className="hf-muted" title={HF_TIP.successor}>Book moved to {m.successor.name} (CIK {m.successor.cik}) from {hfDate(m.successor.as_of)}; followed.</p> : null}
      {m.notes && m.notes.length > 0 && !open ? <p className="hf-muted hf-note-line">{m.notes[0]}</p> : null}
      <div className="hf-card-actions">
        <button className="sl-mode" onClick={onToggle} title="Show or hide the full record">{open ? "Hide detail" : "Show detail"}</button>
        {onRefresh ? <button className="sl-mode" onClick={() => onRefresh(m.key)} title={HF_TIP.refresh}>Re-read EDGAR</button> : null}
      </div>
      {open ? (loading ? <div className="st-loading" aria-busy="true"><div className="skel skel-line" style={{ width: "60%" }} /><div className="skel skel-line" style={{ width: "90%" }} /></div>
                       : <FundDetail d={detail} onOpenTicker={onOpenTicker} />) : null}
    </div>
  );
}

function HfNewFilings({ rows }) {
  return (
    <section className="hf-newfilings">
      <h4 title={HF_TIP.new_filings}>Filings by watched managers this week <HfTag cls="VERIFIED FUND ACTIVITY" sub="FILING" /></h4>
      {rows && rows.length ? (
        <div className="scan-table-wrap hf-table-wrap">
          <table className="scan-table mtable hf-table">
            <thead><tr><th>Public on</th><th>Manager</th><th>Form</th><th>Filed as</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.accession}|${r.cik}`}>
                  <td data-label="Public on">{hfDate(r.filed)}</td>
                  <td data-label="Manager">{r.manager}</td>
                  <td data-label="Form">{r.form}</td>
                  <td data-label="Filed as">{r.company}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="hf-muted">None found in the last week, or the daily index has not been read yet.</p>}
    </section>
  );
}

function HfWatchlistEditor({ apiFetch, onSaved }) {
  const [openEd, setOpenEd] = React.useState(false);
  const [reg, setReg] = React.useState(null);
  const [form, setForm] = React.useState({ name: "", cik: "", style: "", turnover: "READABLE" });
  const [msg, setMsg] = React.useState(null);
  const load = React.useCallback(async () => {
    try {
      const { d, err } = await hfReadJson(await apiFetch("/api/hf/watchlist"));
      if (d) setReg(d); else setMsg(err);
    } catch (e) { setMsg(String(e && e.message || e)); }
  }, [apiFetch]);
  React.useEffect(() => { if (openEd && !reg) load(); }, [openEd, reg, load]);
  const save = async (overlay) => {
    setMsg(null);
    try {
      const { d, err } = await hfReadJson(await apiFetch("/api/hf/watchlist", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(overlay) }));
      if (d && d.ok) { setMsg("Saved."); setReg(null); onSaved && onSaved(); }
      else setMsg("Not saved: " + (err || JSON.stringify((d && (d.problems || d.error)) || d)));
    } catch (e) { setMsg("Not saved: " + String(e && e.message || e)); }
  };
  const overlay = (reg && reg.overlay) || { managers: [], removed: [] };
  const add = () => {
    const cik = parseInt(form.cik, 10);
    if (!form.name || !cik) { setMsg("A name and a CIK are required."); return; }
    const key = form.name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    const entry = { key, name: form.name, style: form.style || "fund", turnover: form.turnover,
                    ciks: [{ cik, from: null }], feeds: [], news_query: form.name };
    save({ managers: [...(overlay.managers || []).filter((m) => m.key !== key), entry], removed: (overlay.removed || []).filter((k) => k !== key) });
  };
  const remove = (key) => save({ managers: (overlay.managers || []).filter((m) => m.key !== key),
                                 removed: [...new Set([...(overlay.removed || []), key])] });
  const restore = (key) => save({ managers: overlay.managers || [], removed: (overlay.removed || []).filter((k) => k !== key) });
  return (
    <section className="hf-editor">
      <button className="sl-mode" onClick={() => setOpenEd(!openEd)} title={HF_TIP.watchlist}>{openEd ? "Close watchlist editor" : "Edit watchlist"}</button>
      {openEd ? (
        <div className="hf-editor-body">
          <p className="hf-muted" title={HF_TIP.watchlist}>Add a manager by EDGAR CIK (find it at sec.gov/cgi-bin/browse-edgar). Remove one to hide it; removed managers can be restored.</p>
          <div className="hf-editor-row">
            <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} title="The manager's name as you want it shown" />
            <input placeholder="CIK" value={form.cik} onChange={(e) => setForm({ ...form, cik: e.target.value })} title="EDGAR Central Index Key — digits only" />
            <input placeholder="Style" value={form.style} onChange={(e) => setForm({ ...form, style: e.target.value })} title="activist, quant, macro… free text" />
            <select value={form.turnover} onChange={(e) => setForm({ ...form, turnover: e.target.value })} title={HF_TIP.turnover}>
              <option value="READABLE">READABLE</option><option value="OPAQUE">OPAQUE</option>
            </select>
            <button className="sl-mode" onClick={add}>Add</button>
          </div>
          {reg ? (
            <ul className="hf-editor-list">
              {(reg.registry.managers || []).map((m) => (
                <li key={m.key}><b>{m.name}</b> <span className="hf-muted">CIK {(m.ciks || []).map((c) => c.cik).join(" → ")} · {m.turnover}</span> <button className="hf-link" onClick={() => remove(m.key)}>remove</button></li>
              ))}
              {(overlay.removed || []).map((k) => (
                <li key={`removed-${k}`} className="hf-muted">{k} (removed) <button className="hf-link" onClick={() => restore(k)}>restore</button></li>
              ))}
            </ul>
          ) : null}
          {msg ? <p className="hf-muted">{msg}</p> : null}
        </div>
      ) : null}
    </section>
  );
}


// ══════════════════════════════════════════════════════════════════════════
// THE PULSE — the aggregate layer. Anonymous sources only; no fund is named
// anywhere in this half of the tab.
// ══════════════════════════════════════════════════════════════════════════

const HF_CONF_ORDER = { HIGH: 3, MODERATE: 2, LOW: 1, NONE: 0 };

function HfConfidence({ c }) {
  if (!c) return null;
  const k = String(c.level || "NONE").toLowerCase();
  return (
    <span className={`hf-conf hf-conf-${k}`} title={`${HF_TIP.pulse_conf}\n\n${c.why || ""}`}>
      confidence {c.level}
      {c.classes ? <span className="hf-muted"> · {c.classes} class{c.classes === 1 ? "" : "es"}</span> : null}
    </span>
  );
}

function HfInputs({ inputs, open }) {
  if (!open || !inputs || !inputs.length) return null;
  return (
    <div className="scan-table-wrap hf-table-wrap">
      <table className="scan-table mtable hf-table">
        <thead>
          <tr>
            <th title={HF_TIP.pulse_input}>Measure</th>
            <th title={HF_TIP.evidence}>Evidence</th>
            <th title={HF_TIP.pulse_input}>Weekly change</th>
            <th title={HF_TIP.pulse_unusual}>Level percentile</th>
            <th title={HF_TIP.as_of}>As of</th>
          </tr>
        </thead>
        <tbody>
          {inputs.map((i) => (
            <tr key={i.key}>
              <td data-label="Measure" title={i.note || ""}>
                {i.label}
                {i.weight < 1 ? <span className="hf-muted" title={HF_TIP.pulse_input}> · supporting</span> : null}
              </td>
              <td data-label="Evidence"><HfTag cls={i.class} /></td>
              <td data-label="Weekly change" className={i.direction > 0 ? "up" : i.direction < 0 ? "down" : ""}>
                {i.change == null ? "—" : hfSigned(Math.round(i.change))}
                {i.direction === 0 && i.change != null ? <span className="hf-muted" title="Inside this measure's own noise band"> (no direction)</span> : null}
              </td>
              <td data-label="Level percentile" title={HF_TIP.pulse_unusual}>
                {i.percentile == null ? "—" : `${i.percentile}th`}{i.n ? <span className="hf-muted"> of {hfInt(i.n)}</span> : null}
              </td>
              <td data-label="As of">{hfDate(i.as_of)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HfQuestion({ q }) {
  const [open, setOpen] = React.useState(false);
  if (!q) return null;
  const v = String(q.verdict || "NO DATA");
  const k = v.toLowerCase().replace(/[^a-z]+/g, "-");
  const st = q.streak || {};
  return (
    <div className={`hf-q hf-q-${k}`}>
      <div className="hf-q-head">
        <h4 title={HF_TIP.pulse_verdict}>{q.question}</h4>
        <span className={`hf-q-verdict hf-q-verdict-${k}`} title={HF_TIP.pulse_verdict}>{v}</span>
      </div>
      <p className="hf-q-meta">
        <HfConfidence c={q.confidence} />
        {st.weeks && st.direction ? <span title={HF_TIP.pulse_streak}> · {st.word}</span> : null}
        {q.unusual && q.unusual.percentile != null ? (
          <span title={HF_TIP.pulse_unusual}> · level at the {q.unusual.percentile}th percentile of {hfInt(q.unusual.n)} weeks</span>
        ) : null}
      </p>
      {q.persistence ? (
        <p className="hf-q-persist" title={HF_TIP.pulse_persist}>
          {["2", "4", "8", "12"].map((w) => {
            const p = q.persistence[w];
            return <span key={w} className="hf-persist-cell">{w}w: <b>{p ? `${p.same}/${p.of}` : "—"}</b></span>;
          })}
        </p>
      ) : null}
      {q.note ? <p className="hf-muted">{q.note}</p> : null}
      {q.conflicts && q.conflicts.length ? (
        <p className="hf-conflict" title={HF_TIP.pulse_conflict}>
          Disagreeing: {q.conflicts.map((c) => c.label).join("; ")}
        </p>
      ) : null}
      {q.missing && q.missing.length ? (
        <p className="hf-muted" title={HF_TIP.pulse_missing}>No answer from: {q.missing.join("; ")}</p>
      ) : null}
      {q.context && q.context.length ? (
        <ul className="hf-context" title={HF_TIP.pulse_ofr}>
          {q.context.map((c) => (
            <li key={c.label}>{c.label}: <b>{c.value == null ? "—" : (Math.abs(c.value) > 1e6 ? hfMoney(c.value) : Number(c.value).toFixed(2))}</b> <span className="hf-muted">as of {hfDate(c.as_of)} — {c.note}</span></li>
          ))}
        </ul>
      ) : null}
      {q.inputs && q.inputs.length ? (
        <React.Fragment>
          <button className="hf-link" onClick={() => setOpen(!open)} title={HF_TIP.pulse_input}>
            {open ? "Hide" : "Show"} the {q.inputs.length} input{q.inputs.length === 1 ? "" : "s"}
          </button>
          <HfInputs inputs={q.inputs} open={open} />
        </React.Fragment>
      ) : null}
    </div>
  );
}

function HfSectorStrip({ sec }) {
  if (!sec || !sec.rows) return null;
  return (
    <section>
      <h4 title={HF_TIP.pulse_sector}>Which sectors are being bought, and which sold</h4>
      <p className="hf-muted" title={HF_TIP.pulse_sector_rank}>{sec.ranked_by}</p>
      <div className="scan-table-wrap hf-table-wrap">
        <table className="scan-table mtable hf-table">
          <thead>
            <tr>
              <th>Sector</th><th title={HF_TIP.pulse_verdict}>Verdict</th>
              <th title={HF_TIP.pulse_sector_rank}>Size of this week's move</th>
              <th title={HF_TIP.pulse_streak}>Streak</th>
              <th title={HF_TIP.pulse_sector}>Inputs</th>
              <th title={HF_TIP.pulse_conf}>Confidence</th>
            </tr>
          </thead>
          <tbody>
            {sec.rows.map((r) => {
              const k = String(r.verdict || "").toLowerCase().replace(/[^a-z]+/g, "-");
              return (
                <tr key={r.sector}>
                  <td data-label="Sector">
                    {r.sector}
                    {!r.has_futures ? <span className="hf-muted" title={HF_TIP.pulse_nofutures}> · flows only</span> : null}
                  </td>
                  <td data-label="Verdict"><span className={`hf-q-verdict hf-q-verdict-${k}`}>{r.verdict}</span></td>
                  <td data-label="Size of this week's move" title={HF_TIP.pulse_sector_rank}>{r.move_size_text || "—"}</td>
                  <td data-label="Streak">{r.streak && r.streak.weeks && r.streak.direction ? r.streak.word : "—"}</td>
                  <td data-label="Inputs" title={HF_TIP.pulse_sector}>{r.inputs_available}</td>
                  <td data-label="Confidence">{(r.confidence || {}).level || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {sec.no_futures && sec.no_futures.length ? (
        <p className="hf-muted" title={HF_TIP.pulse_nofutures}>
          No leveraged-fund futures contract exists for {sec.no_futures.join(", ")} — those four are read from flows alone.
        </p>
      ) : null}
    </section>
  );
}

function HfCrowding({ cr, onOpenTicker }) {
  if (!cr) return null;
  return (
    <section>
      <h4 title={HF_TIP.pulse_crowd}>Where trades are crowded, and where crowding is unwinding</h4>
      <p className="hf-muted" title={HF_TIP.pulse_crowd}>{cr.rule}</p>
      <div className="scan-table-wrap hf-table-wrap">
        <table className="scan-table mtable hf-table">
          <thead>
            <tr>
              <th>Market</th><th title={HF_TIP.pulse_crowd}>State</th>
              <th title={HF_TIP.pulse_unusual}>Net</th><th title={HF_TIP.pulse_gross}>Gross</th>
              <th title={HF_TIP.pulse_crowd}>One-sidedness</th><th title={HF_TIP.as_of}>As of</th>
            </tr>
          </thead>
          <tbody>
            {cr.markets.map((r) => {
              const k = String(r.state || "").toLowerCase().replace(/[^a-z]+/g, "-");
              return (
                <tr key={r.key}>
                  <td data-label="Market">{r.market}</td>
                  <td data-label="State"><span className={`hf-crowd hf-crowd-${k}`}>{r.state}</span></td>
                  <td data-label="Net">{r.percentiles.net == null ? "—" : `${r.percentiles.net}th`}</td>
                  <td data-label="Gross">{r.percentiles.gross == null ? "—" : `${r.percentiles.gross}th`}</td>
                  <td data-label="One-sidedness">{r.percentiles.one_sided == null ? "—" : `${r.percentiles.one_sided}th`}</td>
                  <td data-label="As of">{hfDate(r.as_of)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {cr.names && cr.names.length ? (
        <React.Fragment>
          <h5 title={HF_TIP.pulse_crowd_names}>Most crowded single-name shorts <HfTag cls="REGULATORY POSITIONING DATA" /></h5>
          <div className="scan-table-wrap hf-table-wrap">
            <table className="scan-table mtable hf-table">
              <thead>
                <tr><th>Symbol</th><th>Name</th><th title={HF_TIP.pulse_dtc}>Days to cover</th>
                    <th title={HF_TIP.pulse_si}>Shares short</th><th title={HF_TIP.pulse_si}>Change</th>
                    <th title={HF_TIP.as_of}>Settled</th></tr>
              </thead>
              <tbody>
                {cr.names.map((n) => (
                  <tr key={n.symbol}>
                    <td data-label="Symbol">
                      <button className="hf-sym" onClick={() => onOpenTicker && onOpenTicker(n.symbol)} title={`Open ${n.symbol} on the Trade tab`}>{n.symbol}</button>
                    </td>
                    <td data-label="Name" className="hf-issuer">{n.name}</td>
                    <td data-label="Days to cover" title={HF_TIP.pulse_dtc}>{Number(n.days_to_cover).toFixed(1)}</td>
                    <td data-label="Shares short">{hfInt(Math.round(n.short))}</td>
                    <td data-label="Change" className={n.change_pct > 0 ? "up" : n.change_pct < 0 ? "down" : ""}>{n.change_pct == null ? "—" : `${n.change_pct > 0 ? "+" : ""}${n.change_pct}%`}</td>
                    <td data-label="Settled">{hfDate(n.settlement)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </React.Fragment>
      ) : null}
    </section>
  );
}

function PulsePanel({ apiFetch, onOpenTicker }) {
  const [d, setD] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setBusy(true);
    try {
      const { d: got, err: readErr } = await hfReadJson(await apiFetch("/api/hf/pulse", { noCache: true }));
      if (!got) throw new Error(readErr || "no data");
      setD(got); setErr(got.error || null);
    } catch (e) { setErr(String(e && e.message || e)); }
    finally { setBusy(false); }
  }, [apiFetch]);
  React.useEffect(() => { load(); }, [load]);
  React.useEffect(() => {
    if (!(d && d.refreshing)) return;
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [d && d.refreshing, load]);

  if (busy && !d) {
    return <div className="st-loading" aria-busy="true"><div className="skel skel-line" style={{ width: "45%" }} /><div className="skel skel-line" style={{ width: "90%" }} /></div>;
  }
  if (err && !d) {
    return (
      <React.Fragment>
        <div className="research-error">{err}</div>
        <button className="card-error-btn st-retry" onClick={load}>Try again</button>
      </React.Fragment>
    );
  }
  if (d && !d.available) {
    return (
      <div className="hf-pulse">
        <p className="hf-muted" title={HF_TIP.pulse}>{d.note}{d.refreshing ? " Reading now — this takes about a minute the first time." : ""}</p>
        <button className="sl-mode" onClick={load} disabled={busy}>{busy ? "Loading…" : "Check again"}</button>
      </div>
    );
  }
  if (!d) return null;
  const dates = d.dates || {};
  return (
    <div className="hf-pulse" title={HF_TIP.pulse}>
      <p className="sl-status">
        <span title={HF_TIP.pulse_week}>Week {d.week}</span>
        {dates.cftc_as_of ? <span title={HF_TIP.pulse_cftc}> · futures positions as of {dates.cftc_as_of}</span> : null}
        {dates.short_interest ? <span title={HF_TIP.pulse_si}> · short interest settled {dates.short_interest}</span> : null}
        {dates.short_volume ? <span title={HF_TIP.pulse_shvol}> · short volume {dates.short_volume}</span> : null}
        {dates.ofr ? <span title={HF_TIP.pulse_ofr}> · Form PF {dates.ofr}</span> : null}
        {d.refreshing ? <span className="sl-live"> · reading</span> : null}
        <span> · pulse {d.pulse_version || (d.version || "")}</span>
        {" "}<button className="hf-link" onClick={load} disabled={busy} title="Read the sources again">reload</button>
      </p>

      {d.changed ? (
        <section className="hf-changed" title={HF_TIP.pulse_changed}>
          <h4>What changed since last week</h4>
          {d.changed.available ? (
            d.changed.n ? (
              <ul className="hf-notes">
                {d.changed.changes.map((c, i) => (
                  <li key={i}><b>{c.what}</b>: {c.from} → <b>{c.to}</b></li>
                ))}
              </ul>
            ) : <p className="hf-muted">Nothing changed since {d.changed.since}. That is itself a finding.</p>
          ) : <p className="hf-muted">{d.changed.note}</p>}
        </section>
      ) : null}

      <div className="hf-questions">
        {(d.questions || []).map((q, i) => <HfQuestion key={i} q={q} />)}
      </div>

      <HfSectorStrip sec={d.sectors} />
      <HfCrowding cr={d.crowding} onOpenTicker={onOpenTicker} />

      {d.unavailable && d.unavailable.length ? (
        <section>
          <h4 title={HF_TIP.pulse_missing}>Sources that had nothing this week</h4>
          <ul className="hf-notes">{d.unavailable.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </section>
      ) : null}

      {d.history && d.history.length ? (
        <section>
          <h4 title={HF_TIP.pulse_history}>Every week read so far</h4>
          <div className="scan-table-wrap hf-table-wrap">
            <table className="scan-table mtable hf-table">
              <thead><tr><th title={HF_TIP.pulse_week}>Week</th><th>Exposure</th><th>Leverage</th><th>Longs</th><th>Shorts</th><th title={HF_TIP.pulse_crowd}>Crowded</th><th title={HF_TIP.pulse_changed}>Changes</th></tr></thead>
              <tbody>
                {d.history.map((h) => (
                  <tr key={h.week}>
                    <td data-label="Week">{h.week}</td>
                    <td data-label="Exposure">{(h.verdicts || {}).exposure || "—"}</td>
                    <td data-label="Leverage">{(h.verdicts || {}).leverage || "—"}</td>
                    <td data-label="Longs">{(h.verdicts || {}).longs || "—"}</td>
                    <td data-label="Shorts">{(h.verdicts || {}).shorts || "—"}</td>
                    <td data-label="Crowded">{(h.crowded || []).join(", ") || "—"}</td>
                    <td data-label="Changes">{h.n_changes == null ? "—" : h.n_changes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function HfPressQuotes({ press }) {
  const [open, setOpen] = React.useState(false);
  if (!press) return null;
  const quotes = press.quotes || [];
  const captured = press.captured || [];
  return (
    <section className="hf-press" title={HF_TIP.press}>
      <h4 title={HF_TIP.press}>What the banks were quoted saying</h4>
      <p className="hf-muted" title={HF_TIP.press}>{press.note}</p>
      {quotes.length ? (
        <div className="scan-table-wrap hf-table-wrap">
          <table className="scan-table mtable hf-table">
            <thead>
              <tr>
                <th title={HF_TIP.press_quote}>Claim</th>
                <th title={HF_TIP.report_conclusion}>Question</th>
                <th title={HF_TIP.press_outlet}>Bank and outlet</th>
                <th title={HF_TIP.press_period}>Published</th>
                <th title={HF_TIP.press_carried}>Outlets carrying it</th>
                <th title={HF_TIP.evidence}>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {quotes.map((q, i) => (
                <tr key={i}>
                  <td data-label="Claim" title={q.note || ""}>
                    <span className={q.direction > 0 ? "up" : "down"}>{q.label}</span>
                    <div className="hf-muted">“{q.text}”</div>
                  </td>
                  <td data-label="Question">{q.about}</td>
                  <td data-label="Bank and outlet" title={HF_TIP.press_outlet}>
                    {q.bank}
                    <div className="hf-muted">{q.outlet || "—"}{q.tier ? ` · ${q.tier}` : ""}</div>
                  </td>
                  <td data-label="Published" title={HF_TIP.press_period}>{hfDate(q.public_on)}</td>
                  <td data-label="Outlets carrying it" title={HF_TIP.press_carried}>{q.carried_by || 1}</td>
                  <td data-label="Evidence"><HfTag cls={q.class || "PRIME BROKER AGGREGATE DATA"} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="hf-muted" title={HF_TIP.press_quote}>
          No headline this week cleared every filter. That is common: most of what the feed returns is
          about returns rather than positions, cites no bank as the source, or describes a foreign market.
        </p>
      )}
      {captured.length ? (
        <React.Fragment>
          <button className="hf-link" onClick={() => setOpen(!open)} title={HF_TIP.press_captured}>
            {open ? "Hide" : "Show"} the {captured.length} headline{captured.length === 1 ? "" : "s"} read but not counted
          </button>
          {open ? (
            <ul className="hf-notes" title={HF_TIP.press_captured}>
              {captured.filter((c) => !c.eligible).map((c, i) => (
                <li key={i}>
                  {c.title}
                  <span className="hf-muted"> — {c.outlet || "unknown outlet"}, {hfDate(c.public_on)} · not counted: {(c.why_not || []).join("; ")}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </React.Fragment>
      ) : null}
    </section>
  );
}

function HfReportConflicts({ rows }) {
  // Never collapsed by default: the brief asks for conflicting signals to be
  // visible without a click, because a hidden disagreement reads as agreement.
  return (
    <section className="hf-conflicts" title={HF_TIP.report_conflicts}>
      <h4 title={HF_TIP.report_conflicts}>Where the sources disagree</h4>
      {rows && rows.length ? (
        <ul className="hf-notes">
          {rows.map((c, i) => (
            <li key={i}>
              <b>{c.where}</b> <span className="hf-muted">· {c.kind}</span>
              {c.verdict ? <span className="hf-muted"> · verdict reads {c.verdict}</span> : null}
              <ul>
                {(c.rows || []).map((r, j) => (
                  <li key={j}>
                    <span className={r.direction > 0 ? "up" : r.direction < 0 ? "down" : ""}>{r.label}</span>
                    {r.class ? <span className="hf-muted"> · {r.class}</span> : null}
                    {r.as_of ? <span className="hf-muted"> · as of {hfDate(r.as_of)}</span> : null}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      ) : (
        <p className="hf-muted">Nothing disagreed this week. Every input that spoke pointed the same way as its verdict.</p>
      )}
    </section>
  );
}

function HfTrendTable({ trends }) {
  if (!trends || !trends.length) return null;
  return (
    <section>
      <h4 title={HF_TIP.report_trend}>How long each answer has read this way</h4>
      <div className="scan-table-wrap hf-table-wrap">
        <table className="scan-table mtable hf-table">
          <thead>
            <tr>
              <th title={HF_TIP.report_conclusion}>Question</th>
              <th title={HF_TIP.pulse_verdict}>This week</th>
              <th title={HF_TIP.pulse_streak}>Streak</th>
              {[2, 4, 8, 12].map((w) => <th key={w} title={HF_TIP.report_trend}>{w} weeks</th>)}
            </tr>
          </thead>
          <tbody>
            {trends.map((t) => (
              <tr key={t.key}>
                <td data-label="Question">{t.title}</td>
                <td data-label="This week"><b>{t.verdict}</b></td>
                <td data-label="Streak">{t.streak || "—"}</td>
                {(t.windows || []).map((w) => (
                  <td key={w.weeks} data-label={`${w.weeks} weeks`} title={HF_TIP.report_trend}>
                    {w.same == null ? "—" : `${w.same}/${w.of}`}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function HfCompare({ cmp }) {
  if (!cmp || !cmp.ok) return null;
  return (
    <section className="hf-compare" title={HF_TIP.report_compare}>
      <h4 title={HF_TIP.report_compare}>
        {cmp.older.week} compared with {cmp.newer.week}
      </h4>
      <p className="sl-status">
        <span title={HF_TIP.as_of}>{cmp.older.week}: positions as of {cmp.older.as_of_text || "—"}</span>
        <span title={HF_TIP.as_of}> · {cmp.newer.week}: positions as of {cmp.newer.as_of_text || "—"}</span>
        <span title={HF_TIP.report_compare}> · {cmp.n_same} of 4 answers unchanged</span>
      </p>
      <div className="scan-table-wrap hf-table-wrap">
        <table className="scan-table mtable hf-table">
          <thead>
            <tr>
              <th title={HF_TIP.report_conclusion}>Question</th>
              <th title={HF_TIP.report_week}>{cmp.older.week}</th>
              <th title={HF_TIP.report_week}>{cmp.newer.week}</th>
              <th title={HF_TIP.report_compare}>Moved</th>
            </tr>
          </thead>
          <tbody>
            {(cmp.conclusions || []).map((c) => (
              <tr key={c.key}>
                <td data-label="Question">{c.title}</td>
                <td data-label={cmp.older.week}>
                  {c.older.verdict || "—"}
                  {c.older.confidence ? <span className="hf-muted"> · {c.older.confidence}</span> : null}
                </td>
                <td data-label={cmp.newer.week}>
                  {c.newer.verdict || "—"}
                  {c.newer.confidence ? <span className="hf-muted"> · {c.newer.confidence}</span> : null}
                </td>
                <td data-label="Moved">{c.same ? <span className="hf-muted">unchanged</span> : <b>changed</b>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(cmp.sectors || []).some((s) => !s.same) ? (
        <p className="hf-notes" title={HF_TIP.pulse_sector}>
          Sectors that moved: {cmp.sectors.filter((s) => !s.same).map((s) => `${s.sector} ${s.older || "—"} → ${s.newer || "—"}`).join(" · ")}
        </p>
      ) : null}
    </section>
  );
}

function ReportPanel({ apiFetch }) {
  const [d, setD] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [week, setWeek] = React.useState("");
  const [cmp, setCmp] = React.useState(null);
  const [cmpA, setCmpA] = React.useState("");
  const [cmpB, setCmpB] = React.useState("");

  const load = React.useCallback(async (wk) => {
    setBusy(true);
    try {
      const q = wk ? `?week=${encodeURIComponent(wk)}` : "";
      const { d: got, err: readErr } = await hfReadJson(await apiFetch(`/api/hf/report${q}`, { noCache: true }));
      if (!got) throw new Error(readErr || "no data");
      setD(got); setErr(got.error || null);
    } catch (e) { setErr(String(e && e.message || e)); }
    finally { setBusy(false); }
  }, [apiFetch]);
  React.useEffect(() => { load(week); }, [load, week]);
  React.useEffect(() => {
    if (!(d && d.refreshing)) return;
    const t = setInterval(() => load(week), 20000);
    return () => clearInterval(t);
  }, [d && d.refreshing, load, week]);

  const runCompare = React.useCallback(async () => {
    if (!cmpA || !cmpB) return;
    try {
      const { d: got } = await hfReadJson(await apiFetch(
        `/api/hf/report/compare?a=${encodeURIComponent(cmpA)}&b=${encodeURIComponent(cmpB)}`, { noCache: true }));
      setCmp(got || null);
    } catch (e) { setCmp({ ok: false, error: String(e && e.message || e) }); }
  }, [apiFetch, cmpA, cmpB]);

  if (busy && !d) {
    return <div className="st-loading" aria-busy="true"><div className="skel skel-line" style={{ width: "45%" }} /><div className="skel skel-line" style={{ width: "90%" }} /></div>;
  }
  if (err && !d) {
    return (
      <React.Fragment>
        <div className="research-error">{err}</div>
        <button className="card-error-btn st-retry" onClick={() => load(week)}>Try again</button>
      </React.Fragment>
    );
  }
  if (d && !d.available) {
    return (
      <div className="hf-report">
        <p className="hf-muted" title={HF_TIP.report}>{d.note || d.error}{d.refreshing ? " Assembling now — this takes about a minute the first time." : ""}</p>
        <button className="sl-mode" onClick={() => load(week)} disabled={busy}>{busy ? "Loading…" : "Check again"}</button>
      </div>
    );
  }
  if (!d) return null;
  const history = d.history || [];
  const weeks = history.map((h) => h.week);
  return (
    <div className="hf-report" title={HF_TIP.report}>
      <p className="sl-status">
        <span title={HF_TIP.report_week}>Week {d.week}</span>
        {d.dates && d.dates.as_of ? <span title={HF_TIP.pulse_cftc}> · futures positions as of {d.dates.as_of}</span> : null}
        {d.built_at ? <span title={HF_TIP.report_built}> · assembled {hfDateTime(d.built_at)}</span> : null}
        {d.revisions && d.revisions.length > 1 ? (
          <span title={HF_TIP.report_revision}> · revision {d.revisions.length} of this week</span>
        ) : null}
        {d.refreshing ? <span className="sl-live"> · assembling</span> : null}
        <span> · report {d.version || ""}</span>
        {" "}<button className="hf-link" onClick={() => load(week)} disabled={busy} title="Read it again">reload</button>
        {week ? <span> · <button className="hf-link" onClick={() => setWeek("")}>back to the current week</button></span> : null}
      </p>

      {d.summary ? (
        <section className="hf-summary" title={HF_TIP.report_summary}>
          <h4 title={HF_TIP.report_summary}>The short version</h4>
          <ul className="hf-notes">
            {(d.summary.bullets || []).map((b, i) => <li key={i}>{b}</li>)}
          </ul>
          <p className="hf-muted">{d.summary.note}</p>
        </section>
      ) : null}

      {d.changed ? (
        <section className="hf-changed" title={HF_TIP.report_changed}>
          <h4 title={HF_TIP.report_changed}>What changed since the last stored report</h4>
          {d.changed.comparable ? (
            d.changed.n ? (
              <ul className="hf-notes">
                {d.changed.changes.map((c, i) => (
                  <li key={i}><b>{c.what}</b>: {c.from} → <b>{c.to}</b> <span className="hf-muted">· {c.kind}</span></li>
                ))}
              </ul>
            ) : <p className="hf-muted">Nothing changed since {d.changed.since}. That is itself a finding.</p>
          ) : <p className="hf-muted">{d.changed.note}</p>}
        </section>
      ) : null}

      <div className="hf-questions">
        {(d.conclusions || []).map((c) => <HfQuestion key={c.key} q={{ ...c, question: c.title }} />)}
      </div>

      <HfTrendTable trends={d.trends} />
      <HfSectorStrip sec={d.sectors} />
      <HfPressQuotes press={d.press} />
      <HfReportConflicts rows={d.conflicts} />

      <section title={HF_TIP.report_activity}>
        <h4 title={HF_TIP.report_activity}>Named fund activity this week</h4>
        {d.funds && d.funds.n_acted ? (
          <ul className="hf-notes">
            {d.funds.acted.map((f) => (
              <li key={f.key}>
                <b>{f.name}</b> <HfTag cls={f.class} /> — filed something describing a date after {hfDate(f.since)}
                {(f.items || []).length ? <span className="hf-muted"> · {f.items.length} filing{f.items.length === 1 ? "" : "s"}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="hf-muted" title={HF_TIP.activity}>
            {/* Rendered from the report's own sentence, not re-derived here.
                Deriving it a second time said "no manager filed anything"
                while the first EDGAR sweep was still running, which the data
                did not support. */}
            {(d.funds || {}).sentence} {(d.funds || {}).note}
            {(d.funds || {}).sentence_filled_in ? (
              <span className="hf-muted" title={HF_TIP.report_filled_in}> · written for this report on reading it</span>
            ) : null}
          </p>
        )}
        {d.funds ? (
          <p className="hf-muted">
            <span title={HF_TIP.activity}>{d.funds.n_unknown} of {d.funds.n_managers} watched managers are in the ordinary UNKNOWN state</span>
            {d.funds.n_filed_since ? <span title={HF_TIP.report_carrying}> · {d.funds.n_filed_since} carry a filing newer than their last holdings report</span> : null}
            {d.funds.n_ceased ? <span title={HF_TIP.ceased}> · {d.funds.n_ceased} ceased filing</span> : null}
            {d.funds.n_not_read ? <span title={HF_TIP.refresh}> · {d.funds.n_not_read} not read yet</span> : null}
          </p>
        ) : null}
      </section>

      {d.new_filings ? (
        <section title={HF_TIP.report_filings}>
          <h4 title={HF_TIP.report_filings}>Major new filings</h4>
          <p className="hf-muted">{d.new_filings.note}</p>
          {(d.new_filings.events.length + d.new_filings.amendments.length + d.new_filings.notices.length) ? (
            <ul className="hf-notes">
              {[].concat(d.new_filings.events, d.new_filings.amendments, d.new_filings.notices).map((r, i) => (
                <li key={i}>
                  <b>{r.form}</b> — {r.manager || r.company} <span className="hf-muted">· filed {hfDate(r.filed)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="hf-muted">No watched manager filed a 13D, an amendment or a notice{d.new_filings.since_text ? ` since ${d.new_filings.since_text}` : ""}.</p>
          )}
        </section>
      ) : null}

      {d.watchlist ? (
        <section title={HF_TIP.report_watchlist}>
          <h4 title={HF_TIP.report_watchlist}>Watchlist</h4>
          {d.watchlist.comparable ? (
            (d.watchlist.added.length + d.watchlist.removed.length + d.watchlist.status_changes.length) ? (
              <ul className="hf-notes">
                {d.watchlist.added.map((m) => <li key={`a${m.key}`}>Added: <b>{m.name || m.key}</b></li>)}
                {d.watchlist.removed.map((m) => <li key={`r${m.key}`}>Removed: <b>{m.name || m.key}</b></li>)}
                {d.watchlist.status_changes.map((m) => <li key={`s${m.key}`}><b>{m.name}</b>: {m.from} → <b>{m.to}</b></li>)}
              </ul>
            ) : <p className="hf-muted">{d.watchlist.n} managers watched, unchanged since the last report.</p>
          ) : <p className="hf-muted">{d.watchlist.note} {d.watchlist.n} managers are being watched.</p>}
        </section>
      ) : null}

      {d.unavailable && d.unavailable.length ? (
        <section>
          <h4 title={HF_TIP.pulse_missing}>Sources that had nothing this week</h4>
          <ul className="hf-notes">{d.unavailable.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </section>
      ) : null}

      {d.limitations && d.limitations.length ? (
        <section title={HF_TIP.report_limits}>
          <h4 title={HF_TIP.report_limits}>What this report cannot tell you</h4>
          <ul className="hf-notes">{d.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </section>
      ) : null}

      {history.length ? (
        <section title={HF_TIP.report_history}>
          <h4 title={HF_TIP.report_history}>Every report kept</h4>
          <div className="scan-table-wrap hf-table-wrap">
            <table className="scan-table mtable hf-table">
              <thead>
                <tr>
                  <th title={HF_TIP.report_week}>Week</th>
                  <th title={HF_TIP.as_of}>Positions as of</th>
                  <th>Exposure</th><th>Leverage</th><th>Longs</th><th>Shorts</th>
                  <th title={HF_TIP.report_conflicts}>Conflicts</th>
                  <th title={HF_TIP.press_quote}>Bank quotes</th>
                  <th title={HF_TIP.report_revision}>Revisions</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.week}>
                    <td data-label="Week">{h.week}</td>
                    <td data-label="Positions as of">{h.as_of_text || hfDate(h.as_of)}</td>
                    <td data-label="Exposure">{(h.verdicts || {}).exposure || "—"}</td>
                    <td data-label="Leverage">{(h.verdicts || {}).leverage || "—"}</td>
                    <td data-label="Longs">{(h.verdicts || {}).longs || "—"}</td>
                    <td data-label="Shorts">{(h.verdicts || {}).shorts || "—"}</td>
                    <td data-label="Conflicts">{h.n_conflicts == null ? "—" : h.n_conflicts}</td>
                    <td data-label="Bank quotes">{h.n_quotes == null ? "—" : h.n_quotes}</td>
                    <td data-label="Revisions">{h.n_revisions || 1}</td>
                    <td data-label="">
                      <button className="hf-link" onClick={() => setWeek(h.week)} title={HF_TIP.report_history}>read</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {weeks.length > 1 ? (
        <section title={HF_TIP.report_compare}>
          <h4 title={HF_TIP.report_compare}>Compare two weeks</h4>
          <div className="hf-compare-pick">
            <label title={HF_TIP.report_compare}>
              Earlier week{" "}
              <select value={cmpA} onChange={(e) => setCmpA(e.target.value)}>
                <option value="">choose a week</option>
                {weeks.map((w) => <option key={w} value={w}>{w}</option>)}
              </select>
            </label>
            <label title={HF_TIP.report_compare}>
              Later week{" "}
              <select value={cmpB} onChange={(e) => setCmpB(e.target.value)}>
                <option value="">choose a week</option>
                {weeks.map((w) => <option key={w} value={w}>{w}</option>)}
              </select>
            </label>
            <button className="sl-mode" onClick={runCompare} disabled={!cmpA || !cmpB} title={HF_TIP.report_compare}>Compare</button>
          </div>
          {cmp && !cmp.ok ? <p className="research-error">{cmp.error}</p> : null}
          <HfCompare cmp={cmp} />
        </section>
      ) : null}
    </div>
  );
}

const HF_SORTS = {
  public: { label: "Newest filing first", fn: (a, b) => String(b.last_public_on || "").localeCompare(String(a.last_public_on || "")) },
  changes: { label: "Most lines changed", fn: (a, b) => ((b.n_new || 0) + (b.n_increased || 0) + (b.n_reduced || 0) + (b.n_exited || 0)) - ((a.n_new || 0) + (a.n_increased || 0) + (a.n_reduced || 0) + (a.n_exited || 0)) },
  name: { label: "Name", fn: (a, b) => String(a.name).localeCompare(String(b.name)) },
  style: { label: "Style", fn: (a, b) => String(a.style || "").localeCompare(String(b.style || "")) || String(a.name).localeCompare(String(b.name)) },
};

function HedgeTab({ apiFetch, onOpenTicker }) {
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
    if (view !== "funds") return;          // the fund list is only fetched when it is on screen
    setBusy(true);
    try {
      const { d, err: readErr } = await hfReadJson(await apiFetch("/api/hf", { noCache: true }));
      if (!d) throw new Error(readErr || "no data");
      if (d.error && !d.managers) throw new Error(d.error);
      setData(d); setErr(null);
    } catch (e) { setErr(String(e && e.message || e)); }
    finally { setBusy(false); }
  }, [apiFetch, view]);
  React.useEffect(() => { load(); }, [load]);
  // A refresh runs in the background on the server; poll while it does.
  React.useEffect(() => {
    if (!(data && data.refreshing)) return;
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [data && data.refreshing, load]);

  const openFund = async (key) => {
    const next = !open[key];
    setOpen({ ...open, [key]: next });
    if (next && !detail[key]) {
      setLoadingKey((s) => ({ ...s, [key]: true }));
      try {
        const { d, err } = await hfReadJson(await apiFetch(`/api/hf/fund?key=${encodeURIComponent(key)}`, { noCache: true }));
        setDetail((s) => ({ ...s, [key]: d || { ok: false, notes: [err || "no data"] } }));
      } catch (e) {
        setDetail((s) => ({ ...s, [key]: { ok: false, notes: [String(e && e.message || e)] } }));
      } finally { setLoadingKey((s) => ({ ...s, [key]: false })); }
    }
  };
  const refreshFund = async (key) => {
    setLoadingKey((s) => ({ ...s, [key]: true }));
    try {
      await apiFetch(`/api/hf/refresh?key=${encodeURIComponent(key)}`, { noCache: true });
      const { d } = await hfReadJson(await apiFetch(`/api/hf/fund?key=${encodeURIComponent(key)}`, { noCache: true }));
      if (d) setDetail((s) => ({ ...s, [key]: d }));
      load();
    } catch (e) { /* the card keeps what it had */ }
    finally { setLoadingKey((s) => ({ ...s, [key]: false })); }
  };

  const managers = ((data && data.managers) || [])
    .filter((m) => !filter || `${m.name} ${m.style || ""} ${(m.people || []).join(" ")}`.toLowerCase().includes(filter.toLowerCase()))
    .sort(HF_SORTS[sort].fn);

  return (
    <div className="card hf-root" title={HF_TIP.card}>
      <div className="card-head">
        <div>
          <h2 title={HF_TIP.card}>Hedge Funds</h2>
          <p className="hf-muted">
            {view === "pulse"
              ? "What the whole universe appears to be doing, from anonymous official data. Never about any one fund."
              : view === "report"
                ? "Both layers assembled into one document a week, kept forever, with every disagreement printed."
                : "What is verified about each manager, and when. Between filings: UNKNOWN."}
          </p>
        </div>
        <div className="hf-controls">
          <div className="hf-views" title={HF_TIP.view}>
            <button className={`sl-mode ${view === "pulse" ? "sl-mode-on" : ""}`} onClick={() => setView("pulse")}>The Pulse</button>
            <button className={`sl-mode ${view === "funds" ? "sl-mode-on" : ""}`} onClick={() => setView("funds")}>Named Funds</button>
            <button className={`sl-mode ${view === "report" ? "sl-mode-on" : ""}`} onClick={() => setView("report")}>Weekly Report</button>
          </div>
          {view === "funds" ? (
            <React.Fragment>
              <input className="hf-filter" placeholder="Filter by name, style, person" value={filter} onChange={(e) => setFilter(e.target.value)} title="Narrow the cards" />
              <select value={sort} onChange={(e) => setSort(e.target.value)} title={HF_TIP.sort}>
                {Object.keys(HF_SORTS).map((k) => <option key={k} value={k}>{HF_SORTS[k].label}</option>)}
              </select>
              <button className="sl-mode" onClick={load} disabled={busy} title="Reload the board">{busy ? "Loading…" : "Reload"}</button>
            </React.Fragment>
          ) : null}
        </div>
      </div>

      {view === "pulse" ? <PulsePanel apiFetch={apiFetch} onOpenTicker={onOpenTicker} /> : null}
      {view === "report" ? <ReportPanel apiFetch={apiFetch} /> : null}

      {view === "funds" && data ? (
        <p className="sl-status">
          <span title="When the server last finished reading every manager">{data.as_of ? `Read ${hfDateTime(data.as_of)}` : "Not fully read yet"}</span>
          {data.refreshing ? <span className="sl-live"> · reading EDGAR</span> : null}
          <span> · {data.n_read} of {data.n_managers} managers read</span>
          <span title={HF_TIP.cusips}> · {hfInt(data.cusips_mapped)} CUSIPs mapped</span>
          {data.last_sweep ? <span title={HF_TIP.new_filings}> · index swept {hfDateTime(data.last_sweep)}</span> : null}
          <span> · watch {data.version}</span>
        </p>
      ) : null}

      {view === "funds" && err ? (
        <React.Fragment>
          <div className="research-error">{err}</div>
          <button className="card-error-btn st-retry" onClick={load}>Try again</button>
        </React.Fragment>
      ) : null}
      {view === "funds" && busy && !data ? (
        <div className="st-loading" aria-busy="true">
          <div className="skel skel-line" style={{ width: "40%" }} />
          <div className="skel skel-line" style={{ width: "88%" }} />
        </div>
      ) : null}

      {view === "funds" && data ? <HfBroader bt={data.broader_trend} /> : null}
      {view === "funds" && data ? <HfNewFilings rows={data.new_filings} /> : null}

      {view === "funds" && data && data.errors && Object.keys(data.errors).length ? (
        <p className="hf-conflict">Could not read: {Object.entries(data.errors).map(([k, v]) => `${k} (${v})`).join("; ")}</p>
      ) : null}

      <div className="hf-grid">
        {view !== "funds" ? null : managers.map((m) => (
          <FundCard key={m.key} m={m} open={!!open[m.key]} onToggle={() => openFund(m.key)}
                    detail={detail[m.key]} loading={!!loadingKey[m.key]}
                    onOpenTicker={onOpenTicker} onRefresh={refreshFund} />
        ))}
      </div>

      {view === "funds" ? <HfWatchlistEditor apiFetch={apiFetch} onSaved={load} /> : null}
    </div>
  );
}

Object.assign(window, { HedgeTab: React.memo(HedgeTab) });
