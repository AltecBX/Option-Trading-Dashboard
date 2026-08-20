(function () {
// tab-gap.jsx — LAZY CHUNK, loaded on first Gap Scan open.
// Premarket Gap Fade & Rebound scanner: which gap ups historically fade,
// which gap downs historically rebound, with measured same-ticker history
// (sample sizes + Wilson intervals) behind every number. The board stays
// compact by design — evidence lives one click away in the detail view.
// Endpoints: GET /api/gap · /api/gap/scan · /api/gap/detail
// · /api/gap/events · /api/gap/backtest · /api/gap/config

const GAP_SIG_TONE = {
  "STRONG FADE": "up",
  "FADE": "up",
  "STRONG REBOUND": "up",
  "REBOUND": "up",
  "MIXED": "warn",
  "HOLD / CONTINUATION RISK": "down",
  "CONTINUATION LOWER RISK": "down",
  "NO DATA": "mut"
};
const gapPct = (v, d = 1) => v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
const gapNum = (v, d = 1) => v == null ? "—" : Number(v).toFixed(d);
// Dates render as "Oct 28, 2026" — house rule: Month Day, Year, never ISO.
const gapDate = s => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  });
};
// Historical rows also carry the weekday — "Apr 8, 2026 (Wed)" — because a
// Monday gap and a Friday gap are not the same animal.
const gapDateDow = s => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  if (Number.isNaN(d.getTime())) return String(s);
  return `${gapDate(s)} (${d.toLocaleDateString("en-US", {
    weekday: "short"
  })})`;
};
// Catalyst names are spelled out — no insider shorthand in the UI.
const GAP_CATALYST_LABEL = {
  EARNINGS: "Earnings",
  UPGRADE: "Upgrade",
  DOWNGRADE: "Downgrade",
  "ANALYST ACTION": "Analyst action",
  MACRO: "Macro event",
  OFFERING: "Offering",
  DILUTION: "Dilution",
  "FDA APPROVAL": "FDA approval",
  "FDA REJECTION": "FDA rejection",
  "TRIAL SUCCESS": "Trial hit",
  "TRIAL FAILURE": "Trial missed",
  BUYOUT: "Buyout",
  "MERGER DEAL": "Merger deal",
  "MERGER VOTE": "Merger vote",
  "DEAL CLOSED": "Deal closed",
  BANKRUPTCY: "Bankruptcy",
  "DELISTING NOTICE": "Delisting notice",
  RESTATEMENT: "Restatement",
  "AUDITOR CHANGE": "Auditor change",
  RESTRUCTURING: "Restructuring",
  IMPAIRMENT: "Write-down",
  "LEADERSHIP CHANGE": "Leadership change",
  "SHORT REPORT": "Short-seller report",
  "INDEX ADD": "Index add",
  "INDEX DROP": "Index drop",
  "GUIDANCE RAISED": "Guidance raised",
  "GUIDANCE CUT": "Guidance cut",
  "INSIDER BUYING": "Insider buying",
  "INSIDER SELLING": "Insider selling",
  "ACTIVIST STAKE": "Activist stake",
  BUYBACK: "Buyback",
  "REVERSE SPLIT": "Reverse split",
  "LATE FILING": "Late filing",
  UNTAGGED: "None tagged"
};
// An upgrade behind a gap up confirms the move; a downgrade behind one is a
// very different animal. Color says which way the analyst leaned. Selling
// stock is red on either side of the tape — new shares are new supply.
const GAP_CATALYST_TONE = {
  UPGRADE: "up",
  DOWNGRADE: "down",
  OFFERING: "down",
  DILUTION: "down",
  "FDA APPROVAL": "up",
  "FDA REJECTION": "down",
  "TRIAL SUCCESS": "up",
  "TRIAL FAILURE": "down",
  BUYOUT: "up",
  BANKRUPTCY: "down",
  "DELISTING NOTICE": "down",
  RESTATEMENT: "down",
  // amber: real, dated, and genuinely ambiguous for direction
  "MERGER DEAL": "warn",
  "MERGER VOTE": "warn",
  "DEAL CLOSED": "warn",
  "AUDITOR CHANGE": "warn",
  RESTRUCTURING: "warn",
  IMPAIRMENT: "warn",
  "LEADERSHIP CHANGE": "warn",
  "SHORT REPORT": "down",
  "GUIDANCE CUT": "down",
  "INDEX DROP": "down",
  "GUIDANCE RAISED": "up",
  "INDEX ADD": "up",
  // Insiders buying with their own money is the cleanest bullish tag here;
  // a discretionary sale is the mirror of it. An activist arriving and a
  // buyback are both bids. A reverse split and a late annual report are
  // not directional on their own — amber, and the warning does the talking.
  "INSIDER BUYING": "up",
  BUYBACK: "up",
  "ACTIVIST STAKE": "up",
  "INSIDER SELLING": "down",
  "LATE FILING": "down",
  "REVERSE SPLIT": "warn"
};
const gapCatalyst = k => GAP_CATALYST_LABEL[k] || k || "None tagged";
const GAP_CATALYST_TIP = {
  EARNINGS: "The company reported earnings today or last night. Earnings gaps are kept in their OWN statistical population and never mixed with ordinary gaps — the probabilities you see are measured only against this stock's other earnings gaps.",
  UPGRADE: "An analyst raised their rating on this stock today or yesterday. Hover the label for the firm and the grade change. Note this is context, not part of the statistics — only earnings days are separated into their own population.",
  DOWNGRADE: "An analyst cut their rating on this stock today or yesterday. A downgrade behind a gap UP is worth a hard look — the move is fighting the news. Context only: only earnings days are separated in the statistics.",
  "ANALYST ACTION": "An initiation or price-target change today or yesterday. Context only — it is not separated in the statistics the way earnings are.",
  MACRO: "A scheduled market-wide event (CPI, FOMC or the jobs report) lands today, so much of this move may be the whole market rather than the stock. Context only.",
  OFFERING: "This company filed with the SEC to SELL STOCK — today or last night. That is the most common reason a small stock gaps down hard premarket: new shares are new supply, and the deal usually prices below the market. The label says what was sold and how much, read out of the filing itself; click through to read it on EDGAR. Bond deals are not counted here — only stock, convertibles and private placements, because only those dilute you. Context only: it is not separated in the statistics the way earnings are.",
  DILUTION: "This company REGISTERED shares that it can sell later — a shelf registration, or a resale prospectus for shares somebody else already holds. It is not a sale today, it is permission to sell, which is why it is a softer signal than an Offering. Click through to read the filing on EDGAR. Context only, not separated in the statistics.",
  "FDA APPROVAL": "The company told the SEC — in an 8-K filed today or last night — that the FDA APPROVED something. The label quotes the filing's own sentence, so you can read exactly what was approved; click through for the filing itself. Note this is what the company disclosed, not an FDA feed: the FDA publishes approvals on a delay and never publishes rejections at all. Context only — it is not separated in the statistics the way earnings are.",
  "FDA REJECTION": "The company disclosed that the FDA did NOT approve — usually a Complete Response Letter, which means the FDA is asking for more before it will approve. It is not always fatal to the drug, but it is the reason the stock is gapping. The label quotes the filing's own sentence; click through to read it. Context only, not separated in the statistics.",
  "TRIAL SUCCESS": "The company reported that a clinical trial MET its main goal — the primary endpoint. For a small biotech this moves the stock harder than most approvals do, because it is the moment the drug stops being a maybe. Read from the company's own 8-K; the label quotes the sentence. Context only, not separated in the statistics.",
  "TRIAL FAILURE": "The company reported that a clinical trial DID NOT meet its primary endpoint. This is the single most brutal gap down in biotech — a failed readout can take out most of the company's value in one morning, and the stock often keeps going rather than bouncing. Read from the company's own 8-K. Context only, not separated in the statistics.",
  BUYOUT: "Somebody is buying this company. Either a tender offer is outstanding for the shares or the company signed a deal to be acquired. IMPORTANT: once a takeover price is fixed, the stock trades to that price — it stops moving on its own, so its gap history no longer describes it. Where the filing names the price, the label shows it.",
  "MERGER DEAL": "The company signed a merger agreement. Which side it is on could NOT be proven from the filing's own words, so it is not labeled a buyout — read the filing to see. A pending deal usually pins the price, which makes this stock's gap history much less comparable.",
  "MERGER VOTE": "Shareholders are being asked to vote on a merger (a DEFM14A/PREM14A proxy). The deal was announced earlier; this is a step toward closing, so it moves the stock less than the announcement did.",
  "DEAL CLOSED": "The company completed an acquisition or a disposition (8-K item 2.01). Note this can mean it BOUGHT something as easily as it was bought.",
  BANKRUPTCY: "The company filed for bankruptcy or went into receivership (8-K item 1.03). This is the most consequential tag in the list and it outranks everything else. Ordinary gap statistics have very little to say about a stock in bankruptcy.",
  "DELISTING NOTICE": "The exchange told the company it is not meeting a continued-listing rule (8-K item 3.01) — often the minimum price or market value. It usually starts a cure period rather than an immediate delisting, but it is a real gap-down catalyst for small caps.",
  RESTATEMENT: "The company said its previously issued financial statements can NO LONGER BE RELIED ON (8-K item 4.02). The numbers everyone was valuing the stock on were wrong. Rare and severe.",
  "AUDITOR CHANGE": "The company changed accountants (8-K item 4.01). Routine at times, a warning sign at others — the filing says whether the auditor resigned or was dismissed.",
  RESTRUCTURING: "The company recorded costs for an exit or disposal plan (8-K item 2.05) — layoffs, closing a site, discontinuing a product line.",
  IMPAIRMENT: "The company wrote down the value of an asset (8-K item 2.06). An accounting recognition that something it owns is worth less than the books said.",
  "LEADERSHIP CHANGE": "An officer or director change was filed (8-K item 5.02). This covers a CEO leaving AND a routine board appointment, so it is the weakest tag here — it only shows when nothing stronger explains the move.",
  "SHORT REPORT": "A short seller published a report against this stock — the tag names the firm and quotes the headline. NOTE THE EVIDENCE: this one comes from the NEWS FEED, not from a filing, because short sellers publish on their own websites and file nothing with anybody. Only named research firms (Hindenburg, Muddy Waters, Wolfpack, Kerrisdale and the like) or an explicit 'short-seller report' count — rising short INTEREST is not this. Click through to read the story.",
  "INDEX ADD": "This stock is being added to an index (S&P, Russell, Nasdaq-100). Index funds have to buy it, which is why it gaps — but the buying is mechanical and concentrated around the rebalance date, so it is a different animal from news. From the news feed rather than a filing: index changes are announced by S&P or FTSE Russell, not by the company.",
  "INDEX DROP": "This stock is being removed from an index. Index funds have to sell it. From the news feed rather than a filing — the index provider announces these, not the company.",
  "GUIDANCE RAISED": "The company raised its own forecast OUTSIDE a quarterly report — a preannouncement. Read from its 8-K; guidance inside a quarterly release is not counted here, because that day is an earnings gap and earnings already outranks this.",
  "GUIDANCE CUT": "The company cut, withdrew or suspended its own forecast OUTSIDE a quarterly report. A preannounced cut is one of the harder gaps down there is, because it usually means the quarter went wrong badly enough that they could not wait. Read from the company's 8-K.",
  "INSIDER BUYING": "One or more insiders BOUGHT this company's stock on the open market, with their own money, and the Form 4 landed today or last night. The label names who, their role, and the day's total across every insider who filed — four officers each buying separately on one morning only means something added up. Why this tag is trusted: across 200 consecutive Form 4s from nine of these tickers, only 9 were open-market purchases, and NOT ONE was made under a pre-scheduled 10b5-1 plan. An insider buy is always a decision. Grants, option exercises and shares withheld for tax are ignored entirely — that is compensation arriving, not a view. Small totals are dropped. Click through to read the Form 4.",
  "INSIDER SELLING": "An insider SOLD stock on the open market and — this is the whole point — did NOT do it under a pre-scheduled trading plan. Measured on this app's own tickers: 321 of 345 insider sales (93%) were made under Rule 10b5-1 plans, set up months in advance and non-discretionary by law, so they say nothing about what the seller thinks today. Every one of those is thrown away here. What is left is a person choosing to sell, which is worth seeing. Still the weaker half of the pair: insiders sell to buy houses and pay taxes, and they buy for one reason. Only sizeable sales are shown.",
  "ACTIVIST STAKE": "An investor crossed 5% of the company and filed a Schedule 13D — the form you use when you intend to INFLUENCE the company, as opposed to the passive 13G. That usually means a push for board seats, a sale of the company, or a strategy change, and the stock often reprices on the filing alone. Free from the form type; no document read needed. Note that only a FIRST 13D is tagged: the amendments behind it are that same holder adding, trimming or leaving, and the form alone cannot say which.",
  BUYBACK: "The board authorized a NEW share repurchase program — the company bidding for its own stock. The label quotes the filing's own sentence, with the size where it is stated. Only new authorizations count: a filing that sets up a trading plan to execute a program approved months ago, or reports how much is left on one, is deliberately not tagged, because neither is news.",
  "REVERSE SPLIT": "The company is doing a reverse stock split — the label shows the ratio, read from its own filing. READ THE WARNING ABOVE: this is the one catalyst that changes the numbers rather than the company. A 1-for-10 multiplies the quoted price by ten overnight with no trading involved, so the gap percentages below were measured on a price scale that no longer exists. Common in small caps trying to hold a $1 minimum listing price, which is why it often arrives next to a delisting notice or an offering.",
  "LATE FILING": "The company told the SEC it cannot file its annual or quarterly report on time (Form NT 10-K or NT 10-Q). Sometimes it is a genuine administrative delay; often it is the first public sign of an accounting problem, an auditor disagreement or a going-concern fight — and a restatement or a delisting notice can follow. Free from the form type. It outranks an offering here, because a company that cannot produce its own financials explains a gap better than a share sale does.",
  UNTAGGED: "No earnings, FDA decision, deal, offering filing, insider trade, rating change, macro event or catalyst headline was found for this stock. That does NOT mean nothing happened — it means none of the sources this app actually has (earnings calendar, SEC EDGAR filings, analyst feeds, macro schedule) show one. Check the news feed in the detail view."
};
// Past gap days carry the same tags, but the wording has to be past tense —
// and honest that history is tagged from the filing TYPE, without opening
// each document the way this morning's tag does.
const GAP_EVENT_CAT_TIP = {
  EARNINGS: "Earnings day — counted only against other earnings gaps, never mixed with ordinary ones.",
  OFFERING: "The company filed with the SEC to sell stock on this date or the session before (a priced offering, a private placement, or an at-the-market program). Read from the filing type on EDGAR.",
  DILUTION: "The company registered shares for possible future sale on this date or the session before — a shelf or resale registration on EDGAR. Permission to sell, not a sale.",
  "FDA APPROVAL": "On this session the company filed an 8-K announcing an FDA approval. Read from the filing's own words; filings that merely recap an older decision are not counted.",
  "FDA REJECTION": "On this session the company filed an 8-K disclosing that the FDA did not approve — typically a Complete Response Letter. Read from the filing's own words.",
  "TRIAL SUCCESS": "A clinical trial met its primary endpoint on this session, per the company's own filing.",
  "TRIAL FAILURE": "A clinical trial missed its primary endpoint on this session, per the company's own filing.",
  BUYOUT: "A takeover of this company was announced or a tender offer was outstanding on this session. Prices around a live deal are pinned to the deal, so this day is a poor analog for an ordinary gap.",
  "MERGER DEAL": "A merger agreement was filed on this session; which side the company was on was not provable from the filing's own words.",
  "MERGER VOTE": "A merger proxy was filed on this session — a step toward closing a deal announced earlier.",
  "DEAL CLOSED": "The company completed an acquisition or disposition on this session (8-K item 2.01).",
  BANKRUPTCY: "The company filed for bankruptcy or receivership on this session (8-K item 1.03).",
  "DELISTING NOTICE": "The exchange flagged a continued-listing failure on this session (8-K item 3.01).",
  RESTATEMENT: "The company said prior financial statements could no longer be relied on (8-K item 4.02).",
  "AUDITOR CHANGE": "The company changed accountants on this session (8-K item 4.01).",
  RESTRUCTURING: "Exit or disposal costs were recorded on this session (8-K item 2.05).",
  IMPAIRMENT: "A material asset write-down was recorded on this session (8-K item 2.06).",
  "LEADERSHIP CHANGE": "An officer or director change was filed on this session (8-K item 5.02) — routine appointments included.",
  "ACTIVIST STAKE": "An investor filed a Schedule 13D on this session — a 5%-plus stake taken with the intent to influence the company. Read from the form type on EDGAR.",
  "LATE FILING": "The company notified the SEC on this session that a periodic report would be late (Form NT 10-K or NT 10-Q).",
  "INSIDER BUYING": "Insiders bought stock on the open market on this session, per their own Form 4s.",
  "INSIDER SELLING": "An insider sold stock on this session outside any pre-scheduled trading plan, per their own Form 4.",
  BUYBACK: "The board authorized a new share repurchase program on this session, per the company's own filing.",
  "REVERSE SPLIT": "A reverse stock split was filed on this session. Prices before and after are on different scales, which makes this day a poor analog."
};
const gapTime = s => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit"
  });
};
const gapWhen = s => {
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return String(s);
  return `${d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  })} ` + d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit"
  });
};

