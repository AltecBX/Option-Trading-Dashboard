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
  }, gapSym ? /*#__PURE__*/React.createElement(GapDetail, {
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