// "82% ·44" — a probability NEVER renders without its sample size.
function GapProb({
  r
}) {
  if (!r || r.p == null) return /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014");
  return /*#__PURE__*/React.createElement("span", {
    title: `${r.k} of ${r.n} events · conservative (Wilson) range ${r.lo}–${r.hi}%`
  }, Math.round(r.p), "%", /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7", r.n));
}
function GapSigPill({
  signal,
  held
}) {
  const tone = GAP_SIG_TONE[signal] || "mut";
  return /*#__PURE__*/React.createElement("span", {
    className: `gap-sig gap-sig-${tone}`,
    title: held ? "signal held by hysteresis — the raw signal differs but hasn't persisted long enough to flip the display" : undefined
  }, signal || "—", held ? " ·" : "");
}
function GapQualityDot({
  q
}) {
  const cls = q === "HIGH" ? "hi" : q === "MODERATE" ? "mid" : "lo";
  return /*#__PURE__*/React.createElement("i", {
    className: `gap-qdot gap-qdot-${cls}`,
    title: `Analog quality: ${q || "unknown"} — how similar the historical examples are to today's setup`
  });
}

// ── detail view (§27: enough evidence, no analytics dashboard) ──────────────

// Every historical event, sortable — one click to see the biggest gaps, the
// worst squeezes, or every earnings day grouped together.
// Column names follow TODAY's setup, in the words a trader uses: on a fade
// you care about the max fade down and the max squeeze up; on a rebound,
// the max rebound up and the max flush down. A ticker's history can contain
// gaps in BOTH directions, so each row's arrow follows that row's own
// direction — an opposite-direction row is visibly different rather than
// silently mislabeled by the header.
const GAP_MOVE_LABELS = {
  up: {
    fav: "Max fade ↓",
    adv: "Max squeeze ↑",
    favWord: "faded",
    advWord: "squeezed"
  },
  down: {
    fav: "Max rebound ↑",
    adv: "Max flush ↓",
    favWord: "rebounded",
    advWord: "flushed"
  }
};
function GapAnalogTable({
  events,
  direction
}) {
  const L = GAP_MOVE_LABELS[direction === "down" ? "down" : "up"];
  const [k, setK] = useState("date");
  const [dir, setDir] = useState(1);
  const sorted = useMemo(() => {
    const key = e => {
      switch (k) {
        case "gap":
          return -Math.abs(e.official_gap_pct ?? 0);
        case "pmmax":
          return -Math.abs(e.pm_gap_max_pct ?? 0);
        case "dir":
          return e.direction || "";
        case "via":
          return (e.qualified_by || []).join("+");
        case "cat":
          return e.catalyst_kind || "";
        case "fav":
          return -(e.fav_pct ?? -99);
        case "adv":
          return -(e.adv_pct ?? -99);
        case "basis":
          return e.exclusion || e.basis || "";
        default:
          return e.date || "";
      }
    };
    const s = [...(events || [])];
    s.sort((a, b) => {
      const ka = key(a),
        kb = key(b);
      return (ka < kb ? 1 : ka > kb ? -1 : 0) * (k === "date" ? dir : -dir);
    });
    return s;
  }, [events, k, dir]);
  // sort marker is ▾/▴ so it can't be mistaken for the ↓/↑ that carry
  // meaning in the Max fade / Max squeeze column names
  const th = (label, key_, tip, cls) => /*#__PURE__*/React.createElement("th", {
    className: cls,
    title: tip,
    onClick: () => {
      if (k === key_) setDir(-dir);else {
        setK(key_);
        setDir(1);
      }
    }
  }, label, k === key_ ? dir === 1 ? " ▾" : " ▴" : "");
  return /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table gap-ev-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, th("Date", "date", "The historical session this gap happened on, with its weekday — click to sort oldest/newest."), th("Direction", "dir", "Green ▲ = the stock gapped UP that morning (a fade candidate). Red ▼ = it gapped DOWN (a rebound candidate). Click to group the two together."), th("Open gap", "gap", "How far the official 9:30 opening price was from the prior day's close. Click to sort by the biggest gaps.", "scan-th-num"), th("Premarket peak", "pmmax", "The largest gap reached during that day's premarket session, where minute data exists. A dash means we have the daily bars but not that morning's premarket tape.", "scan-th-num"), th("Qualified by", "via", "Why this day is in the database. OFFICIAL = the opening gap alone cleared the threshold. PREMARKET = the stock reached the threshold before the open (even if it opened small — those faded-before-the-open days are exactly what this scanner studies). OFFICIAL+PREMARKET = both."), th("Catalyst", "cat", "What is known to have been behind that day's gap, from dated records only. Earnings = the company reported that day or the night before — those days are kept in a separate statistical population and never mixed with ordinary gaps. FDA approval / FDA rejection = the company filed an 8-K that day announcing the FDA's decision. Offering / Dilution = the company filed with the SEC to sell or register stock on that date (SEC EDGAR). A dash means no record was found, not that nothing happened. Click to group the same catalyst together."), th(L.fav, "fav", direction === "down" ? "How far the stock rallied off the open — the rebound you were trying to catch. Rows marked ▲ Up are gap-up days, where this column is the fade instead; the arrow on each value tells you which." : "How far the stock dropped from the open — the fade you were trying to catch. Rows marked ▼ Down are gap-down days, where this column is the rebound instead; the arrow on each value tells you which.", "scan-th-num"), th(L.adv, "adv", direction === "down" ? "How far it flushed BELOW the open first — the pain you had to sit through before any bounce. Click to sort by the worst flushes." : "How far it squeezed ABOVE the open first — the pain you had to sit through before any fade. Click to sort by the worst squeezes.", "scan-th-num"), th("Data basis", "basis", "MINUTE PATH = we have that day minute by minute, so target-vs-stop ordering is measured. DAILY ONLY = we know how far it moved but not in what order. An EXCLUDE_ label means the day was thrown out (split, dividend or unreliable data) and contributes to nothing."))), /*#__PURE__*/React.createElement("tbody", null, sorted.map((e, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    className: e.exclusion ? "gap-row-excl" : ""
  }, /*#__PURE__*/React.createElement("td", {
    title: e.exclusion ? `Excluded: ${e.exclusion}` : e.delayed_open ? "This session opened late (halt or delayed first print)." : ""
  }, gapDateDow(e.date), e.delayed_open ? " *" : ""), /*#__PURE__*/React.createElement("td", {
    className: e.direction === "up" ? "gap-dir-up" : "gap-dir-down",
    title: e.direction === "up" ? "Gapped up — fade candidate" : "Gapped down — rebound candidate"
  }, e.direction === "up" ? "▲ Up" : "▼ Down"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: "Official open vs prior close"
  }, gapPct(e.official_gap_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: e.pm_gap_max_pct == null ? "No premarket minute data stored for this day" : "Largest premarket gap that morning"
  }, gapPct(e.pm_gap_max_pct)), /*#__PURE__*/React.createElement("td", {
    title: (e.qualified_by || []).includes("PM") ? "Reached the threshold during premarket" : "Qualified on the official opening gap"
  }, (e.qualified_by || []).map(q => q === "PM" ? "Premarket" : "Official").join(" + ") || "—"), /*#__PURE__*/React.createElement("td", {
    className: GAP_CATALYST_TONE[e.catalyst_kind] ? "gap-cat-" + GAP_CATALYST_TONE[e.catalyst_kind] : "",
    title: GAP_EVENT_CAT_TIP[e.catalyst_kind] || "No earnings and no SEC offering filing found on or just before this session"
  }, e.catalyst_kind && e.catalyst_kind !== "UNTAGGED" ? /*#__PURE__*/React.createElement("b", null, gapCatalyst(e.catalyst_kind)) : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num up",
    title: `This day ${e.direction === "up" ? "faded" : "rebounded"} ${gapPct(e.fav_pct)} from the open at its best`
  }, gapPct(e.fav_pct), " ", /*#__PURE__*/React.createElement("span", {
    className: "gap-arrow"
  }, e.direction === "up" ? "↓" : "↑")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down",
    title: `This day ${e.direction === "up" ? "squeezed" : "flushed"} ${gapPct(e.adv_pct)} against the trade first`
  }, gapPct(e.adv_pct), " ", /*#__PURE__*/React.createElement("span", {
    className: "gap-arrow"
  }, e.direction === "up" ? "↑" : "↓")), /*#__PURE__*/React.createElement("td", {
    className: "muted gap-basis-cell",
    title: e.exclusion ? "This day is excluded from every statistic" : e.basis === "MINUTE PATH" ? "Measured minute by minute" : "Daily bars only — no ordering claims"
  }, e.exclusion || (e.basis === "MINUTE PATH" ? "Minute path" : "Daily only")))))));
}

// Recent headlines — the "why is it moving" the statistics can't tell you.
// A stock gaps because of something that JUST happened, so anything older
// than a few days is noise here: last week's upgrade is not this morning's
// reason. Older headlines are dropped, and the count is shown so a quiet
// tape reads as quiet rather than as missing data.
const GAP_NEWS_MAX_DAYS = 3;
function GapNews({
  apiFetch,
  sym
}) {
  const [news, setNews] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let dead = false;
    setNews(null);
    setErr(null);
    apiFetch(`/api/news?symbol=${sym}`).then(r => r.json()).then(x => {
      if (!dead) x.items ? setNews(x) : setErr(x.error || "no news");
    }).catch(e => !dead && setErr(String(e)));
    return () => {
      dead = true;
    };
  }, [sym]);
  const all = news && news.items || [];
  const cutoff = Date.now() - GAP_NEWS_MAX_DAYS * 86400000;
  const items = all.filter(n => {
    const t = n.published ? new Date(n.published).getTime() : NaN;
    return Number.isNaN(t) ? false : t >= cutoff; // undatable ≠ recent
  });
  const dropped = all.length - items.length;
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: `Headlines from the last ${GAP_NEWS_MAX_DAYS} days only, newest first. A stock gaps because of a catalyst that just happened — older stories are filtered out so they can't be mistaken for this morning's reason. The statistics tell you what usually happens after a gap this size; the news tells you what kind of gap this one is.`
  }, "Latest news ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 why it's moving \xB7 last ", GAP_NEWS_MAX_DAYS, " days")), err && /*#__PURE__*/React.createElement("div", {
    className: "gap-note"
  }, "news unavailable: ", err), !news && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Loading headlines\u2026"), news && !items.length && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, "No headlines in the last ", GAP_NEWS_MAX_DAYS, " days for ", sym, dropped > 0 ? ` (${dropped} older ${dropped === 1 ? "story" : "stories"} filtered out — a gap with no fresh news is worth a second look).` : "."), items.length > 0 && /*#__PURE__*/React.createElement("ul", {
    className: "gap-news"
  }, items.slice(0, 8).map((n, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, /*#__PURE__*/React.createElement("span", {
    className: "gap-news-when",
    title: `Published ${n.date_label || ""} ${n.time_label || ""} ET`
  }, n.age || n.date_label || ""), n.url ? /*#__PURE__*/React.createElement("a", {
    href: n.url,
    target: "_blank",
    rel: "noopener noreferrer",
    title: "Open the full story in a new tab"
  }, n.title) : /*#__PURE__*/React.createElement("span", null, n.title), /*#__PURE__*/React.createElement("span", {
    className: "gap-news-src",
    title: "Publisher"
  }, n.source), n.day_change != null && /*#__PURE__*/React.createElement("span", {
    className: `gap-news-chg ${n.day_change >= 0 ? "up" : "down"}`,
    title: "How the stock closed on the day of this headline"
  }, gapPct(n.day_change))))));
}
function GapDetail({
  apiFetch,
  sym,
  onClose,
  onOpenTicker,
  liveQ
}) {
  const [d, setD] = useState(null);
  const [evs, setEvs] = useState(null);
  const [bt, setBt] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let dead = false;
    setD(null);
    setEvs(null);
    setBt(null);
    setErr(null);
    apiFetch(`/api/gap/detail?symbol=${sym}`, {
      noCache: true
    }).then(r => r.json()).then(x => {
      if (!dead) x.row || x.offline ? setD(x) : setErr(x.error || "no data");
    }).catch(e => !dead && setErr(String(e)));
    apiFetch(`/api/gap/events?symbol=${sym}`).then(r => r.json()).then(x => !dead && setEvs(x)).catch(() => {});
    apiFetch(`/api/gap/backtest?symbol=${sym}`).then(r => r.json()).then(x => !dead && setBt(x)).catch(() => {});
    return () => {
      dead = true;
    };
  }, [sym]);
  // Price and the gap it implies move every second; the statistics behind
  // them are history and cannot. Overlay the live quote on the fetched row.
  const r = d && d.row && (liveQ ? {
    ...d.row,
    ...liveQ
  } : d.row);
  const st = d && d.stats;
  return /*#__PURE__*/React.createElement("div", {
    className: "card gap-detail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, sym, " \xB7 gap evidence", /*#__PURE__*/React.createElement("button", {
    className: "rr-btn gap-back",
    onClick: onClose
  }, "\u2190 board"), /*#__PURE__*/React.createElement("button", {
    className: "rr-btn",
    onClick: () => onOpenTicker && onOpenTicker(sym),
    title: "Open this ticker on the Trade tab."
  }, "Trade tab \u2192")), r && /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, r.population === "EARNINGS" ? "earnings-gap history" : "non-earnings history", " \xB7", " ", r.data_basis, " \xB7 store: ", d.store_meta && d.store_meta.events, " events (", d.store_meta && d.store_meta.minute_scanned, " minute-scanned)"))), err && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, err), !d && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Loading ", sym, " evidence\u2026"), d && d.offline && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, "Live quote unavailable \u2014 showing stored history only."), r && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: "Where this stock stands right now. Prices refresh every 15 seconds."
  }, "Current setup"), /*#__PURE__*/React.createElement("div", {
    className: "gap-hero"
  }, /*#__PURE__*/React.createElement(GapSigPill, {
    signal: r.signal,
    held: r.signal_held
  }), /*#__PURE__*/React.createElement("div", {
    className: "gap-hero-nums"
  }, /*#__PURE__*/React.createElement("div", {
    title: "Current premarket price. Updates every 15 seconds while this tab is open."
  }, /*#__PURE__*/React.createElement("span", null, "Price"), /*#__PURE__*/React.createElement("b", null, "$", gapNum(r.price, 2))), /*#__PURE__*/React.createElement("div", {
    title: "How far the current price is from yesterday's regular-session close. This keeps moving until 9:30 \u2014 it is not the official opening gap."
  }, /*#__PURE__*/React.createElement("span", null, "Premarket gap"), /*#__PURE__*/React.createElement("b", {
    className: r.pm_gap_pct >= 0 ? "up" : "down"
  }, gapPct(r.pm_gap_pct))), /*#__PURE__*/React.createElement("div", {
    title: r.direction === "up" ? "How far the price has pulled back from the highest premarket price seen SO FAR this morning. The final premarket high doesn't exist yet — this is what's known right now." : "How far the price has bounced off the lowest premarket price seen so far this morning."
  }, /*#__PURE__*/React.createElement("span", null, r.direction === "up" ? "Off premarket high" : "Off premarket low"), /*#__PURE__*/React.createElement("b", null, gapPct(r.direction === "up" ? r.from_pm_high_pct : r.from_pm_low_pct))), /*#__PURE__*/React.createElement("div", {
    title: "Which way the premarket tape has drifted over the last 30 minutes. Negative on a gap up means it's already rolling over."
  }, /*#__PURE__*/React.createElement("span", null, "Last 30 min"), /*#__PURE__*/React.createElement("b", null, gapPct(r.trend_30m_pct))), /*#__PURE__*/React.createElement("div", {
    title: (r.catalyst_quote ? `"${r.catalyst_quote}"\n\n` : "") + (GAP_CATALYST_TIP[r.catalyst_kind] || "What is known to be driving this move, from real data sources only.")
  }, /*#__PURE__*/React.createElement("span", null, "Catalyst"), /*#__PURE__*/React.createElement("b", {
    className: GAP_CATALYST_TONE[r.catalyst_kind] ? "gap-cat-" + GAP_CATALYST_TONE[r.catalyst_kind] : ""
  }, gapCatalyst(r.catalyst_kind), r.catalyst_label ? ` · ${r.catalyst_label}` : "", r.catalyst_evidence === "headline" && /*#__PURE__*/React.createElement("span", {
    className: "gap-cat-grade",
    title: "This tag came from a news headline, not from a filing. Short-seller reports and index changes are never filed with the SEC by anybody, so a headline is the only record there is \u2014 weaker evidence, and shown as such."
  }, "\xB7 headline", r.catalyst_source ? ` · ${r.catalyst_source}` : ""), r.catalyst_url && /*#__PURE__*/React.createElement("a", {
    className: "gap-cat-link",
    href: r.catalyst_url,
    target: "_blank",
    rel: "noopener noreferrer",
    title: r.catalyst_evidence === "headline" ? "Open the story this tag was read from." : "Open the filing on the SEC's EDGAR site — the source this tag was read from."
  }, r.catalyst_evidence === "headline" ? "read the story" : "read the filing"))), r.catalyst_warning && /*#__PURE__*/React.createElement("div", {
    className: "gap-cat-warning",
    title: "The statistics below are still this stock's measured history \u2014 but they were measured on a stock that was free to move. Read them with that in mind."
  }, "\u26A0 ", r.catalyst_warning), /*#__PURE__*/React.createElement("div", {
    title: r.days_to_earnings == null ? "No scheduled earnings date found for this ticker." : `Next scheduled report: ${gapDateDow(r.next_earnings)}. A fade that has to survive an earnings report is a different trade from one with a clear runway.`
  }, /*#__PURE__*/React.createElement("span", null, "Next earnings"), /*#__PURE__*/React.createElement("b", {
    className: r.days_to_earnings != null && r.days_to_earnings <= 7 ? "down" : ""
  }, r.days_to_earnings == null ? "—" : r.days_to_earnings === 0 ? "today" : r.days_to_earnings < 0 ? gapDate(r.next_earnings) : `${r.days_to_earnings} day${r.days_to_earnings === 1 ? "" : "s"}`), r.next_earnings && r.days_to_earnings > 0 && /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, gapDate(r.next_earnings))), r.sector && /*#__PURE__*/React.createElement("div", {
    title: `The stock's sector ETF (${r.sector.etf}) gapped ${gapPct(r.sector.etf_gap_pct)} this morning. SECTOR DRIVEN means the whole group is moving together — the stock isn't doing anything special. ISOLATED means this move is its own story.`
  }, /*#__PURE__*/React.createElement("span", null, "Sector (", r.sector.etf, ")"), /*#__PURE__*/React.createElement("b", null, gapPct(r.sector.etf_gap_pct), " \xB7 ", r.sector.label)), r.quote_age_s != null && /*#__PURE__*/React.createElement("div", {
    title: "Seconds since the last actual trade printed. A premarket quote that is minutes old can make a stock look like it's moving when nothing is trading \u2014 signals are blocked past the freshness limit."
  }, /*#__PURE__*/React.createElement("span", null, "Quote age"), /*#__PURE__*/React.createElement("b", null, Math.round(r.quote_age_s), "s"))), r.signal_why && /*#__PURE__*/React.createElement("div", {
    className: "gap-why",
    title: "The evidence behind the signal above, in plain numbers."
  }, r.signal_why), r.what_changed && /*#__PURE__*/React.createElement("div", {
    className: "gap-changed",
    title: "What materially moved since the previous evaluation of this ticker."
  }, "Changed: ", r.what_changed)), st && st.n > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead"
  }, "What happened after similar gaps", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 n=", st.n, " ", /*#__PURE__*/React.createElement(GapQualityDot, {
    q: r.cohort_quality
  }), r.cohort_scope === "all_same_direction" ? " (widened to all same-direction gaps)" : " (size-matched)")), /*#__PURE__*/React.createElement("div", {
    className: "gap-grid2"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt",
    title: r.direction === "up" ? "Of the comparable historical gap ups, how often the stock dropped at least this far from the opening price. The bar is the rate; the white tick is the conservative (statistically cautious) end of the range." : "Of the comparable historical gap downs, how often the stock rose at least this far from the opening price. The white tick marks the conservative end of the range."
  }, r.direction === "up" ? "Faded at least…" : "Rebounded at least…"), ["1", "2", "3", "5"].map(lv => /*#__PURE__*/React.createElement("div", {
    key: lv,
    className: "gap-prow",
    title: `How often it moved ${lv}% or more in the profitable direction`
  }, /*#__PURE__*/React.createElement("span", null, lv, "%"), /*#__PURE__*/React.createElement("div", {
    className: "gap-ptrack"
  }, st.p_fav[lv] && /*#__PURE__*/React.createElement("i", {
    style: {
      width: `${st.p_fav[lv].p}%`
    }
  }), st.p_fav[lv] && /*#__PURE__*/React.createElement("em", {
    style: {
      left: `${st.p_fav[lv].lo}%`
    },
    title: `conservative bound ${st.p_fav[lv].lo}%`
  })), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(GapProb, {
    r: st.p_fav[lv]
  }))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt",
    title: "How much pain came first. A setup that eventually works but squeezes hard against you on the way is not the same trade as one that goes your way immediately."
  }, "Risk first"), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "How often the 2% profit target printed BEFORE a 3% stop would have been hit \u2014 measured minute by minute on the real historical paths. If the two happened inside the same minute, the tie is counted as a loss."
  }, /*#__PURE__*/React.createElement("span", null, "2% target before 3% stop"), /*#__PURE__*/React.createElement("b", null, st.tbs ? /*#__PURE__*/React.createElement(GapProb, {
    r: st.tbs
  }) : /*#__PURE__*/React.createElement("span", {
    className: "muted",
    title: "Only daily bars exist for these events. Daily bars show how far the stock moved but not in what order, so no honest before/after claim can be made."
  }, "Unknown \xB7 daily bars only"))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: `The typical (middle) ${r.direction === "up" ? "squeeze" : "flush"} before the trade resolved. Half the historical events were worse than this, half better.`
  }, /*#__PURE__*/React.createElement("span", null, "Typical ", r.direction === "up" ? "squeeze ↑" : "flush ↓"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_med_pct != null ? st.mae_med_pct : st.med_adv_pct))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: `The bad day: 9 out of 10 historical events ${r.direction === "up" ? "squeezed" : "flushed"} LESS than this. Roughly where a stop needs to sit to survive normal noise.`
  }, /*#__PURE__*/React.createElement("span", null, "Bad day (90th percentile)"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_p90_pct != null ? st.mae_p90_pct : st.adv_p90_pct))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: `The very bad day: only 1 in 20 historical events went against you further than this.`
  }, /*#__PURE__*/React.createElement("span", null, "Very bad day (95th percentile)"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_p95_pct != null ? st.mae_p95_pct : st.adv_p95_pct))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv gap-worst",
    title: "The single worst comparable day in this stock's history. Tail risk is never hidden here \u2014 if one day ran 18% against the trade, you see it."
  }, /*#__PURE__*/React.createElement("span", null, "Worst single day"), /*#__PURE__*/React.createElement("b", null, "moved ", gapPct(st.worst_adv_pct), " against \xB7 ", gapDateDow(st.worst_adv_date))), st.mae_before_target_med_pct != null && /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "On the days that DID reach the target, this is how far the stock typically pushed against you first \u2014 the heat you had to sit through to collect."
  }, /*#__PURE__*/React.createElement("span", null, "Typical heat before it worked"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_before_target_med_pct)))), /*#__PURE__*/React.createElement("div", {
    className: "gap-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt",
    title: "How fast it usually happens, and what the stock tends to do with the rest of the day."
  }, "Timing & tendencies"), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "On the days it reached 2%, how many minutes after the open it typically took. A fade that needs all afternoon ties up capital differently from one that's done by 10am."
  }, /*#__PURE__*/React.createElement("span", null, "Typical time to 2%"), /*#__PURE__*/React.createElement("b", null, st.med_time_to_min && st.med_time_to_min["2"] != null ? `${st.med_time_to_min["2"]} min` : "—")), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "How often the stock traded all the way back to the prior day's closing price \u2014 completely closing the gap."
  }, /*#__PURE__*/React.createElement("span", null, "Gap closed completely"), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(GapProb, {
    r: st.gap_fill
  }))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: `How often the stock kept going ${r.direction === "up" ? "up" : "down"} instead of reversing — it closed the day beyond where it opened. This is the fade/rebound failing.`
  }, /*#__PURE__*/React.createElement("span", null, "Kept going ", r.direction === "up" ? "higher" : "lower"), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(GapProb, {
    r: st.continuation
  }))), st.ev && st.ev.mean_pct != null && /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "Average profit or loss per trade if you had taken every one of these historical setups with the 2% target and 3% stop, including modeled trading costs and stops filling worse than the stop price. Positive means the edge survived the costs."
  }, /*#__PURE__*/React.createElement("span", null, "Average result per trade"), /*#__PURE__*/React.createElement("b", {
    className: st.ev.mean_pct > 0 ? "up" : "down"
  }, gapPct(st.ev.mean_pct, 2))), st.ev && st.ev.basis && /*#__PURE__*/React.createElement("div", {
    className: "gap-note",
    title: "Trading costs and stop slippage are modeled, not measured \u2014 real fills will differ."
  }, st.ev.basis))), /*#__PURE__*/React.createElement("div", {
    className: "gap-note"
  }, "Evidence basis: ", st.basis, st.tbs && st.tbs.intrabar_modeled_share > 0 && ` · ${Math.round(st.tbs.intrabar_modeled_share * 100)}% of orderings INTRABAR MODELED (same-minute ties resolved against the trade)`, " ", "\xB7 probabilities show conservative Wilson ranges on hover")), st && !st.n && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, "No comparable", r.population === "EARNINGS" ? " earnings-gap" : "", " history for this setup \u2014 the store fills as mornings accumulate."), bt && bt.grid && bt.grid.length > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead"
  }, "Target / stop grid ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 walk-forward, this ticker's measured paths")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table gap-bt-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which trade this row tests: fade = shorting a gap up, rebound = buying a gap down."
  }, "Trade"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How far from the opening price you'd take profit."
  }, "Target"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How far against you before you'd cut the trade. Stops are modeled to fill slightly WORSE than the stop price, because in reality they do."
  }, "Stop"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How many historical events this combination was tested on."
  }, "Events"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Percent of those events where the target printed before the stop. A high win rate with terrible losses is not a good strategy \u2014 read the Average and Worst columns too."
  }, "Win rate"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average profit or loss per trade after modeled costs. This is the number that decides whether the combination is worth using."
  }, "Average"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average result over the OLDER half of the history."
  }, "First half"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average result over the NEWER half. A combination that only worked in one half is curve-fitting, not an edge."
  }, "Second half"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "The single worst simulated trade for this combination."
  }, "Worst"), /*#__PURE__*/React.createElement("th", {
    title: "A checkmark means this target/stop pair made money in BOTH halves of the history independently \u2014 it survived the walk-forward test rather than being tuned to the whole sample."
  }, "Holds up"))), /*#__PURE__*/React.createElement("tbody", null, bt.grid.slice(0, 10).map((g, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    className: g.robust ? "gap-row-robust" : ""
  }, /*#__PURE__*/React.createElement("td", null, g.direction === "up" ? "fade" : "rebound"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.target_pct, "%"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.stop_pct, "%"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.n), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.win_rate, "%"), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${g.expectancy_pct > 0 ? "up" : "down"}`
  }, gapPct(g.expectancy_pct, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(g.h1_pct, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(g.h2_pct, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down"
  }, gapPct(g.worst_pct, 2)), /*#__PURE__*/React.createElement("td", null, g.robust ? "✓" : "—")))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-note"
  }, bt.note)), /*#__PURE__*/React.createElement(GapNews, {
    apiFetch: apiFetch,
    sym: sym
  }), evs && evs.events && evs.events.length > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: "Every historical day behind the percentages above. Click any column header to sort \u2014 biggest gaps, worst squeezes, or all the earnings days together."
  }, "The analogs ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 every event behind the numbers \xB7 click a header to sort")), /*#__PURE__*/React.createElement(GapAnalogTable, {
    events: evs.events,
    direction: r.direction
  }))));
}

// ── KOREA LEAD ──────────────────────────────────────────────────────────────
// The overnight context layer above the individual-stock scanner. Korea has
// already finished trading by the time a U.S. chip stock has to pick an
// opening price, so the panel answers, in order: what did Korea do, did the
// Korean chip names confirm it, what has historically happened to THIS
// ticker's OPEN after a Korean move like this, what is the U.S. premarket
// actually doing about it, and — separately, always separately — whether
// Korea has ever said anything useful about what happens after 9:30.
//
// Every number here is computed server-side. This file renders results; it
// does not reproduce the research.

const KL_STORE_KEY = "jerry_korea_target";
const KL_WINDOW_KEY = "jerry_korea_window";
const klRate = (v, d = 1) => v == null ? "—" : `${Number(v).toFixed(d)}%`;
const klCorr = v => v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}`;
const KL_BIAS_TONE = {
  UP: "up",
  DOWN: "down",
  MIXED: "warn",
  INCONCLUSIVE: "mut",
  "RELATIONSHIP UNSTABLE": "down",
  "NO DATA": "mut"
};
const KL_FRESH_TONE = {
  "CURRENT FOR SOURCE": "up",
  "SETTLED CLOSE": "up",
  DELAYED: "warn",
  STALE: "down",
  "AGE UNKNOWN": "warn",
  UNAVAILABLE: "mut"
};
const KL_MOVE_TONE = {
  EXTREME: "down",
  UNUSUAL: "warn",
  NORMAL: "mut",
  "NOT MEASURED": "mut"
};
const KL_DRIVER_LABEL = {
  kospi: "KOSPI",
  samsung: "Samsung Electronics",
  hynix: "SK Hynix"
};
const KL_EDGE_TONE = {
  STRONG: "up",
  MODERATE: "warn",
  "WEAK HISTORICAL EDGE": "mut",
  "NO EDGE": "mut",
  "NOT MEASURED": "mut"
};
const KL_CONFIRM_TONE = {
  CONFIRMED: "up",
  MIXED: "warn",
  DIVERGENCE: "down",
  UNAVAILABLE: "mut"
};
const KL_CMP_TONE = {
  CONFIRMING: "up",
  DIVERGING: "down",
  UNAVAILABLE: "mut"
};
const KL_TIP = {
  panel: "KOREA LEAD — what the Korean market did overnight, and what has historically happened to this U.S. ticker's OPENING PRICE after a Korean session like it. Korea finishes trading hours before New York opens, so today's Korean result is a completed, published fact by the time a U.S. chip stock has to choose an opening price. This panel measures whether that fact has ever told us anything. It is history, not a forecast, and it says how many sessions each number rests on.",
  session: "Where Seoul is in its own trading day right now, on Seoul's clock. Korea's regular session runs 09:00 to 15:30 Korea time. SESSION IN PROGRESS means today's Korean numbers are still moving and are shown as provisional. AFTER NORMAL CLOSE means the hours Korea normally trades have passed — which is not the same as the session being over, and the DATA state beside it is what settles that. NOT A TRADING DAY means it is a weekend in Seoul.",
  kospi: "The KOSPI Composite Index — the whole South Korean market — measured from yesterday's Korean close to today's Korean close. This is the signal every statistic in this panel is built on. If it cannot be read, there is no opening-gap bias and the panel says so rather than substituting something else.",
  samsung: "Samsung Electronics, the world's largest memory maker, from its previous Korean close to today's. Shown next to KOSPI rather than blended into it: the index is broad and this is not, and when they disagree that disagreement is the information.",
  hynix: "SK Hynix, the other Korean memory maker and the most direct listed comparison to Micron. Same measurement as Samsung — close to close on Korea's calendar.",
  usdkrw: "The US dollar against the Korean won, shown as CONTEXT ONLY. It is deliberately excluded from every statistic in this panel. The reason is honest and unglamorous: this app's currency source stamps the USD/KRW daily bar on a London day, and London is still open when New York opens — so that 'close' may have been set hours AFTER the U.S. opening price it would be used to predict. Using it would be reading the future. There is no intraday currency history reaching back years to sample it properly instead, so it stays out of the model until there is.",
  confirm: "Whether Korea's two memory names went the same way the KOSPI index went today. CONFIRMED = both agreed with the index AND all three moved far enough for the agreement to mean something — three series within a rounding error of unchanged are on the same side of zero by coin flip, and calling that confirmation would make the strongest label the easiest one to earn on the quietest day of the year. MIXED = they agreed but too weakly, or one agreed and one did not, or one could not be read. DIVERGENCE = a memory name moved materially AGAINST an index that had itself moved enough to be worth disagreeing with. This is descriptive only — there is no weighting behind it and it is not a score. It exists because KOSPI is a broad index and the stocks traded here are not: an index that rose while both memory makers fell is worth seeing plainly instead of being averaged into something that reads as mild.",
  target: "Which U.S. ticker the history below is measured on. The preset buttons are the semiconductor, memory and storage names this relationship was researched on; SPY and IGV are controls, not trades. Any ticker with enough matched history works, and opening a stock's row in the scanner below points this panel at it.",
  control: "A CONTROL, not a trade idea. SPY is the whole U.S. market — if Korea predicts SPY's open about as well as it predicts a chip stock's, then what is being measured is broad risk appetite rather than anything about semiconductors. IGV is technology SOFTWARE, which buys no memory from Korea at all — if Korea predicts IGV as well as it predicts SMH, the semiconductor story is weaker than it looks. Compare them against the chip names before trusting the chip names.",
  window: "How far back the history is measured. A 60-session result and a three-year result are not the same claim, and the sample size next to every number is there so they never get read as though they were. Longer windows are more stable; shorter ones describe the market as it is behaving now. Look at both in Details before believing either.",
  bias: "OPENING GAP BIAS — which way the historical sessions that looked like today's Korean move opened this ticker. UP or DOWN means the conservative end of the match rate cleared a coin flip and the matched sessions leaned that way at the median. MIXED means they did not lean enough to say. NO DATA means there were not enough matched sessions to say anything. This describes the OPEN ONLY. It says nothing about the rest of the day — that is the separate box to its right, and on the evidence so far it says something much weaker.",
  match: "HISTORICAL MATCH RATE — of the past sessions whose Korean move fell in the same bucket as today's, the share where this ticker's OPENING GAP went the same direction Korea did. It is deliberately NOT called a probability: nothing here has been calibrated against out-of-sample outcomes, so calling it a probability would claim an accuracy that has never been tested. The number after n is how many past sessions it rests on, and the range beside it is the conservative (Wilson) interval — the honest span the true rate could sit in given that sample size.",
  implied: "KOREA IMPLIED GAP — what this ticker's opening gap ACTUALLY DID on the past sessions whose Korean move looked like today's. It is a lookup into history, not a prediction: find today's Korean bucket, take every past session in it, and report the distribution of what followed. The median is the middle outcome — half the matched sessions opened better, half worse. The typical range is the middle half of them (the 25th to the 75th percentile), so a quarter of past sessions opened outside it in each direction.",
  premarket: "What this U.S. ticker is doing RIGHT NOW against yesterday's closing price. Before 9:30 that is the real premarket gap, which is what this whole panel is about. From 9:30 onward the honest comparison becomes the OFFICIAL opening gap — the price it actually opened at — because the last trade has stopped being an opening price. The label under the number always says which of the two you are looking at.",
  residual: "The difference between what the U.S. premarket is doing and the middle outcome of the matched Korean sessions, in percentage points. This is an OBSERVATION, not a trade. A premarket that has moved less than history suggests may be about to move further — or history may simply not apply this morning, and nothing in this panel can tell those two apart. It is here so the discrepancy is visible instead of being felt.",
  cmp: "Whether the U.S. premarket is moving the same way the matched Korean sessions moved this ticker's open (CONFIRMING) or the opposite way (DIVERGING). When the two signs disagree, no 'share already priced in' figure is shown — a share of a move in the other direction is not a quantity, and printing one would be a made-up number.",
  share: "How much of the matched historical opening gap the premarket has already covered. Shown only when both are moving the same way AND the historical gap is big enough for a ratio to mean something — dividing by an expectation near zero turns a rounding difference into a dramatic percentage. Over 100% means the premarket has moved further than the matched sessions typically opened.",
  afteropen: "AFTER OPEN EDGE — a completely separate measurement, from the 9:30 opening price to the 4:00 close. This is the question 'does Korea tell me what happens during the day', and it is judged by exactly the same standard as the opening-gap box, from exactly the same kind of evidence. Whatever difference you see between the two boxes is the data speaking, not a softer test applied to one of them. The research this feature was built on found the Korea relationship was materially stronger for where a stock OPENS than for what it does after — which is why these two are never combined into one bullish-or-bearish word.",
  pearson: "How closely the two moved together in a straight line, from −1 to +1. Zero means no linear relationship at all. It is reported next to the rank correlation on purpose: when the two disagree, a handful of extreme days are usually carrying the result.",
  spearman: "The same relationship measured on RANKS instead of raw sizes — did bigger Korean moves go with bigger U.S. gaps, whatever the shape of the relationship. It cannot be dominated by one crash the way an ordinary correlation can, so it is the more robust of the two.",
  buckets: "Every past session sorted by how big the Korean move was, with UP sessions and DOWN sessions kept strictly apart. They are never combined, because there is no reason to assume a 3% Korean fall does the same thing to a U.S. open that a 3% Korean rise does — and in the measured history they often do not. Each row shows how many sessions it holds, how often the U.S. open went the same way as Korea, and what the opening gaps actually looked like.",
  sources: "Which provider answered for each series, how many daily bars came back, and when it was last fetched. A series marked stale is being served from the cache because a refresh failed — the numbers are still real, they are just older than they should be.",
  skipped: "Sessions that could NOT become observations. 'Korea traded, U.S. closed' are U.S. holidays: those Korean sessions are skipped, never rolled forward onto a later U.S. session, because a Korean move on Thursday does not describe a U.S. open the following Tuesday. 'U.S. traded, Korea closed' are Korean holidays, of which there are many.",
  through: "The most recent COMPLETED U.S. session in the history. Today is deliberately not in its own history: before the open today's U.S. bar does not exist, and after the open it is unfinished, so scoring today against a set that contained today would not be a measurement.",
  relationship: "How strong the Korea-to-this-ticker relationship is RIGHT NOW, next to how strong it has been over a year. Both are shown because a one-year average can hold a relationship that has since halved — or inverted — and still read as healthy the whole way down. The little line is the last 120 sessions of the 60-session reading: its SHAPE is the point, because a number that has been sliding for months is a different thing from the same number holding steady.",
  unstable: "RELATIONSHIP UNSTABLE means the recent window and the one-year window disagree about which way this relationship even runs. When that happens neither number should be traded on, and no average of the two is offered — averaging a positive and a negative into something mild would hide exactly the thing you need to see.",
  health: "A plain-language summary of the four numbers printed beside it — nothing is hidden behind the label, and there is deliberately no score out of a hundred. STRONG and STABLE both mean the recent and long windows agree in direction and differ only in size. UNSTABLE means they disagree in direction. INSUFFICIENT DATA means there are not enough sessions to describe anything yet.",
  unusual: "Today's Korean move is larger than the great majority of that index's own moves over the past year. This adapts on its own: a fixed rule like 'KOSPI above 1.5% is major' fires constantly in a calm year and never in a violent one, where a comparison against the index's own recent history carries the volatility regime with it. It flags size, not direction — an unusually large move is not automatically a good one.",
  regression: "A SECOND, independent estimate of today's opening gap: a straight line fitted through every matched session, rather than the median of just the sessions that landed in today's bucket. The two are shown side by side and are never averaged.",
  disagree: "MODEL DISAGREEMENT means the two independent estimates do not agree — either they point opposite ways, or they are far enough apart to matter. They are deliberately NOT averaged: the midpoint of two estimates that disagree is a third number nothing supports, stated with more confidence than either of the two it came from. When they disagree, treat today's expected gap as genuinely uncertain.",
  band: "The range the fitted line's own past errors actually fell in — the middle 50% and the middle 80% of them. NOT a multiple of a standard deviation: gap errors have fat tails, so a 'plus or minus one sigma is 68%' band would be too narrow in exactly the sessions where being wrong costs the most.",
  residualPct: "Where today's gap between the implied and the actual sits among every such gap this pair has produced before. This is why the label is not a fixed percentage: two points light means something completely different for MU than for SPY, and different again in a calm month than a violent one. A percentile against this pair's own history carries all of that automatically. IN LINE is an ordinary distance. UNDERREACTION and OVERREACTION mean today is in the most extreme tenth either way. DIVERGENCE means the premarket is moving the OPPOSITE way, which is a different thing from moving a different amount.",
  research: "The full research layer: whether Korea adds anything the previous U.S. session did not already say, which way the information actually travels, whether the relationship survives an Asian control, how it has behaved year by year, which Korean input best predicts which U.S. ticker, and whether any model beats the simple KOSPI baseline OUT OF SAMPLE. This is measurement, not a trading rule — nothing here is wired into the panel above.",
  incremental: "The number that decides whether Korea is a signal or an echo. It is not how well Korea explains the U.S. open — it is how much Korea ADDS once the previous U.S. session is already in the model. A variable that only repeats what is already known shows a large correlation and adds nothing. The t-statistic uses robust standard errors, because daily market returns are far more volatile in some months than others and ordinary standard errors understate the uncertainty exactly then.",
  leadlag: "Which direction the information is actually travelling. Korea partly ECHOES the previous U.S. session and partly LEADS the next U.S. open, and both are measured here so the balance is visible instead of assumed. Note that rows A and D are close to the same measurement indexed two ways — the U.S. session before a Korean session is usually the one the previous Korean session followed — so they are shown together rather than counted as two separate findings.",
  asiacontrol: "The test for the one explanation that would make this whole feature a mirage: that what looks like Korea leading U.S. chips is really overnight ASIAN risk appetite, with Korea just being a convenient thermometer for it. The Nikkei has far less semiconductor weight, so if it did the same job in the same regression, the semiconductor story would be wrong. All three inputs are fitted on ONE identical set of sessions, because a horse race run on different rows is not a horse race.",
  walkforward: "Out-of-sample testing on time-series data, done the only way that is honest: expanding windows. Every prediction comes from a model fitted ONLY on sessions strictly earlier than the one it is scoring. There is no random train/test split anywhere — a random split on market data trains on the future and scores the past, and every model looks brilliant when it does. A model counts as beating the baseline only when it is better on BOTH the size of the error and the direction; winning one while losing the other shows nothing.",
  placebo: "A deliberate sanity check against the failure that would otherwise be invisible: an off-by-one in the date alignment that still produces a plausible-looking number. The correct same-day pairing is compared against pairing Korea one session early, one session late, and against 200 random shuffles of Korea's own dates. If the correct alignment did not clearly beat all of them, the relationship would not be real.",
  fdr: "Test dozens of pairs and a couple will look significant purely by luck. The q-value is the Benjamini-Hochberg false-discovery rate across the whole matrix — the share of the discoveries at that cut-off expected to be false. Read the q-value, not the raw p-value; a striking correlation that fails this correction has not earned attention.",
  matrix: "Every Asian input against every U.S. target, measured DIRECTLY on each ticker's own history rather than inferred through a sector proxy. Sorted by the strength of the relationship. The role column matters: signal inputs are the Korean series the panel actually uses, research inputs are Taiwan, and the Nikkei is a control that is never promoted into a signal.",
  byyear: "The same relationship split by calendar year — how a signal that only worked during one semiconductor cycle gives itself away. A relationship that shows up in every year is a structural one; a relationship that lives in two of them is a story about those two years.",
  regime: "Whether Korea matters more when markets are violent. The split is at the MEDIAN of trailing volatility rather than at a round number, and — this is the part that matters — the volatility is computed through the PRIOR close. Using the same day's VIX would be a future value at 9:30, and every regime finding built on it would be an artefact of that.",
  surprise: "Raw KOSPI is partly an echo of the previous U.S. semiconductor session. KOREA SURPRISE is what is left after subtracting what that session predicted — an attempt to isolate genuinely NEW overnight information. The echo model behind it is fitted only on sessions before each row, so it contains no hindsight. Whether it is actually better is settled by the out-of-sample comparison, not by these two correlations.",
  convergence: "If a stock has not moved as far as Korea implies, does it make up the difference after 9:30? This box exists to answer that question honestly, including when the answer is no. Note the basis: this app holds no historical premarket prices for ordinary sessions, so this is measured from the OFFICIAL OPEN — whether a stock that opened further from the implied gap than usual closes the difference during the day.",
  inconclusive: "INCONCLUSIVE means the matched history cannot establish a direction — either there were fewer matched sessions than this panel requires before it names one, or the honest range around the match rate contains a coin flip, or the count and the median disagreed with each other. This is NOT the same answer as RELATIONSHIP UNSTABLE. Inconclusive says the evidence is too thin to tell you anything; unstable says the relationship itself has changed underneath evidence that may look perfectly decisive. They call for opposite responses, so they are given different words.",
  freshness: "How old this reading is, from the PROVIDER'S own timestamp rather than from when this app happened to fetch it. CURRENT FOR SOURCE is the strongest thing this can honestly say, and it is weaker than it sounds — the Korean series here come from a delayed feed, so a two-minute-old print is the freshest thing available and still is not the exchange's live price. DELAYED means visibly behind but usable as context. STALE means too old to describe the current session. SETTLED CLOSE means the Korean session is finished, so this is a final closing price and its age is not a defect. AGE UNKNOWN means the provider did not timestamp it at all, which is treated as unverified rather than as current.",
  sessiondata: "Two different questions, shown side by side. The SCHEDULED state is what the clock says should be happening in Seoul. The DATA state is what the Korean numbers are actually doing. They usually agree; the case worth seeing is when they do not. Korea moves its trading day — most visibly on the annual national university entrance exam, when the exchange opens and closes an hour later — and no exam-day calendar ships with this app, deliberately, because a hardcoded calendar is silently wrong the first year nobody updates it. So a session is only called final once its value has been watched standing still, never while it is still moving, however late that runs.",
  moveState: "How far into its own recent history today's move sits. UNUSUAL means larger than the great majority of that index's own moves over the trailing year; EXTREME is the far tail of the same distribution. Percentiles rather than fixed percentages, because a fixed rule like 'KOSPI above 1.5% is major' fires constantly in a calm year and never in a violent one, where a comparison against the index's own recent history carries the volatility regime with it. The percentile and the number of sessions behind it are printed beside the label so you can disagree with the word by looking at the number. It flags SIZE, not direction — an unusually large move is not automatically a good one.",
  premarketStale: "PREMARKET NOT AVAILABLE YET means the most recent price for this ticker is too old to describe this morning, so no premarket gap, residual, percentile or confirming call is calculated from it. This gate exists because of a measured failure rather than a hypothetical one: when the primary quote source is unavailable, the fallback returns YESTERDAY'S four o'clock close as the last price and the close before that as the previous close — which subtract into yesterday's full-day return wearing this morning's label. It looks entirely plausible and it is completely wrong. Thin premarket names like WDC and STX are where an old print looks most like a live one.",
  driver: "PRIMARY KOREA DRIVER — which Korean input has actually predicted THIS ticker's open best, measured OUT OF SAMPLE on an expanding walk-forward, never on an in-sample correlation. It changes reluctantly by design: a challenger must clear an absolute quality floor on its own merits, then beat the sitting driver on BOTH direction accuracy and error size by a margin, and hold both advantages across dozens of matched sessions. Without that hysteresis the driver flips on noise and reads SK Hynix on Monday, KOSPI on Tuesday, Samsung on Wednesday — three findings that are one sampling error. NO CLEAR PRIMARY DRIVER is a real answer: if nothing clears the floor, then nothing has been shown to lead this ticker, and promoting the least bad of several weak signals would put a finding on screen that nobody found.",
  selfcheck: "Everything that has to be true before this panel is allowed to sound confident, checked in one place. When one of them fails the output is degraded — to NO DATA, INCONCLUSIVE, RELATIONSHIP UNSTABLE or PREMARKET NOT AVAILABLE YET — rather than keeping its confident wording while an input has quietly stopped being true.",
  forward: "FORWARD RECORDED — what this app actually said out loud before the open, scored against what happened afterwards. Completely separate from the historical match rates elsewhere on this panel, and never combined with them: a backtest and a forward record answer different questions, and a single hit rate covering both would be neither of them. Predictions are archived immutably at 9:25am Eastern and scored later as separate records, so a forecast can never be quietly rewritten once the answer is known. Nothing is shown here until enough genuine mornings have accumulated for it to mean something.",
  checkpoints: "Point-in-time records of the Korean session, archived at fixed Seoul times going forward. They exist to answer a question no amount of daily history can: how early in the Korean session the predictive information stops improving. A daily bar holds one number for the whole session, so the 11:00 state and the closing state are the same bar — the only way to tell them apart is to write them down as they happen. MISSED means a Korean session existed and this app failed to record that checkpoint; it is never filled in later, because a reading taken at 13:47 is not the 13:00 observation. NO KOREA SESSION means the market was shut, which is a fact about Korea rather than a failure here.",
  nodata: "Korea Lead cannot produce an opening-gap bias without the KOSPI series. It does not fall back to Samsung, to SK Hynix, or to yesterday's reading — the panel says NO DATA instead, because a substituted signal would be a different measurement wearing this one's name."
};

// A stacked label-over-value pair. The boxes are narrow and several of the
// values are long ("78.6% · n=14 · 52.4%–92.4%"), so a label and a value on
// one line collide the moment the label wraps. Stacking removes the failure
// mode entirely and reads better in a column on a phone.
function KlStat({
  label,
  tip,
  children,
  tone
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "kl-stat",
    title: tip
  }, /*#__PURE__*/React.createElement("span", null, label), /*#__PURE__*/React.createElement("b", {
    className: tone || ""
  }, children));
}
function KlPill({
  text,
  tone,
  tip
}) {
  return /*#__PURE__*/React.createElement("span", {
    className: `gap-sig gap-sig-${tone || "mut"}`,
    title: tip
  }, text || "—");
}

// One Korean series line: name, move, and — when it matters — a marker
// saying the value is provisional, stale, or context only.
//
// The session date is deliberately NOT repeated on every row. All four
// series normally belong to the same Korean session, which is named once
// in the line above; four identical dates would be noise. A date that
// DIFFERS is the opposite of noise — it means that series is showing an
// older session than the rest — so that is the only case it appears, in
// the warning colour.
function KlSeries({
  s,
  tip,
  sessionDate
}) {
  if (!s) return null;
  const bad = !s.ok;
  const odd = s.session_date && sessionDate && s.session_date !== sessionDate;
  const provTip = `${s.name_from_provider || s.label}${s.symbol ? ` (${s.symbol})` : ""}` + ` · Korean session of ${gapDate(s.session_date)}`;
  return /*#__PURE__*/React.createElement("div", {
    className: "kl-srow",
    title: `${tip}\n\n${provTip}`
  }, /*#__PURE__*/React.createElement("span", {
    className: "kl-srow-name"
  }, /*#__PURE__*/React.createElement("span", null, s.label), !s.in_model && /*#__PURE__*/React.createElement("em", {
    className: "kl-ctx",
    title: KL_TIP.usdkrw
  }, "context"), s.provisional && /*#__PURE__*/React.createElement("em", {
    className: "kl-prov",
    title: "Seoul is still trading, so this number is not final for today."
  }, "provisional"), s.stale && /*#__PURE__*/React.createElement("em", {
    className: "kl-prov",
    title: "Served from the stored copy \u2014 the last refresh of this series failed."
  }, "stale"), s.move_state && (s.move_state === "UNUSUAL" || s.move_state === "EXTREME") && /*#__PURE__*/React.createElement("em", {
    className: `kl-move ${KL_MOVE_TONE[s.move_state] || "mut"}`,
    title: `${s.label} ${gapPct(s.pct, 2)} is larger than ${klRate(s.abs_percentile, 0)} of its own trailing ${s.trailing_n} sessions. ${KL_TIP.moveState}`
  }, s.move_state.toLowerCase())), bad ? /*#__PURE__*/React.createElement("b", {
    className: "muted",
    title: s.error ? `Unavailable: ${s.error}` : "This series could not be read. It is left blank rather than filled with a zero — a missing move is not a flat move."
  }, "UNAVAILABLE") : /*#__PURE__*/React.createElement("b", {
    className: s.pct >= 0 ? "up" : "down"
  }, gapPct(s.pct, 2)), s.in_model && s.freshness && s.freshness.state && /*#__PURE__*/React.createElement("span", {
    className: `kl-fresh ${KL_FRESH_TONE[s.freshness.state] || "mut"}`,
    title: `${s.freshness.detail || ""}${s.provider_timestamp ? `\n\nProvider timestamp: ${s.provider_timestamp}` : ""}\n\n${KL_TIP.freshness}`
  }, s.freshness.state), odd && /*#__PURE__*/React.createElement("span", {
    className: "kl-srow-when",
    title: s.off_session ? `This series last traded on ${gapDate(s.session_date)}, which is NOT the session the rest of the panel is reading. Its move is shown, but it is NOT counted as confirming or diverging from KOSPI — comparing two different trading days would not mean anything.` : `This series is showing the Korean session of ${gapDate(s.session_date)}, which is NOT the session the rest of the panel is reading. Treat it as older data, not as today's move.`
  }, "\u26A0 ", gapDate(s.session_date), s.off_session ? " · not counted" : ""));
}

// "72.4% · n=58 · 60% to 83%" — a match rate never renders alone.
function KlMatch({
  sd,
  tip
}) {
  if (!sd || sd.rate_pct == null) return /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014");
  return /*#__PURE__*/React.createElement("span", {
    title: tip || `${sd.k} of ${sd.n} matched sessions went the same way as Korea. Conservative range ${klRate(sd.lo_pct)} to ${klRate(sd.hi_pct)}.`
  }, /*#__PURE__*/React.createElement("b", null, klRate(sd.rate_pct)), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 n=", sd.n, " \xB7 ", klRate(sd.lo_pct), "\u2013", klRate(sd.hi_pct)));
}
function KlBucketTable({
  rows,
  title,
  tip
}) {
  if (!rows || !rows.length) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: "kl-buckets"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: tip || KL_TIP.buckets
  }, title), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-bucket-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "How big the Korean move was that day. Upside and downside are kept in separate tables and never merged."
  }, "Korean move"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How many past sessions fall in this bucket. A rate from a handful of sessions is not a rate."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.match
  }, "Match rate"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "The conservative (Wilson) range around the match rate, given how few or how many sessions it rests on."
  }, "Honest range"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "The middle outcome: half of the sessions in this bucket opened better than this, half worse."
  }, "Median gap"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "The average opening gap in this bucket. Read it next to the median \u2014 when they disagree, a few extreme mornings are pulling the average."
  }, "Average gap"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "The middle half of the outcomes, from the 25th to the 75th percentile. A quarter of past sessions opened outside this range on each side."
  }, "Typical range"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(b => /*#__PURE__*/React.createElement("tr", {
    key: b.bucket
  }, /*#__PURE__*/React.createElement("td", {
    title: `${b.label} — ${b.n} matched session${b.n === 1 ? "" : "s"}`
  }, b.label), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, b.n), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, b.same_direction ? klRate(b.same_direction.rate_pct) : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, b.same_direction ? `${klRate(b.same_direction.lo_pct)}–${klRate(b.same_direction.hi_pct)}` : "—"), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${b.distribution && b.distribution.median_pct >= 0 ? "up" : "down"}`
  }, gapPct(b.distribution && b.distribution.median_pct, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(b.distribution && b.distribution.avg_pct, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, b.distribution ? `${gapPct(b.distribution.p25_pct, 2)} to ${gapPct(b.distribution.p75_pct, 2)}` : "—")))))));
}

// A 120-session trail of the 60-session correlation. Drawn rather than
// tabulated because the SHAPE is the information: a relationship sliding
// for months and one holding steady can share today's number exactly.
function KlSpark({
  points,
  tip
}) {
  const pts = (points || []).filter(v => v != null);
  if (pts.length < 8) return null;
  const lo = Math.min(...pts, 0),
    hi = Math.max(...pts, 0);
  const span = hi - lo || 1;
  const W = 74,
    H = 20;
  const d = pts.map((v, i) => {
    const x = i / (pts.length - 1) * W;
    const y = H - (v - lo) / span * H;
    return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join("");
  const zero = H - (0 - lo) / span * H;
  const last = pts[pts.length - 1];
  return /*#__PURE__*/React.createElement("svg", {
    className: "kl-spark",
    width: W,
    height: H,
    viewBox: `0 0 ${W} ${H}`,
    role: "img",
    "aria-label": "recent relationship trend",
    title: tip || `The 60-session correlation over the last ${pts.length} sessions: from ${pts[0].toFixed(2)} to ${last.toFixed(2)}. The dashed line is zero.`
  }, zero >= 0 && zero <= H && /*#__PURE__*/React.createElement("line", {
    x1: "0",
    y1: zero,
    x2: W,
    y2: zero,
    className: "kl-spark-zero"
  }), /*#__PURE__*/React.createElement("path", {
    d: d,
    className: `kl-spark-line ${last >= 0 ? "up" : "down"}`
  }));
}
const KL_HEALTH_TONE = {
  STRONG: "up",
  STABLE: "up",
  WEAK: "mut",
  UNSTABLE: "down",
  "INSUFFICIENT DATA": "mut"
};
function KlDetails({
  d
}) {
  const dg = d.diagnostics || {};
  const m = dg.measures || {};
  const chips = dg.chip_signals || {};
  const srcs = d.sources || {};
  const measureRow = (key, label, tip) => {
    const s = m[key] || {};
    return /*#__PURE__*/React.createElement("tr", {
      key: key
    }, /*#__PURE__*/React.createElement("td", {
      title: tip
    }, label), /*#__PURE__*/React.createElement("td", {
      className: "scan-num muted"
    }, s.n ?? "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num",
      title: KL_TIP.pearson
    }, klCorr(s.pearson)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num",
      title: KL_TIP.spearman
    }, klCorr(s.spearman)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, /*#__PURE__*/React.createElement(KlMatch, {
      sd: s.same_direction
    })));
  };
  const chk = d.self_check || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "kl-details"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.selfcheck
  }, "Data quality self-check", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", chk.passed ?? 0, " of ", chk.n ?? 0, " passed")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-diag-table kl-check-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "What has to be true."
  }, "Check"), /*#__PURE__*/React.createElement("th", {
    title: "Whether it holds right now."
  }, "Result"), /*#__PURE__*/React.createElement("th", {
    className: "kl-check-detail",
    title: "What was found."
  }, "Detail"))), /*#__PURE__*/React.createElement("tbody", null, (chk.checks || []).map(c => /*#__PURE__*/React.createElement("tr", {
    key: c.name,
    title: c.detail || c.name
  }, /*#__PURE__*/React.createElement("td", null, c.name), /*#__PURE__*/React.createElement("td", {
    className: c.ok ? "up" : c.blocking ? "down" : "warn"
  }, c.ok ? "PASS" : c.blocking ? "DEGRADED" : "LIMITED"), /*#__PURE__*/React.createElement("td", {
    className: "muted kl-check-detail"
  }, c.detail)))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.sessiondata
  }, "Korean session \u2014 the clock against the data"), /*#__PURE__*/React.createElement("div", {
    className: "kl-kv"
  }, /*#__PURE__*/React.createElement(KlStat, {
    label: "Scheduled state",
    tip: KL_TIP.sessiondata
  }, d.session && d.session.scheduled_state || "—"), /*#__PURE__*/React.createElement(KlStat, {
    label: "Data state",
    tip: d.session && d.session.final_reason || KL_TIP.sessiondata
  }, d.session && d.session.data_state || "—"), /*#__PURE__*/React.createElement(KlStat, {
    label: "Normal close",
    tip: KL_TIP.sessiondata
  }, d.session && d.session.scheduled_close || "—", " Seoul"), /*#__PURE__*/React.createElement(KlStat, {
    label: "Latest market timestamp",
    tip: `The provider's own stamp on the most recent Korean reading: ${d.session && d.session.latest_market_timestamp || "not published"}. ${KL_TIP.freshness}`
  }, d.session && d.session.latest_market_timestamp_pretty || "—"), /*#__PURE__*/React.createElement(KlStat, {
    label: "Provider market state",
    tip: d.session && d.session.provider_market_state_note || KL_TIP.sessiondata
  }, d.session && d.session.provider_market_state || "not published"), /*#__PURE__*/React.createElement(KlStat, {
    label: "Readings of this session",
    tip: KL_TIP.sessiondata
  }, (d.session && d.session.readings_today) ?? "—", /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 steady ", (d.session && d.session.steady_minutes) ?? "—", " min"))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: "How KOSPI relates to each of the three U.S. measurements. They are different questions and are never mixed: the opening gap and the full day differ by exactly the move after 9:30."
  }, "KOSPI against each U.S. measurement ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 ", d.window_label)), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-diag-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which U.S. measurement is being predicted."
  }, "U.S. measurement"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Matched sessions behind the row."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.pearson
  }, "Correlation"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.spearman
  }, "Rank correlation"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.match
  }, "Same direction"))), /*#__PURE__*/React.createElement("tbody", null, measureRow("opening_gap", "Opening gap (9:30 open vs prior close)", "Today's regular-session opening price against yesterday's regular-session close. This is what Korea Lead is about."), measureRow("open_to_close", "Open to close (9:30 to 4:00)", "From the opening price to the closing price on the same day — everything that happens after the open, with the gap itself removed."), measureRow("full_day", "Full day (close to close)", "Yesterday's close to today's close. It contains the opening gap AND the move after it, which is exactly why it is a poor way to judge either one on its own.")))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: "The same measurement, run with Samsung Electronics and with SK Hynix in place of KOSPI. Nothing is combined \u2014 this is here so you can see whether the Korean chip names carry more information about this ticker's open than the broad index does."
  }, "Korean chip names, measured the same way ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 against the opening gap \xB7 not combined with anything")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-diag-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which Korean series is used as the signal in this row."
  }, "Korean signal"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Matched sessions behind the row."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.pearson
  }, "Correlation"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.spearman
  }, "Rank correlation"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.match
  }, "Same direction"))), /*#__PURE__*/React.createElement("tbody", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    title: KL_TIP.kospi
  }, "KOSPI (the index)"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, (m.opening_gap || {}).n ?? "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klCorr((m.opening_gap || {}).pearson)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klCorr((m.opening_gap || {}).spearman)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, /*#__PURE__*/React.createElement(KlMatch, {
    sd: (m.opening_gap || {}).same_direction
  }))), ["samsung", "hynix"].map(k => /*#__PURE__*/React.createElement("tr", {
    key: k
  }, /*#__PURE__*/React.createElement("td", {
    title: k === "samsung" ? KL_TIP.samsung : KL_TIP.hynix
  }, k === "samsung" ? "Samsung Electronics" : "SK Hynix"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, (chips[k] || {}).n ?? "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klCorr((chips[k] || {}).pearson)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klCorr((chips[k] || {}).spearman)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, /*#__PURE__*/React.createElement(KlMatch, {
    sd: (chips[k] || {}).same_direction
  }))))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.window
  }, "The same measurement over every lookback ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 opening gap \xB7 sample size is the point")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-diag-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "How far back this row looks."
  }, "Lookback"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Matched sessions in this lookback \u2014 never assume a 60-session result carries the confidence of a three-year one."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    title: "The first matched session in this lookback."
  }, "Starting"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.pearson
  }, "Correlation"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.spearman
  }, "Rank correlation"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.match
  }, "Same direction"))), /*#__PURE__*/React.createElement("tbody", null, (dg.windows || []).map(w => /*#__PURE__*/React.createElement("tr", {
    key: w.window,
    className: w.window === d.window ? "kl-row-on" : ""
  }, /*#__PURE__*/React.createElement("td", null, w.label, w.window === d.window ? " ·" : ""), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, w.n), /*#__PURE__*/React.createElement("td", {
    className: "muted"
  }, gapDate(w.first_date)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klCorr(w.pearson)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klCorr(w.spearman)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, /*#__PURE__*/React.createElement(KlMatch, {
    sd: w.same_direction
  }))))))), /*#__PURE__*/React.createElement(KlBucketTable, {
    rows: (d.opening_gap || {}).buckets_down,
    title: "After a Korean session DOWN by\u2026",
    tip: KL_TIP.buckets
  }), /*#__PURE__*/React.createElement(KlBucketTable, {
    rows: (d.opening_gap || {}).buckets_up,
    title: "After a Korean session UP by\u2026",
    tip: KL_TIP.buckets
  }), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.sources
  }, "Where the data came from"), /*#__PURE__*/React.createElement("div", {
    className: "kl-srcs"
  }, Object.keys(srcs).map(k => {
    const s = srcs[k] || {};
    return /*#__PURE__*/React.createElement("div", {
      key: k,
      className: "gap-kv",
      title: `${s.bars || 0} daily bars${s.fetched ? `, last fetched ${gapWhen(s.fetched)}` : ""}${s.stale ? " — served from the stored copy after a failed refresh" : ""}`
    }, /*#__PURE__*/React.createElement("span", null, k === "us" ? "U.S. target" : k === "korea" ? "KOSPI" : k === "samsung" ? "Samsung Electronics" : k === "hynix" ? "SK Hynix" : k, " ", "(", s.symbol, ")"), /*#__PURE__*/React.createElement("b", null, s.source || "unavailable", /*#__PURE__*/React.createElement("small", {
      className: "muted"
    }, " \xB7 ", s.bars || 0, " bars")));
  }), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: KL_TIP.skipped
  }, /*#__PURE__*/React.createElement("span", null, "Sessions skipped"), /*#__PURE__*/React.createElement("b", null, (dg.skipped || {}).korea_only || 0, " Korea traded, U.S. closed", " · ", (dg.skipped || {}).us_only || 0, " U.S. traded, Korea closed")), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: KL_TIP.through
  }, /*#__PURE__*/React.createElement("span", null, "History runs through"), /*#__PURE__*/React.createElement("b", null, gapDate(dg.through))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "A daily series adjusted for splits and spinoffs should never print an overnight move this large, so any day that does is left out of the statistics and counted rather than explained away. It is not a split detector and does not claim to be."
  }, /*#__PURE__*/React.createElement("span", null, "Days excluded as not credible"), /*#__PURE__*/React.createElement("b", null, "beyond \xB1", dg.max_credible_move_pct, "% overnight")), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "The exact definition these statistics were built under, and the version of the mathematics that built them. If either ever changes, cached results stop being served for today's question."
  }, /*#__PURE__*/React.createElement("span", null, "Signal definition"), /*#__PURE__*/React.createElement("b", {
    className: "kl-defn"
  }, d.signal_definition))));
}

// ── the research drawer ─────────────────────────────────────────────────────
// Loaded on demand: the walk-forward alone re-fits every candidate model once
// per fold, so it is never on the path that renders the panel above.

const KL_VERDICT_TONE = {
  PASSED: "good",
  CONVERGES: "good",
  "KOREA-SPECIFIC": "good",
  "NO MEASURABLE EDGE": "mut",
  "NOT MEASURABLE": "mut",
  "SHARED WITH BROAD ASIA": "warn",
  "DIVERGES FURTHER": "warn",
  "EXPLAINED BY BROAD ASIA": "bad",
  FAILED: "bad"
};
const klVerdictTone = v => KL_VERDICT_TONE[v] || (typeof v === "string" && v.startsWith("WEAK") ? "warn" : "mut");

// Spelled out rather than derived from the key — a naive underscore
// replace turned "kospi_only" into "kospi + alone".
const KL_ASIA_MODEL = {
  kospi_only: "KOSPI alone",
  nikkei_only: "Nikkei alone",
  kospi_plus_nikkei: "KOSPI + Nikkei",
  kospi_nikkei_tsmc: "KOSPI + Nikkei + TSMC"
};
const KL_ASIA_MODEL_TIP = {
  kospi_only: "KOSPI on its own, on the shared sample.",
  nikkei_only: "The Nikkei on its own, on the very same sessions — the control.",
  kospi_plus_nikkei: "Both together. If Korea were only a thermometer for Asian risk appetite, its t-statistic would collapse here.",
  kospi_nikkei_tsmc: "Both plus Taiwan, to see whether Taipei carries anything the other two do not."
};
function KlVerdict({
  value,
  tip
}) {
  if (!value) return null;
  return /*#__PURE__*/React.createElement("span", {
    className: `kl-verdict ${klVerdictTone(value)}`,
    title: tip
  }, value);
}

// The genuine point-in-time record. Deliberately quiet: until enough real
// mornings have accumulated it shows how many it has and nothing else,
// because a three-observation scorecard looks like evidence and is not.
function KlForward({
  apiFetch,
  symbol
}) {
  const [s, setS] = useState(null);
  const [c, setC] = useState(null);
  useEffect(() => {
    let dead = false;
    apiFetch(`/api/korea_forward/scorecard?symbol=${encodeURIComponent(symbol)}`, {
      noCache: true
    }).then(x => x.json()).then(x => !dead && setS(x)).catch(() => {});
    apiFetch("/api/korea_forward/coverage", {
      noCache: true
    }).then(x => x.json()).then(x => !dead && setC(x)).catch(() => {});
    return () => {
      dead = true;
    };
  }, [symbol]);
  if (!s && !c) return null;
  const cp = c && c.per_checkpoint || {};
  const rows = Object.keys(cp).sort();
  return /*#__PURE__*/React.createElement("div", {
    className: "kl-forward"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.forward
  }, "Forward recorded ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 not a backtest")), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.forward
  }, s && s.note || ""), s && !s.usable && /*#__PURE__*/React.createElement("div", {
    className: "research-empty",
    title: KL_TIP.forward
  }, s.reason), s && s.usable && /*#__PURE__*/React.createElement("div", {
    className: "kl-kv"
  }, /*#__PURE__*/React.createElement(KlStat, {
    label: "Opening direction",
    tip: KL_TIP.forward
  }, s.direction_correct, " of ", s.n, /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 ", klRate(s.direction_pct))), /*#__PURE__*/React.createElement(KlStat, {
    label: "Opening gap mean absolute error",
    tip: KL_TIP.forward
  }, s.gap_mae_pct == null ? "—" : `${s.gap_mae_pct} pts`), /*#__PURE__*/React.createElement(KlStat, {
    label: "Median absolute error",
    tip: KL_TIP.forward
  }, s.gap_median_abs_error_pct == null ? "—" : `${s.gap_median_abs_error_pct} pts`), /*#__PURE__*/React.createElement(KlStat, {
    label: "Recorded between",
    tip: KL_TIP.forward
  }, gapDate(s.first_date), " and ", gapDate(s.last_date))), s && s.mixed_versions_note && /*#__PURE__*/React.createElement("div", {
    className: "kl-flag",
    title: KL_TIP.forward
  }, "\u26A0 ", s.mixed_versions_note), rows.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-diag-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: KL_TIP.checkpoints
  }, "Seoul checkpoint"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Sessions archived on time."
  }, "Captured"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Sessions the app failed to record. Never filled in later."
  }, "Missed"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Dates Korea did not trade. Not a failure."
  }, "No Korea session"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(k => /*#__PURE__*/React.createElement("tr", {
    key: k
  }, /*#__PURE__*/React.createElement("td", null, k === "final" ? "Confirmed close" : `${k} Seoul`), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, cp[k].captured), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, cp[k].missed), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, cp[k].no_session)))))), c && c.note && /*#__PURE__*/React.createElement("div", {
    className: "gap-note",
    title: KL_TIP.checkpoints
  }, c.note));
}
function KlResearch({
  apiFetch,
  symbol,
  window: win
}) {
  const [r, setR] = useState(null);
  const [m, setM] = useState(null);
  const [err, setErr] = useState(null);
  const [asked, setAsked] = useState(false);
  const load = () => {
    setAsked(true);
    setErr(null);
    apiFetch(`/api/korea_research?symbol=${encodeURIComponent(symbol)}&window=max`, {
      noCache: true
    }).then(x => x.json()).then(x => x && x.ok ? setR(x) : setErr(x && x.error || "unavailable")).catch(e => setErr(String(e)));
    apiFetch("/api/korea_research/matrix?window=max", {
      noCache: true
    }).then(x => x.json()).then(setM).catch(() => {});
  };
  if (!asked) {
    return /*#__PURE__*/React.createElement("div", {
      className: "kl-research"
    }, /*#__PURE__*/React.createElement("div", {
      className: "gap-sechead",
      title: KL_TIP.research
    }, "Research \u2014 does Korea actually add anything?"), /*#__PURE__*/React.createElement("div", {
      className: "kl-research-note",
      title: KL_TIP.research
    }, "Whether Korea tells us anything the previous U.S. session did not, which way the information travels, whether it survives an Asian control, and whether any model beats plain KOSPI out of sample. Loaded on request because it re-fits every candidate model once per fold and takes a few seconds."), /*#__PURE__*/React.createElement("button", {
      className: "rr-btn kl-loadbtn",
      onClick: load,
      title: "Run the research layer for this ticker. It measures rather than recommends \u2014 nothing here is wired into the panel above."
    }, "Run the research"));
  }
  if (err) return /*#__PURE__*/React.createElement("div", {
    className: "kl-research"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, "Research unavailable: ", err));
  if (!r) return /*#__PURE__*/React.createElement("div", {
    className: "kl-research"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Measuring ", symbol, " against ten years of Asian sessions\u2026"));
  const inc = r.incremental || {};
  const ac = r.asia_control || {};
  const mc = r.model_comparison || {};
  const pl = r.placebo || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "kl-research"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.research
  }, "Research \xB7 ", r.symbol, " \xB7 ", r.n, " matched sessions", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", gapDate(r.first_date), " to ", gapDate(r.last_date))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.incremental
  }, "Does Korea add anything the previous U.S. session did not?"), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.incremental
  }, "The baseline model knows only what the U.S. did yesterday. The full model adds one Asian input. What matters is the CHANGE in R\xB2, not its level \u2014 a variable that only repeats what is already known scores a large correlation and adds nothing."), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-research-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which Asian series is being added to the baseline."
  }, "Asian input"), /*#__PURE__*/React.createElement("th", {
    title: "Signal inputs are what the panel uses. Research is Taiwan. The Nikkei is a control and is never promoted into a signal."
  }, "Role"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Matched sessions behind the row."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How much the U.S. opening gap moves per 1% of this Asian input, holding yesterday's U.S. session fixed."
  }, "Slope"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.incremental
  }, "Robust t"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "R\xB2 of the baseline: the previous U.S. session alone."
  }, "R\xB2 baseline"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "R\xB2 once the Asian input is added."
  }, "R\xB2 with Asia"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "The number that decides whether this is a signal or an echo."
  }, "R\xB2 added"))), /*#__PURE__*/React.createElement("tbody", null, ["kospi", "samsung", "hynix", "tsmc", "nikkei"].map(k => {
    const v = inc[k];
    if (!v) return null;
    const a = (v.added || []).find(x => x.name === k);
    return /*#__PURE__*/React.createElement("tr", {
      key: k
    }, /*#__PURE__*/React.createElement("td", {
      title: v.label
    }, v.label), /*#__PURE__*/React.createElement("td", {
      className: "muted"
    }, v.role), /*#__PURE__*/React.createElement("td", {
      className: "scan-num muted"
    }, v.ok ? v.base.n : "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, a ? a.beta.toFixed(3) : "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, a && a.t != null ? a.t.toFixed(2) : "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num muted"
    }, v.ok ? v.r2_base.toFixed(4) : "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, v.ok ? v.r2_full.toFixed(4) : "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num up"
    }, v.ok ? `+${v.delta_r2.toFixed(4)}` : "—"));
  })))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.asiacontrol
  }, "Is it Korea, or is it Asia? ", /*#__PURE__*/React.createElement(KlVerdict, {
    value: ac.verdict,
    tip: ac.detail
  })), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.asiacontrol
  }, ac.detail || KL_TIP.asiacontrol), ac.ok && /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-research-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which inputs are in the regression. All are fitted on one identical set of sessions."
  }, "Model"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Share of the opening gap's variation the model accounts for."
  }, "R\xB2"), /*#__PURE__*/React.createElement("th", {
    title: "Each input's robust t-statistic inside that model."
  }, "Inputs"))), /*#__PURE__*/React.createElement("tbody", null, Object.keys(ac.models || {}).map(k => /*#__PURE__*/React.createElement("tr", {
    key: k
  }, /*#__PURE__*/React.createElement("td", {
    title: KL_ASIA_MODEL_TIP[k] || ""
  }, KL_ASIA_MODEL[k] || k), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, ac.models[k].r2.toFixed(4)), /*#__PURE__*/React.createElement("td", {
    className: "muted"
  }, ac.models[k].params.map(pp => `${pp.name} t=${pp.t == null ? "—" : pp.t.toFixed(2)}`).join(" · "))))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.leadlag
  }, "Lead and lag"), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.leadlag
  }, r.lead_lag && r.lead_lag.kospi && r.lead_lag.kospi.note || ""), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-research-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which direction is being measured."
  }, "Direction"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Correlation over the matched sessions."
  }, "Correlation"), /*#__PURE__*/React.createElement("th", {
    title: "What that leg means."
  }, "What it means"))), /*#__PURE__*/React.createElement("tbody", null, ["kospi", "hynix"].map(k => {
    const ll = (r.lead_lag || {})[k];
    if (!ll || !ll.ok) return null;
    return (ll.legs || []).map(leg => /*#__PURE__*/React.createElement("tr", {
      key: k + leg.key
    }, /*#__PURE__*/React.createElement("td", null, leg.label), /*#__PURE__*/React.createElement("td", {
      className: `scan-num ${leg.r >= 0 ? "up" : "down"}`
    }, klCorr(leg.r)), /*#__PURE__*/React.createElement("td", {
      className: "muted"
    }, leg.meaning)));
  })))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.walkforward
  }, "Out of sample \u2014 did anything beat plain KOSPI?"), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.walkforward
  }, mc.note || KL_TIP.walkforward, " ", "Beat the baseline on both error and direction:", " ", /*#__PURE__*/React.createElement("b", null, mc.beats_baseline && mc.beats_baseline.length ? mc.beats_baseline.join(", ") : "nothing"), "."), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-research-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which inputs the model uses. Coefficients are re-fitted inside every fold on that fold's training data alone."
  }, "Model"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Out-of-sample predictions scored."
  }, "Scored"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Share of sessions where the predicted direction of the open was right."
  }, "Direction"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average size of the miss, in percentage points. Lower is better."
  }, "Average miss"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Brier score for the up/down call \u2014 lower is better, and 0.25 is a coin flip."
  }, "Brier"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Correlation between what the model predicted and what actually happened, out of sample."
  }, "Predicted vs actual"))), /*#__PURE__*/React.createElement("tbody", null, (mc.rows || []).map(row => /*#__PURE__*/React.createElement("tr", {
    key: row.model,
    className: row.model === mc.baseline ? "kl-row-on" : ""
  }, /*#__PURE__*/React.createElement("td", null, row.model, row.model === mc.baseline ? " · baseline" : ""), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, row.n), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, row.direction_pct == null ? "—" : klRate(row.direction_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, row.mae_pct.toFixed(4)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, row.brier.toFixed(4)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klCorr(row.pred_actual_corr))))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.placebo
  }, "Alignment placebo ", /*#__PURE__*/React.createElement(KlVerdict, {
    value: pl.verdict,
    tip: KL_TIP.placebo
  })), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.placebo
  }, "Correct same-day pairing ", /*#__PURE__*/React.createElement("b", null, klCorr(pl.correct)), (pl.placebos || []).map(x => /*#__PURE__*/React.createElement("span", {
    key: x.shift
  }, " \xB7 ", x.label, ": ", /*#__PURE__*/React.createElement("b", null, klCorr(x.pearson)))), pl.shuffled && /*#__PURE__*/React.createElement("span", null, " \xB7 ", pl.shuffled.draws, " random shuffles of Korea's own dates reached at most ", /*#__PURE__*/React.createElement("b", null, klCorr(pl.shuffled.max_abs)), ", and", " ", klRate(pl.shuffled.share_beating_correct_pct), " of them beat the real alignment.")), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.surprise
  }, "Korea surprise"), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.surprise
  }, KL_TIP.surprise), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-research-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which Korean series."
  }, "Input"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Sessions with a point-in-time surprise value."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "The raw series against this ticker's opening gap."
  }, "Raw"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "The same series with the previous U.S. semiconductor session subtracted out."
  }, "Surprise"))), /*#__PURE__*/React.createElement("tbody", null, Object.keys(r.surprise || {}).map(k => {
    const v = r.surprise[k];
    if (!v || !v.ok) return /*#__PURE__*/React.createElement("tr", {
      key: k
    }, /*#__PURE__*/React.createElement("td", null, k), /*#__PURE__*/React.createElement("td", {
      className: "scan-num muted",
      colSpan: 3
    }, v && v.reason));
    return /*#__PURE__*/React.createElement("tr", {
      key: k
    }, /*#__PURE__*/React.createElement("td", null, v.label), /*#__PURE__*/React.createElement("td", {
      className: "scan-num muted"
    }, v.n), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, klCorr(v.raw_pearson)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, klCorr(v.surprise_pearson)));
  })))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.convergence
  }, "Do gaps converge after the open?", " ", /*#__PURE__*/React.createElement(KlVerdict, {
    value: (r.convergence || {}).verdict,
    tip: KL_TIP.convergence
  })), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.convergence
  }, (r.convergence || {}).basis, r.convergence && r.convergence.ok && /*#__PURE__*/React.createElement("span", null, " Correlation ", /*#__PURE__*/React.createElement("b", null, klCorr(r.convergence.pearson)), ", robust t ", /*#__PURE__*/React.createElement("b", null, r.convergence.slope_t), " over ", r.convergence.n, " sessions.")), r.convergence && r.convergence.extremes && /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-research-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which extreme of the residual distribution."
  }, "Group"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Sessions in that extreme fifth."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Middle open-to-close outcome for that group."
  }, "Median after the open"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Share of that group that closed above its open."
  }, "Closed higher"))), /*#__PURE__*/React.createElement("tbody", null, r.convergence.extremes.map(g => /*#__PURE__*/React.createElement("tr", {
    key: g.group
  }, /*#__PURE__*/React.createElement("td", null, g.group), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, g.n), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(g.median_outcome_pct, 3)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klRate(g.share_positive_pct))))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.byyear
  }, "Year by year"), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-research-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Calendar year."
  }, "Year"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Matched sessions in that year."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "KOSPI against this ticker's opening gap, that year alone."
  }, "Correlation"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Share of that year's sessions that opened the same way Korea moved."
  }, "Same direction"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average opening gap that year."
  }, "Average gap"))), /*#__PURE__*/React.createElement("tbody", null, (r.by_year || []).map(y => /*#__PURE__*/React.createElement("tr", {
    key: y.year
  }, /*#__PURE__*/React.createElement("td", null, y.year), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, y.n), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${y.pearson >= 0 ? "up" : "down"}`
  }, klCorr(y.pearson)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klRate(y.same_direction_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(y.avg_y_pct, 2))))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.regime
  }, "Volatility regime"), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.regime
  }, (r.regime || {}).basis), r.regime && r.regime.ok && /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-research-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which half of the trailing-volatility split."
  }, "Regime"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Sessions in that half."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "KOSPI against the opening gap within that half."
  }, "Correlation"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Same-direction rate within that half."
  }, "Same direction"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Middle opening gap within that half."
  }, "Median gap"))), /*#__PURE__*/React.createElement("tbody", null, r.regime.groups.map(g => /*#__PURE__*/React.createElement("tr", {
    key: g.label
  }, /*#__PURE__*/React.createElement("td", null, g.label === "calm" ? "Calmer half" : "More volatile half"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, g.n), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klCorr(g.pearson)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klRate(g.same_direction_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(g.median_y_pct ?? g.median_gap_pct, 2))))))), m && m.cells && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: KL_TIP.matrix
  }, "Which Asian input predicts which U.S. ticker", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", m.n_significant, " of ", m.n_cells, " cells survive the multiple-testing correction")), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: KL_TIP.fdr
  }, m.note), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table kl-research-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which Asian series."
  }, "Asian input"), /*#__PURE__*/React.createElement("th", {
    title: "Signal, research, or control."
  }, "Role"), /*#__PURE__*/React.createElement("th", {
    title: "Which U.S. ticker's opening gap."
  }, "U.S. target"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Matched sessions."
  }, "Sessions"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.pearson
  }, "Correlation"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.spearman
  }, "Rank"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.match
  }, "Same direction"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: KL_TIP.fdr
  }, "q-value"))), /*#__PURE__*/React.createElement("tbody", null, m.cells.slice(0, 40).map(c => /*#__PURE__*/React.createElement("tr", {
    key: c.input + c.target,
    className: c.significant ? "kl-sig" : ""
  }, /*#__PURE__*/React.createElement("td", null, c.input_label), /*#__PURE__*/React.createElement("td", {
    className: "muted"
  }, c.role), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, c.target)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, c.n), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${c.pearson >= 0 ? "up" : "down"}`
  }, klCorr(c.pearson)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klCorr(c.spearman)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, klRate(c.same_direction_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, c.q == null ? "—" : c.q < 0.0001 ? "<0.0001" : c.q.toFixed(4)))))))), /*#__PURE__*/React.createElement("div", {
    className: "kl-research-note",
    title: "What produced these numbers."
  }, "Engine ", r.engine, " \xB7 settings ", r.config_hash, " \xB7 ", r.adjustment));
}
function KoreaLead({
  apiFetch,
  symbol,
  onSymbol
}) {
  const [win, setWin] = useState(() => {
    try {
      return localStorage.getItem(KL_WINDOW_KEY) || "1y";
    } catch (e) {
      return "1y";
    }
  });
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let dead = false;
    if (!symbol) return undefined;
    setD(null);
    setErr(null);
    apiFetch(`/api/korea_lead?symbol=${encodeURIComponent(symbol)}&window=${win}`, {
      noCache: true
    }).then(r => r.json()).then(x => {
      if (!dead) x && x.session ? setD(x) : setErr(x && x.error || "unavailable");
    }).catch(e => !dead && setErr(String(e)));
    return () => {
      dead = true;
    };
  }, [symbol, win]);
  const pickWin = w => {
    setWin(w);
    try {
      localStorage.setItem(KL_WINDOW_KEY, w);
    } catch (e) {/* private mode */}
  };
  const pickSym = s => {
    onSymbol && onSymbol(s);
    try {
      localStorage.setItem(KL_STORE_KEY, s);
    } catch (e) {/* private mode */}
  };
  const sess = d && d.session;
  const K = d && d.korea && d.korea.series || {};
  const conf = d && d.korea && d.korea.chip_confirmation || null;
  const og = d && d.opening_gap || null;
  const impl = og && og.implied;
  const dist = impl && impl.distribution;
  const pm = d && d.target && d.target.premarket;
  const cmp = d && d.premarket_comparison;
  const ao = d && d.after_open;
  const rel = d && d.relationship;
  const est = d && d.estimates;
  const res = d && d.residual;
  const drv = d && d.primary_driver;
  const chk = d && d.self_check;
  return /*#__PURE__*/React.createElement("div", {
    className: "kl-panel"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kl-head"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kl-title",
    title: KL_TIP.panel
  }, "KOREA LEAD", /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 overnight context for U.S. chips")), /*#__PURE__*/React.createElement("div", {
    className: "kl-controls"
  }, /*#__PURE__*/React.createElement("span", {
    className: "kl-ctl-label",
    title: KL_TIP.target
  }, "Target"), /*#__PURE__*/React.createElement("select", {
    className: "kl-select",
    value: symbol || "",
    title: KL_TIP.target,
    onChange: e => pickSym(e.target.value)
  }, (d && d.presets || []).map(s => /*#__PURE__*/React.createElement("option", {
    key: s,
    value: s
  }, s)), /*#__PURE__*/React.createElement("optgroup", {
    label: "Controls \u2014 not trade ideas"
  }, (d && d.controls || []).map(s => /*#__PURE__*/React.createElement("option", {
    key: s,
    value: s
  }, s))), symbol && !(d && d.presets || []).includes(symbol) && !(d && d.controls || []).includes(symbol) && /*#__PURE__*/React.createElement("option", {
    value: symbol
  }, symbol)), /*#__PURE__*/React.createElement("span", {
    className: "kl-ctl-label",
    title: KL_TIP.window
  }, "Lookback"), /*#__PURE__*/React.createElement("div", {
    className: "kl-wins"
  }, (d && d.windows || [{
    key: "60d",
    label: "60D"
  }, {
    key: "1y",
    label: "1Y"
  }, {
    key: "3y",
    label: "3Y"
  }, {
    key: "max",
    label: "MAX"
  }]).map(w => /*#__PURE__*/React.createElement("button", {
    key: w.key,
    className: `kl-win${w.key === win ? " on" : ""}`,
    title: `${w.label} — ${KL_TIP.window}`,
    onClick: () => pickWin(w.key)
  }, w.label))))), err && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, "Korea Lead unavailable: ", err), !d && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Reading Korea\u2026"), d && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kl-ctxline muted",
    title: KL_TIP.session
  }, "Seoul ", sess && sess.seoul_time, " \xB7", " ", /*#__PURE__*/React.createElement("b", {
    className: sess && sess.scheduled_state === "SESSION IN PROGRESS" ? "warn" : ""
  }, sess && sess.scheduled_state), sess && sess.data_state && /*#__PURE__*/React.createElement("span", {
    title: `${sess.final_reason || ""}\n\n${KL_TIP.sessiondata}`
  }, " · Korean data ", /*#__PURE__*/React.createElement("b", {
    className: sess.data_state === "SETTLED" ? "up" : sess.data_state === "STILL UPDATING" ? "warn" : ""
  }, sess.data_state)), " ", "\xB7 Korean session ", gapDate(d.korea && d.korea.as_of), " ", "\xB7 read ", gapWhen(d.as_of)), sess && sess.scheduled_state === "AFTER NORMAL CLOSE" && sess.data_state === "STILL UPDATING" && /*#__PURE__*/React.createElement("div", {
    className: "kl-flag",
    title: `${sess.final_reason || ""}\n\n${KL_TIP.sessiondata}`
  }, "\u26A0 KOREA STILL TRADING PAST ITS NORMAL CLOSE \u2014 today's Korean numbers are not final"), d.korea && d.korea.unusual && d.korea.unusual.any && /*#__PURE__*/React.createElement("div", {
    className: "kl-unusual",
    title: KL_TIP.unusual
  }, "\u26A1 ", d.korea.unusual.headline, " \u2014 ", d.korea.unusual.detail), d.korea && d.korea.signal && !d.korea.signal.ok && /*#__PURE__*/React.createElement("div", {
    className: "kl-nosignal",
    title: KL_TIP.nodata
  }, "\u26A0 No Korean signal for this session. ", d.korea.signal.reason), /*#__PURE__*/React.createElement("div", {
    className: "kl-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kl-box"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt",
    title: KL_TIP.session
  }, "What Korea did"), [["kospi", KL_TIP.kospi], ["samsung", KL_TIP.samsung], ["hynix", KL_TIP.hynix], ["usdkrw", KL_TIP.usdkrw]].map(([k, tip]) => /*#__PURE__*/React.createElement(KlSeries, {
    key: k,
    s: K[k],
    tip: tip,
    sessionDate: d.korea && d.korea.as_of
  })), /*#__PURE__*/React.createElement("div", {
    className: "kl-confirm",
    title: KL_TIP.confirm
  }, /*#__PURE__*/React.createElement("span", null, "Chip confirmation"), /*#__PURE__*/React.createElement(KlPill, {
    text: conf && conf.state,
    tone: conf && KL_CONFIRM_TONE[conf.state],
    tip: conf && conf.detail || KL_TIP.confirm
  }))), /*#__PURE__*/React.createElement("div", {
    className: "kl-box"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt",
    title: KL_TIP.bias
  }, "Opening gap bias", /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 ", symbol)), !og || !og.bias ? /*#__PURE__*/React.createElement("div", {
    className: "research-empty",
    title: KL_TIP.nodata
  }, d.error || "No opening-gap history for this ticker yet.") : /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(KlPill, {
    text: og.bias.state,
    tone: KL_BIAS_TONE[og.bias.state],
    tip: og.bias.detail || KL_TIP.bias
  }), /*#__PURE__*/React.createElement(KlStat, {
    label: "Historical match rate",
    tip: KL_TIP.match
  }, /*#__PURE__*/React.createElement(KlMatch, {
    sd: impl && impl.same_direction
  })), /*#__PURE__*/React.createElement(KlStat, {
    label: "Matched bucket",
    tip: impl && impl.label ? `Today's Korean move falls in the bucket "${impl.label}". ${KL_TIP.implied}` : KL_TIP.implied
  }, impl && impl.label || "—"), /*#__PURE__*/React.createElement(KlStat, {
    label: "Korea implied gap \xB7 median",
    tip: KL_TIP.implied,
    tone: dist && dist.median_pct >= 0 ? "up" : "down"
  }, gapPct(dist && dist.median_pct, 2)), /*#__PURE__*/React.createElement(KlStat, {
    label: "Typical range",
    tip: KL_TIP.implied
  }, dist ? `${gapPct(dist.p25_pct, 2)} to ${gapPct(dist.p75_pct, 2)}` : "—"), est && est.regression && est.regression.ok && /*#__PURE__*/React.createElement(KlStat, {
    label: "Fitted-line estimate",
    tip: KL_TIP.regression,
    tone: est.regression.expected_pct >= 0 ? "up" : "down"
  }, gapPct(est.regression.expected_pct, 2), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 n=", est.regression.n)), est && est.regression && est.regression.band50 && /*#__PURE__*/React.createElement(KlStat, {
    label: "Where its errors usually landed",
    tip: KL_TIP.band
  }, gapPct(est.regression.band50[0], 2), " to ", gapPct(est.regression.band50[1], 2), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 half the time")), est && est.agreement && est.agreement.state === "MODEL DISAGREEMENT" && /*#__PURE__*/React.createElement("div", {
    className: "kl-flag",
    title: est.agreement.detail || KL_TIP.disagree
  }, "\u26A0 MODEL DISAGREEMENT"), impl && !impl.usable && impl.reason && /*#__PURE__*/React.createElement("div", {
    className: "gap-note",
    title: KL_TIP.match
  }, impl.reason), og.edge && /*#__PURE__*/React.createElement("div", {
    className: "gap-note",
    title: og.edge.detail || KL_TIP.bias
  }, "Evidence: ", og.edge.state))), /*#__PURE__*/React.createElement("div", {
    className: "kl-box"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt",
    title: KL_TIP.premarket
  }, "U.S. premarket vs Korea"), /*#__PURE__*/React.createElement(KlStat, {
    label: `${symbol} ${pm && pm.basis === "official_open" ? "opening gap" : "premarket"}`,
    tip: pm && pm.basis_label ? `This is the ${pm.basis_label}. ${KL_TIP.premarket}` : KL_TIP.premarket,
    tone: pm && pm.ok && pm.fresh_enough ? pm.gap_pct >= 0 ? "up" : "down" : ""
  }, pm && pm.ok && pm.fresh_enough ? gapPct(pm.gap_pct, 2) : /*#__PURE__*/React.createElement("span", {
    className: "muted",
    title: `${pm && (pm.not_available_reason || pm.error) || "No live quote."}\n\n${KL_TIP.premarketStale}`
  }, "NOT AVAILABLE YET")), pm && pm.ok && pm.fresh_enough && pm.mid_gap_pct != null && /*#__PURE__*/React.createElement(KlStat, {
    label: "Bid/ask midpoint gap",
    tip: `Shown beside the traded gap, never substituted for it. ${KL_TIP.premarket}`
  }, gapPct(pm.mid_gap_pct, 2), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 for comparison")), /*#__PURE__*/React.createElement(KlStat, {
    label: "Residual",
    tip: KL_TIP.residual
  }, cmp && cmp.residual_pct != null ? `${gapPct(cmp.residual_pct, 2)} pts` : "—"), /*#__PURE__*/React.createElement(KlStat, {
    label: "Confirming or diverging",
    tip: cmp && cmp.detail || KL_TIP.cmp
  }, /*#__PURE__*/React.createElement(KlPill, {
    text: cmp && cmp.state,
    tone: cmp && KL_CMP_TONE[cmp.state],
    tip: cmp && cmp.detail || KL_TIP.cmp
  })), cmp && cmp.share_shown && /*#__PURE__*/React.createElement(KlStat, {
    label: "Share of the matched gap already covered",
    tip: KL_TIP.share
  }, klRate(cmp.share_pct)), res && res.ok && /*#__PURE__*/React.createElement(KlStat, {
    label: "Against this pair's own residual history",
    tip: res.detail || KL_TIP.residualPct
  }, res.label, /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 bigger than ", klRate(res.percentile, 0), " of ", res.n)), cmp && !cmp.share_shown && cmp.state !== "UNAVAILABLE" && /*#__PURE__*/React.createElement("div", {
    className: "gap-note",
    title: KL_TIP.cmp
  }, cmp.detail)), /*#__PURE__*/React.createElement("div", {
    className: "kl-box"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt",
    title: KL_TIP.relationship
  }, "Relationship strength"), !rel || !rel.ok ? /*#__PURE__*/React.createElement("div", {
    className: "research-empty",
    title: KL_TIP.relationship
  }, rel && rel.reason || "Not enough matched history yet.") : /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(KlPill, {
    text: rel.health && rel.health.state,
    tone: rel.health && KL_HEALTH_TONE[rel.health.state],
    tip: rel.health && rel.health.detail || KL_TIP.health
  }), rel.health && rel.health.state === "UNSTABLE" && /*#__PURE__*/React.createElement("div", {
    className: "kl-flag",
    title: KL_TIP.unstable
  }, "\u26A0 RELATIONSHIP UNSTABLE"), /*#__PURE__*/React.createElement(KlStat, {
    label: "Last 60 sessions",
    tip: KL_TIP.relationship,
    tone: rel.recent && rel.recent.r >= 0 ? "up" : "down"
  }, klCorr(rel.recent && rel.recent.r), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 n=", (rel.recent || {}).n)), /*#__PURE__*/React.createElement(KlStat, {
    label: "Last year",
    tip: KL_TIP.relationship,
    tone: rel.long && rel.long.r >= 0 ? "up" : "down"
  }, klCorr(rel.long && rel.long.r), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 n=", (rel.long || {}).n)), /*#__PURE__*/React.createElement("div", {
    className: "kl-sparkrow",
    title: KL_TIP.relationship
  }, /*#__PURE__*/React.createElement("span", null, "Recent trend"), /*#__PURE__*/React.createElement(KlSpark, {
    points: rel.spark
  })), /*#__PURE__*/React.createElement(KlStat, {
    label: "Primary Korea driver",
    tip: `${drv && drv.detail || ""}\n\n${KL_TIP.driver}`
  }, drv && drv.driver ? /*#__PURE__*/React.createElement("span", null, KL_DRIVER_LABEL[drv.driver] || drv.driver, /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 out of sample")) : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, drv && drv.verdict || "NOT YET EVALUATED")))), /*#__PURE__*/React.createElement("div", {
    className: "kl-box kl-box-after"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt",
    title: KL_TIP.afteropen
  }, "After open edge", /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7 9:30 to 4:00")), /*#__PURE__*/React.createElement(KlPill, {
    text: ao && ao.edge && ao.edge.state,
    tone: ao && ao.edge && KL_EDGE_TONE[ao.edge.state],
    tip: ao && ao.edge && ao.edge.detail || KL_TIP.afteropen
  }), /*#__PURE__*/React.createElement(KlStat, {
    label: "Correlation",
    tip: KL_TIP.pearson
  }, klCorr(ao && ao.stats && ao.stats.pearson)), /*#__PURE__*/React.createElement(KlStat, {
    label: "Rank correlation",
    tip: KL_TIP.spearman
  }, klCorr(ao && ao.stats && ao.stats.spearman)), /*#__PURE__*/React.createElement(KlStat, {
    label: "Same direction",
    tip: KL_TIP.match
  }, /*#__PURE__*/React.createElement(KlMatch, {
    sd: ao && ao.stats && ao.stats.same_direction
  })), /*#__PURE__*/React.createElement("div", {
    className: "gap-note",
    title: KL_TIP.afteropen
  }, ao && ao.note))), chk && !chk.ok && /*#__PURE__*/React.createElement("div", {
    className: "kl-flag",
    title: KL_TIP.selfcheck
  }, "\u26A0 DEGRADED \u2014 ", chk.detail), /*#__PURE__*/React.createElement("div", {
    className: "kl-footline"
  }, /*#__PURE__*/React.createElement("button", {
    className: "rr-btn kl-more",
    onClick: () => setOpen(!open),
    title: "Open the full statistics: every lookback, both correlations, the Korean chip names measured separately, the bucket tables, and where each series came from."
  }, open ? "Hide details" : "Details"), /*#__PURE__*/React.createElement("span", {
    className: "gap-note kl-inline-note",
    title: "Korea Lead measures a historical relationship. A relationship is not a cause and a match rate is not a probability \u2014 both are stated with the number of sessions behind them so they can be judged."
  }, d.target && d.target.n || 0, " matched sessions \xB7", " ", d.window_label, " \xB7 ", d.target && d.target.is_control ? "CONTROL TICKER — read the tooltip before trading it" : "history, not a forecast")), open && /*#__PURE__*/React.createElement(KlDetails, {
    d: d
  }), open && /*#__PURE__*/React.createElement(KlForward, {
    apiFetch: apiFetch,
    symbol: symbol
  }), open && /*#__PURE__*/React.createElement(KlResearch, {
    apiFetch: apiFetch,
    symbol: symbol,
    window: win
  })));
}

// ── main tab ────────────────────────────────────────────────────────────────

function GapTab({
  apiFetch,
  onOpenTicker
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [gapSym, setGapSym] = useState(null);
  const [sortK, setSortK] = useState("rank");
  const [sortD, setSortD] = useState(1);
  const [live, setLive] = useState({});
  const [liveAt, setLiveAt] = useState(null);
  const pollRef = useRef(null);
  // Which ticker the Korea Lead panel is measuring. It remembers the last
  // choice, and opening a stock's evidence below points it at that stock —
  // the intended reading order is Korea first, then this morning's movers.
  const [klSym, setKlSym] = useState(() => {
    try {
      return localStorage.getItem(KL_STORE_KEY) || "SMH";
    } catch (e) {
      return "SMH";
    }
  });
  useEffect(() => {
    if (gapSym) setKlSym(gapSym);
  }, [gapSym]);
  const load = async () => {
    try {
      const r = await apiFetch("/api/gap", {
        noCache: true
      });
      const d = await r.json();
      setBoard(d);
      setErr(null);
      return d;
    } catch (e) {
      setErr(String(e));
      return null;
    }
  };
  const watchScan = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (d && !(d.status && d.status.scanning)) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 4000);
  };
  useEffect(() => {
    load().then(d => {
      if (!d) return;
      if (d.status && d.status.scanning) {
        watchScan();
        return;
      }
      const age = d.as_of ? Date.now() - new Date(d.as_of).getTime() : Infinity;
      if (!d.rows || !d.rows.length || age > 20 * 60000) {
        apiFetch("/api/gap/scan").catch(() => {});
        watchScan();
      }
    });
    const iv = setInterval(skipWhenHidden(load), 60 * 1000);
    return () => {
      clearInterval(iv);
      pollRef.current && clearInterval(pollRef.current);
    };
  }, []);

  // Live price ticker: one batched quote call, prices only. A full scan is
  // expensive and runs every few minutes, so without this the board would
  // show the price frozen at the last scan while the stock keeps moving.
  useEffect(() => {
    const tick = async () => {
      try {
        const r = await apiFetch("/api/gap/live", {
          noCache: true
        });
        const d = await r.json();
        if (d && d.ok && d.quotes) {
          setLive(d.quotes);
          setLiveAt(d.as_of);
        }
      } catch (e) {/* keep the last known price */}
    };
    tick();
    const iv = setInterval(skipWhenHidden(tick), 15 * 1000);
    return () => clearInterval(iv);
  }, []);
  const rows = useMemo(() => {
    const base = board && board.rows || [];
    if (!Object.keys(live).length) return base;
    return base.map(r => live[r.symbol] ? {
      ...r,
      ...live[r.symbol]
    } : r);
  }, [board, live]);
  const sorted = useMemo(() => {
    const key = r => {
      switch (sortK) {
        case "symbol":
          return r.symbol || "";
        case "gap":
          return -Math.abs(r.pm_gap_pct ?? 0);
        case "p2":
          return -((r.p_fav && r.p_fav.p) ?? -1);
        case "tbs":
          return -(r.tbs_p ?? -1);
        case "adv":
          return r.med_adverse_pct ?? 99;
        case "cat":
          return r.catalyst_kind === "UNTAGGED" ? "zzz" : r.catalyst_kind || "zzz";
        case "n":
          return -(r.n ?? 0);
        default:
          return 0;
        // rank = server order
      }
    };
    const s = [...rows];
    if (sortK !== "rank") s.sort((a, b) => {
      const ka = key(a),
        kb = key(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * sortD;
    });
    return s;
  }, [rows, sortK, sortD]);
  const [shown, moreControls] = useBoundedList(sorted, 40, 60);
  const th = (label, k, tip, hideMobile) => /*#__PURE__*/React.createElement("th", {
    className: `${k === "symbol" ? "" : "scan-th-num"}${hideMobile ? " gap-hidemobile" : ""}`,
    title: tip,
    onClick: () => {
      if (sortK === k) setSortD(-sortD);else {
        setSortK(k);
        setSortD(1);
      }
    }
  }, label, sortK === k ? sortD === 1 ? " ↓" : " ↑" : "");
  const scanning = board && board.status && board.status.scanning;
  const ctx = board && board.context || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "card gap-card"
  }, /*#__PURE__*/React.createElement(KoreaLead, {
    apiFetch: apiFetch,
    symbol: klSym,
    onSymbol: setKlSym
  }), gapSym ? /*#__PURE__*/React.createElement(GapDetail, {
    apiFetch: apiFetch,
    sym: gapSym,
    liveQ: live[gapSym],
    onClose: () => setGapSym(null),
    onOpenTicker: onOpenTicker
  }) : /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Gap Scan \xB7 premarket fade & rebound"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "When this stock moved like this before the open, what happened next \u2014 measured history, not \"gaps usually fill\".")), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    disabled: !!scanning,
    title: "Re-scan premarket movers now (quote sweep \u2192 minute history \u2192 same-ticker gap statistics).",
    onClick: () => {
      apiFetch("/api/gap/scan?force=1").catch(() => {});
      watchScan();
    }
  }, scanning ? `Scanning ${board.status.scanned}/${board.status.total || "…"}` : "Scan now")), err && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, err), board && board.status && board.status.error && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, "last scan failed: ", board.status.error), !board && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Loading board\u2026"), board && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-ctxline muted"
  }, board.session === "premarket" ? "premarket" : "market hours", " \xB7 SPY ", gapPct(ctx.spy_gap_pct), " \xB7 QQQ ", gapPct(ctx.qqq_gap_pct), " \xB7", " ", /*#__PURE__*/React.createElement("span", {
    className: "gap-livedot",
    title: "Prices refresh every 15 seconds; the statistics update on each full scan."
  }), "prices live ", gapTime(liveAt || board.price_as_of), " \xB7 statistics as of ", gapWhen(board.as_of)), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table gap-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, th("Ticker", "symbol", "The premarket mover. Click any row to open the full evidence: every historical gap, the news, and the target/stop grid. The colored dot shows how closely today's setup matches the historical examples — green is a close match, grey is a loose one."), th("Price", "rank", "Current premarket price. Refreshes every 15 seconds.", true), th("Premarket gap", "gap", "How far the current price is from yesterday's closing price. This is the LIVE gap and it keeps moving until 9:30 — it is not the official opening gap the history is measured from."), th("Off high/low", "rank", "For a gap up: how far the price has already pulled back from the highest premarket price so far. For a gap down: how far it has bounced off the low. Note 'so far' — the final premarket high doesn't exist until 9:30.", true), th("Catalyst", "cat", "What is known to be driving the move, from real data only: Earnings, a Buyout or other deal, an FDA approval (green) or rejection (red) read out of the company's own 8-K, a trial readout, Bankruptcy, a Delisting notice, a Guidance change, an Offering or Dilution filing straight from SEC EDGAR (red — the company is selling or registering stock), a Short-seller report or Index change (these two come from headlines, since nobody files them, and say so), an analyst Upgrade or Downgrade (green/red — hover a cell for the firm and grade change), another Analyst action such as an initiation or target change, or a Macro event day. 'None tagged' means none of those were found, NOT that nothing happened. Only earnings gaps are separated into their own statistical population; the rest is context. Click to group by catalyst.", true), th("Fades 2%", "p2", "How often this stock's comparable past gaps moved at least 2% in the profitable direction from the opening price — down for a gap up, up for a gap down. The small number after the dot is how many historical examples that rate is based on. Hover the value for the conservative range."), th("Hits target first", "tbs", "How often the 2% profit target printed BEFORE a 3% stop would have been hit, measured minute by minute on real historical paths. A dash means only daily bars exist for those days — daily bars show how far a stock moved but not in what order, so no honest claim is made."), th("Squeeze / flush", "adv", "The typical move AGAINST the trade before it resolved: for a gap up that means the squeeze higher, for a gap down the flush lower. A gap that eventually fades but squeezes 4% higher first is not a comfortable short. Each row's arrow shows which way.", true), th("Examples", "n", "How many comparable historical events back these numbers. No sample size, no probability — a rate from 3 events is not a rate.", true), th("Signal", "rank", "The call. STRONG requires the favorable rate AND the target-before-stop rate AND controlled tail risk AND enough examples — all judged on the conservative end of each range, never on one flattering number. NO DATA means the evidence or the live quote isn't good enough to say anything."))), /*#__PURE__*/React.createElement("tbody", null, shown.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.symbol,
    className: "scan-row gap-row",
    onClick: () => r.data_ok !== false && setGapSym(r.symbol),
    title: r.what_changed || r.signal_why || r.error || ""
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.symbol), " ", /*#__PURE__*/React.createElement(GapQualityDot, {
    q: r.cohort_quality
  })), /*#__PURE__*/React.createElement("td", {
    className: "scan-num gap-hidemobile"
  }, r.price != null ? `$${gapNum(r.price, 2)}` : "—"), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${(r.pm_gap_pct ?? 0) >= 0 ? "up" : "down"}`
  }, /*#__PURE__*/React.createElement("b", null, gapPct(r.pm_gap_pct))), /*#__PURE__*/React.createElement("td", {
    className: "scan-num gap-hidemobile"
  }, gapPct(r.direction === "up" ? r.from_pm_high_pct : r.from_pm_low_pct)), /*#__PURE__*/React.createElement("td", {
    className: `gap-hidemobile ${GAP_CATALYST_TONE[r.catalyst_kind] ? "gap-cat-" + GAP_CATALYST_TONE[r.catalyst_kind] : ""}`,
    title: (r.catalyst_label || r.catalyst_quote ? `${gapCatalyst(r.catalyst_kind)} — ${r.catalyst_quote || r.catalyst_label}` + (r.catalyst_evidence === "headline" ? `\n(headline${r.catalyst_source ? ", " + r.catalyst_source : ""} — not a filing)` : "") + "\n\n" : "") + (r.catalyst_warning ? `⚠ ${r.catalyst_warning}\n\n` : "") + (GAP_CATALYST_TIP[r.catalyst_kind] || "")
  }, r.catalyst_kind === "UNTAGGED" ? /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014") : gapCatalyst(r.catalyst_kind)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, /*#__PURE__*/React.createElement(GapProb, {
    r: r.p_fav
  })), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, r.tbs_p != null ? `${Math.round(r.tbs_p)}%` : /*#__PURE__*/React.createElement("span", {
    className: "muted",
    title: "UNKNOWN / DAILY ONLY \u2014 no minute paths yet for this cohort"
  }, "\u2014")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num gap-hidemobile",
    title: r.med_adverse_pct == null ? "" : `Typically ${r.direction === "up" ? "squeezes" : "flushes"} ${gapPct(r.med_adverse_pct)} against the trade first`
  }, gapPct(r.med_adverse_pct), r.med_adverse_pct != null && /*#__PURE__*/React.createElement("span", {
    className: "gap-arrow"
  }, " ", r.direction === "up" ? "↑" : "↓")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num gap-hidemobile muted"
  }, r.n ?? 0), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(GapSigPill, {
    signal: r.signal,
    held: r.signal_held
  }))))))), moreControls, !rows.length && !scanning && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, board.as_of ? "No premarket movers past the gap threshold right now — the board fills when stocks actually gap. Auto-scans run every few minutes from 7:00 AM ET." : "No scan yet — hit “Scan now”. Most useful 7:00–9:30 AM ET when premarket movers exist."), board.note && /*#__PURE__*/React.createElement("div", {
    className: "gap-note"
  }, board.note))));
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  GapTab: React.memo(GapTab)
});
})();
