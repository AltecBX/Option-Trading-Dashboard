(function () {
// tab-setup.jsx — LAZY CHUNK. The Best Setup card: one explained
// recommendation built from every layer the app already computes, so the
// answer arrives without visiting five tabs and combining them by hand.
//
// The card's job is not to look confident. It is to show the trade AND the
// evidence behind it, including the evidence that is missing — because the
// one thing that would make this feature dangerous is a recommendation to
// sell closer to the money that reads exactly like a well-supported one.
//
// Endpoint: GET /api/setup?symbol=X

const SU_TIP = {
  action: "The side, expiration, strike and delta the evidence supports, from every layer the app already computes — weekly range, streaks, swing maturity, premium, implied volatility, probability, liquidity and gamma exposure.",
  negative_ev: "The best contract the evidence allows pays less than it is worth at the volatility this stock actually realizes. A high probability of keeping the credit is not the same as a profitable trade: sold repeatedly at this price it loses money, because the occasional loss is bigger than all the credits it took to get there. Nothing is recommended rather than the least-bad option.",
  veto: "Refused on dealer positioning: price sits below the gamma flip AND the strike sits in negative gamma nearby, which is the arrangement where an adverse move accelerates instead of fading. Gamma never opens a trade here, but it is allowed to stop one.",
  no_trade: "No contract on this expiration satisfies both the delta band and the measured distance the evidence supports. Saying nothing is the honest answer when the ladder has nothing that qualifies.",
  no_trade_today: "Both sides were examined and neither produced a sale worth making. This is a finding, not a gap in the data — the reason below names the contract that came closest on each side and why it was turned down. Most days on most symbols there is no edge, and a card that always finds one is not measuring anything.",
  credit: "The credit at the BID, not the mid. The bid is the only price a resting sell order is actually promised.",
  premium: "The credit as a percentage of the collateral this trade ties up.",
  annual: "That premium rate extended to a full year. A comparison figure, not a forecast — it assumes you keep finding the same trade.",
  keep: "How often this strike finishes out of the money, at the volatility this stock actually realizes rather than the volatility the market is charging for.",
  ev: "Credit at the bid, minus what the contract is worth at the volatility this stock actually realizes, minus costs. Positive means the market is paying more than the risk is worth.",
  tail: "In the worst 5% of outcomes, the average loss. Not the worst case — the average of the bad cases.",
  breakeven: "The price at which this trade stops making money at expiration.",
  collateral: "The capital this position ties up.",
  spread: "The bid-ask spread as a percentage of the mid. A wide market is expensive to manage even when the credit looks good.",
  band: "The delta range the evidence supports. It stays at the conservative default unless a measured sample says this stock, in this state, travels less far than the market is pricing.",
  raised: "The measured evidence supports selling nearer the money than the default. The distance floor below is the promise being made — the strike must sit at least that far out.",
  notraised: "No measured evidence supports selling nearer the money, so this is the conservative default strike rather than an optimised one.",
  floor: "How far out of the money the strike must sit for the MEASURED keep rate to clear the target, on the conservative (lower-bound) reading of the sample.",
  conditioning: "Which past bars counted as 'this state'. The rule is fixed in advance and shown here, because choosing it after seeing which answer is better would be picking the winner and calling it evidence.",
  confidence: "How much of this recommendation is actually supported. It falls when the layers disagree, when the sample is thin, when gamma positioning argues the other way, or when the contract cannot be traded well.",
  gex: "Gamma exposure is never a reason to take this trade. It can leave it alone, pull the strike back when the path runs through negative gamma, or refuse it outright.",
  iv30: "Constant-maturity 30-day implied volatility — what the market is charging.",
  erv: "The volatility this stock is forecast to actually realize. The gap between this and implied volatility is where premium selling makes money.",
  vrp: "Implied volatility minus expected realized volatility, in points. Positive means options are priced above what the stock is likely to do.",
  measured: "How often price actually travelled each distance within the life of this option — in this state, and from any ordinary bar for comparison. The keep rate is shown on its conservative lower bound.",
  baseline: "The same question asked of every ordinary bar. The conditional rate has to beat this, or the state is not special.",
  alt: "The other side, for comparison. It is shown so the choice is visible rather than assumed.",
  // ── the board ─────────────────────────────────────────────────────────
  board: "What is worth SELLING today, ranked by how rich each option is against what that stock itself realizes — not by how many dollars it pays, which mostly just tracks how volatile the stock is.",
  richness: "Where today's premium sits against this stock's OWN past premiums. 90 means richer than 90% of the readings on file. When too few readings exist for a percentile, it falls back to the raw ratio and the row says which.",
  rich_basis: "Which measurement the ranking used. PERCENTILE means enough of this stock's own history is on file to say where today sits in it. RATIO means there is not yet, so this is simply how many times over the option pays what the stock realizes.",
  roc: "The credit as a percentage of the collateral the trade ties up — what the money actually earns, independent of share price.",
  board_skip: "Names the scan measured and then refused, with the reason. A short list is only trustworthy if you can see what did not make it and why.",
  universe: "How many names were ranked for free against how many had their option chain actually measured. Every chain costs a network round trip, so the scan ranks everything and measures the best few.",
  expiry: "The expiration this credit and return are quoted for — the one the premium engine judged richest inside the selling window, not automatically the nearest monthly.",
  board_earn: "Earnings inside the option's life excludes a name here. That is the opposite of the Premium Edge scan, which seeks earnings out — because a trader who closes before the report harvests that premium, and one who holds to expiry underwrites it."
};
const suNum = (v, d = 2) => v == null || !isFinite(v) ? "—" : Number(v).toFixed(d);
const suPct = (v, d = 1) => v == null || !isFinite(v) ? "—" : `${Number(v).toFixed(d)}%`;
const suMoney = (v, d = 2) => v == null || !isFinite(v) ? "—" : `$${Number(v).toFixed(d)}`;
const suSigned = (v, d = 0) => v == null || !isFinite(v) ? "—" : `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(d)}`;
// House rule: dates read "September 18, 2026".
const suDate = s => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric"
  });
};
const suTime = s => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit"
  });
};
const SU_CONF_TONE = {
  HIGH: "up",
  MODERATE: "warn",
  LOW: "down",
  WEAK: "down"
};
function SuStat({
  label,
  value,
  tip,
  tone
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "su-stat",
    title: tip
  }, /*#__PURE__*/React.createElement("span", {
    className: "su-stat-label"
  }, label), /*#__PURE__*/React.createElement("span", {
    className: `su-stat-value ${tone || ""}`
  }, value));
}

// The measured evidence, as a table. This is the part that decides whether
// the strike may move nearer the money, so it is shown in full rather than
// summarised into a verdict.
function SuMeasured({
  measured,
  floorPct
}) {
  const rows = measured && measured.rows || [];
  if (!rows.length) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty"
    }, measured && measured.reason || "No measured history for how far this stock travels in this state.");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "st-scroll"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table su-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "How far out of the money, as a percentage of the current price."
  }, "Distance"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SU_TIP.measured
  }, "Reached it"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SU_TIP.baseline
  }, "From any bar"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How much better this state is than the stock's ordinary behaviour, in percentage points."
  }, "Difference"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Share of windows in which price did NOT travel this far \u2014 the seller's win rate."
  }, "Kept"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The conservative lower bound of that keep rate. This is the number the strike is chosen on, never the point estimate."
  }, "Kept, low bound"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How many windows this rate is measured over."
  }, "Windows"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => {
    const meets = floorPct != null && r.distance_pct >= floorPct;
    return /*#__PURE__*/React.createElement("tr", {
      key: r.distance_pct,
      className: meets ? "su-row-meets" : "",
      title: meets ? "At or beyond the distance the evidence requires." : undefined
    }, /*#__PURE__*/React.createElement("td", {
      className: "scan-sym"
    }, suPct(r.distance_pct)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, suPct(r.touch_pct)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num muted"
    }, suPct(r.baseline_touch_pct)), /*#__PURE__*/React.createElement("td", {
      className: `scan-num ${(r.edge_points || 0) > 0 ? "up" : ""}`
    }, r.edge_points == null ? "—" : `${r.edge_points > 0 ? "+" : r.edge_points < 0 ? "−" : ""}` + Math.abs(r.edge_points).toFixed(1)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, suPct(r.keep_pct)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num su-strong"
    }, suPct(r.keep_pct_low)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num muted"
    }, r.n));
  }))));
}
function SuRecommendation({
  rec,
  data
}) {
  if (!rec) return null;
  if (!rec.ok) {
    return /*#__PURE__*/React.createElement("div", {
      className: "su-refused"
    }, /*#__PURE__*/React.createElement("b", {
      title: rec.negative_ev ? SU_TIP.negative_ev : rec.vetoed ? SU_TIP.veto : SU_TIP.no_trade
    }, rec.vetoed ? "Refused on market structure." : rec.negative_ev ? "No premium sale worth making here." : "No trade here."), " ", rec.reason);
  }
  const conf = rec.confidence || {};
  const ceil = rec.ceiling || {};
  const raised = !!rec.band_raised;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "su-headline"
  }, /*#__PURE__*/React.createElement("div", {
    className: "su-action",
    title: SU_TIP.action
  }, /*#__PURE__*/React.createElement("span", {
    className: `su-side su-side-${rec.side}`
  }, rec.action), /*#__PURE__*/React.createElement("span", {
    className: "su-strike",
    title: "The strike price to sell."
  }, suNum(rec.strike, 2)), /*#__PURE__*/React.createElement("span", {
    className: "su-delta",
    title: "The market's own delta for this strike \u2014 its estimate of the chance it finishes in the money."
  }, suNum(Math.abs(rec.delta), 2), " delta")), /*#__PURE__*/React.createElement("div", {
    className: "su-expiry"
  }, /*#__PURE__*/React.createElement("span", {
    title: "The expiration this recommendation is for. Chosen as the richest tenor inside the selling window, never a fixed 30 days."
  }, suDate(rec.expiration)), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", suNum(rec.dte, 0), " days")), /*#__PURE__*/React.createElement("span", {
    className: `su-conf su-conf-${SU_CONF_TONE[conf.label] || "mut"}`,
    title: SU_TIP.confidence + (conf.reasons && conf.reasons.length ? " " + conf.reasons.join(" ") : "")
  }, conf.label, " confidence")), /*#__PURE__*/React.createElement("div", {
    className: `su-band ${raised ? "su-band-raised" : ""}`,
    title: raised ? SU_TIP.raised : SU_TIP.notraised
  }, raised ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("b", null, "The evidence supports selling nearer the money."), " ", "Any strike at least ", /*#__PURE__*/React.createElement("b", null, suPct(ceil.min_distance_pct)), " out qualifies \u2014 measured over ", ceil.n, " windows in this state, price stayed inside that distance ", suPct(ceil.keep_pct_low, 0), " of the time on the conservative bound.") : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("b", null, "Conservative default strike."), " ", ceil.why || "No measured evidence supports selling nearer the money.")), /*#__PURE__*/React.createElement("div", {
    className: "su-stats"
  }, /*#__PURE__*/React.createElement(SuStat, {
    label: "Credit",
    value: suMoney(rec.credit),
    tip: SU_TIP.credit,
    tone: "up"
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Premium on collateral",
    value: suPct(rec.premium_pct_collateral, 2),
    tip: SU_TIP.premium
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Annualized",
    value: suPct(rec.annualized_pct, 0),
    tip: SU_TIP.annual
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Keeps it",
    value: suPct(rec.p_keep_model, 0),
    tip: SU_TIP.keep,
    tone: rec.p_keep_model >= 80 ? "up" : ""
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Expected value",
    value: suSigned(rec.ev_per_contract),
    tip: SU_TIP.ev,
    tone: (rec.ev_per_contract || 0) > 0 ? "up" : "down"
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Worst-5% loss",
    value: suMoney(rec.tail_loss_per_share),
    tip: SU_TIP.tail,
    tone: "down"
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Breakeven",
    value: suMoney(rec.breakeven),
    tip: SU_TIP.breakeven
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Collateral",
    value: suMoney(rec.collateral, 0),
    tip: SU_TIP.collateral
  })), /*#__PURE__*/React.createElement("div", {
    className: "su-cols"
  }, /*#__PURE__*/React.createElement("div", {
    className: "su-col"
  }, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: "The factors supporting this trade, each traceable to the tab that computed it."
  }, "Why this trade"), /*#__PURE__*/React.createElement("ul", {
    className: "su-list"
  }, (rec.why || []).map((w, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, w)))), /*#__PURE__*/React.createElement("div", {
    className: "su-col"
  }, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: "The risks in this specific trade \u2014 not a generic disclaimer. An empty list here would itself be a warning sign."
  }, "What could go wrong"), /*#__PURE__*/React.createElement("ul", {
    className: "su-list su-list-risk"
  }, (rec.risks || []).map((w, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, w))))));
}
function BestSetupCard({
  apiFetch,
  ticker,
  onOpenTab
}) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const seq = useRef(0);
  const load = React.useCallback(async force => {
    const sym = String(ticker || "").trim().toUpperCase();
    if (!sym) return;
    const mine = ++seq.current;
    setBusy(true);
    try {
      const r = await apiFetch(`/api/setup?symbol=${encodeURIComponent(sym)}` + (force ? "&force=1" : ""));
      const d = await r.json();
      // A slow response for an older ticker must never paint over a newer
      // one — the same sequence guard the Patterns tab needed.
      if (mine !== seq.current) return;
      if (d && d.symbol && d.symbol !== sym) return;
      setData(d);
      setErr(d && d.ok === false && d.error ? d.error : null);
    } catch (e) {
      if (mine === seq.current) setErr(String(e && e.message ? e.message : e));
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }, [apiFetch, ticker]);
  useEffect(() => {
    setData(null);
    setErr(null);
    load(false);
  }, [load]);
  const rec = data && data.best;
  const alt = data && data.alternative;
  const cond = data && data.conditioning || {};
  const iv = data && data.iv || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "card su-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Best setup"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, ticker ? `The strongest premium sale on ${ticker} right now` : "Best setup"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "Every layer this app already computes \u2014 weekly range, streaks, swing maturity, premium, implied volatility, probability, liquidity and gamma exposure \u2014 combined into one recommendation, with the evidence behind it.")), /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: () => load(true),
    disabled: busy,
    title: "Re-read the chain and rebuild the recommendation now."
  }, busy ? "Reading…" : "Refresh")), data && data.spot ? /*#__PURE__*/React.createElement("div", {
    className: "st-status"
  }, /*#__PURE__*/React.createElement("span", {
    className: "st-status-label"
  }, data.symbol), /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep",
    title: "Last traded price."
  }, suMoney(data.spot)), data.as_of ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep",
    title: "When this recommendation was built."
  }, "built ", suTime(data.as_of)) : null, data.cached ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep",
    title: "Served from the short-lived cache. Hit Refresh for a fresh read."
  }, "cached") : null, data.earnings_in_days != null ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep",
    title: "Earnings inside the life of an option change everything the historical rates here were measured on."
  }, "earnings in ", data.earnings_in_days, " days") : null) : null, busy && !data ? /*#__PURE__*/React.createElement("div", {
    className: "st-loading",
    "aria-busy": "true"
  }, /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "38%"
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "90%"
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "76%"
    }
  })) : null, err ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "research-error"
  }, err), /*#__PURE__*/React.createElement("button", {
    className: "card-error-btn st-retry",
    onClick: () => load(true)
  }, "Try again")) : null, data && data.ok === false && !err ?
  // "Nothing here today" is an answer this card is supposed to give,
  // and it is a different thing from "something went wrong" or "no
  // data". A deliberate refusal is rendered as one — the reason
  // already names the contract that came closest and why it was
  // turned down. Only a genuine absence gets the quiet empty state.
  data.error ? /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, data.error) : /*#__PURE__*/React.createElement("div", {
    className: "su-refused"
  }, /*#__PURE__*/React.createElement("b", {
    title: SU_TIP.no_trade_today
  }, "No trade on ", data.symbol, " today."), " ", data.reason || "No recommendation could be built for this symbol.") : null, rec ? /*#__PURE__*/React.createElement(SuRecommendation, {
    rec: rec,
    data: data
  }) : null, data && data.ok ? /*#__PURE__*/React.createElement("div", {
    className: "su-more"
  }, /*#__PURE__*/React.createElement("button", {
    className: "su-more-btn",
    onClick: () => setOpen(o => !o),
    "aria-expanded": open,
    title: "The full evidence: which bars counted as this state, the measured travel rates, what each layer voted, and the volatility context."
  }, open ? "Hide the evidence" : "Show the evidence"), open ? /*#__PURE__*/React.createElement("div", {
    className: "su-evidence"
  }, /*#__PURE__*/React.createElement("div", {
    className: "su-ev-block"
  }, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: SU_TIP.conditioning
  }, "What counted as this state"), /*#__PURE__*/React.createElement("p", {
    className: "su-note"
  }, cond.note, data.conditioned_bars ? ` ${data.conditioned_bars} past bars matched.` : "")), /*#__PURE__*/React.createElement("div", {
    className: "su-ev-block"
  }, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: SU_TIP.measured
  }, "How far this stock actually travels, over ", suNum(data.dte, 0), " days"), /*#__PURE__*/React.createElement(SuMeasured, {
    measured: (data.measured || {})[rec ? rec.side : "call"],
    floorPct: rec && rec.ceiling && rec.ceiling.min_distance_pct || null
  })), /*#__PURE__*/React.createElement("div", {
    className: "su-ev-block"
  }, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "What each layer said"), /*#__PURE__*/React.createElement("ul", {
    className: "su-list"
  }, ((data.bias || {}).why || []).map((w, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, w)), !((data.bias || {}).why || []).length ? /*#__PURE__*/React.createElement("li", {
    className: "muted"
  }, "No layer reports this stock as stretched in either direction right now.") : null)), /*#__PURE__*/React.createElement("div", {
    className: "su-ev-block"
  }, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Volatility context"), /*#__PURE__*/React.createElement("div", {
    className: "su-stats su-stats-sm"
  }, /*#__PURE__*/React.createElement(SuStat, {
    label: "Implied, 30 day",
    value: iv.iv30 == null ? "—" : suPct(iv.iv30 * 100, 1),
    tip: SU_TIP.iv30
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Expected realized",
    value: iv.erv30 == null ? "—" : suPct(iv.erv30 * 100, 1),
    tip: SU_TIP.erv
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Premium over realized",
    value: iv.vrp_points == null ? "—" : suNum(iv.vrp_points, 1),
    tip: SU_TIP.vrp,
    tone: (iv.vrp_points || 0) > 0 ? "up" : "down"
  }), /*#__PURE__*/React.createElement(SuStat, {
    label: "Term shape",
    value: iv.term_shape || "—",
    tip: "How implied volatility is priced across expirations."
  }))), alt ? /*#__PURE__*/React.createElement("div", {
    className: "su-ev-block"
  }, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: SU_TIP.alt
  }, "The other side"), alt.ok ? /*#__PURE__*/React.createElement("p", {
    className: "su-note"
  }, alt.action, " at ", suNum(alt.strike, 2), " (", suNum(Math.abs(alt.delta), 2), " delta) for ", suMoney(alt.credit), " \u2014 ", alt.confidence.label.toLowerCase(), " confidence. It was not chosen because the layers lean the other way or it scored lower.") :
  /*#__PURE__*/
  // A refused other side is a finding, not an absence. Dropping
  // it would leave the reader assuming it was simply worse.
  React.createElement("p", {
    className: "su-note"
  }, "The ", alt.side === "call" ? "call" : "put", " side was refused outright, not merely outscored. ", alt.reason)) : null) : null) : null);
}

// ══════════════════════════════════════════════════════════════════════════
// THE BOARD — what is worth selling today, across the watchlist
// ══════════════════════════════════════════════════════════════════════════
//
// Endpoint: GET /api/setup_board
//
// This does NOT try to find a better strike. Measurement said the 15-22
// delta band already sits at the ~85% win rate it targets, so there is no
// gap there to harvest. What it does is pick the DAYS AND NAMES where the
// same strike is paying more than that stock's own history says the risk
// is worth — a selection claim, which the data does support.

function SuBoardRow({
  r,
  onPick
}) {
  const rich = r.richness;
  const tone = rich >= 80 ? "up" : rich >= 50 ? "" : "muted";
  return /*#__PURE__*/React.createElement("tr", {
    className: "su-brow"
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("button", {
    className: "su-blink",
    onClick: () => onPick && onPick(r.symbol),
    title: `Load ${r.symbol} in the Best Setup card above`
  }, r.symbol)), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${tone}`,
    title: r.richness_why
  }, suNum(rich, 0), /*#__PURE__*/React.createElement("span", {
    className: "su-bbasis",
    title: SU_TIP.rich_basis
  }, r.richness_basis === "percentile" ? "pctl" : "ratio")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: SU_TIP.vrp
  }, r.vrp_points == null ? "—" : `${r.vrp_points > 0 ? "+" : "−"}${Math.abs(r.vrp_points).toFixed(1)}`), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: SU_TIP.iv30
  }, suPct(r.iv30 * 100, 0)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: SU_TIP.erv
  }, suPct(r.erv30 * 100, 0)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: SU_TIP.action
  }, r.strike == null ? "—" : suNum(r.strike, 2), r.delta == null ? null : /*#__PURE__*/React.createElement("span", {
    className: "su-bdelta"
  }, suNum(Math.abs(r.delta), 2), "\u0394")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: SU_TIP.credit
  }, suMoney(r.credit)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: SU_TIP.roc
  }, suPct(r.roc_pct, 2)), /*#__PURE__*/React.createElement("td", {
    title: SU_TIP.action
  }, suDate(r.expiration)));
}
function SellBoardCard({
  apiFetch,
  onPickTicker
}) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showSkipped, setShowSkipped] = useState(false);
  const seq = useRef(0);
  const load = React.useCallback(async () => {
    const mine = ++seq.current;
    setBusy(true);
    try {
      const r = await apiFetch("/api/setup_board?limit=12");
      const d = await r.json();
      if (mine !== seq.current) return;
      setData(d);
      setErr(d && d.ok === false && d.error ? d.error : null);
    } catch (e) {
      if (mine === seq.current) setErr(String(e && e.message ? e.message : e));
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }, [apiFetch]);
  useEffect(() => {
    load();
  }, [load]);
  const rows = data && data.rows || [];
  const skipped = data && data.skipped || [];
  const uni = data && data.universe || null;
  return /*#__PURE__*/React.createElement("div", {
    className: "card su-card su-board"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: SU_TIP.board
  }, "Worth selling today"), /*#__PURE__*/React.createElement("h3", {
    className: "card-title"
  }, "Where the premium is actually rich"), /*#__PURE__*/React.createElement("p", {
    className: "card-sub"
  }, "Ranked by how rich each option is against what that stock itself realizes. Same delta you always sell \u2014 this picks the names and the days, not the strike.")), /*#__PURE__*/React.createElement("div", {
    className: "toolbar"
  }, /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: load,
    disabled: busy
  }, busy ? "Scanning…" : "Refresh"))), uni ? /*#__PURE__*/React.createElement("p", {
    className: "su-uni",
    title: SU_TIP.universe
  }, uni.ranked, " names ranked \xB7 ", data.measured || 0, " had their option chain measured \xB7 ", rows.length, " qualified", uni.dropped && uni.dropped["earnings inside the option's life"] ? /*#__PURE__*/React.createElement("span", {
    title: SU_TIP.board_earn
  }, " ", "\xB7 ", uni.dropped["earnings inside the option's life"], " skipped for earnings inside the option\u2019s life") : null) : null, busy && !data ? /*#__PURE__*/React.createElement("div", {
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
  })) : null, err ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "research-error"
  }, err), /*#__PURE__*/React.createElement("button", {
    className: "card-error-btn st-retry",
    onClick: load
  }, "Try again")) : null, data && !err && !rows.length ? /*#__PURE__*/React.createElement("div", {
    className: "su-refused"
  }, /*#__PURE__*/React.createElement("b", null, "Nothing qualifies today."), " ", "Every name the scan measured was either paying no more than the stock actually realizes, carrying earnings inside the option\u2019s life, or failing a liquidity or expected-value check. Most days are like this; a board that always has something on it is not measuring anything.") : null, rows.length ? /*#__PURE__*/React.createElement("div", {
    className: "su-btable-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table su-btable"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Symbol"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SU_TIP.richness
  }, "Richness"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SU_TIP.vrp
  }, "Premium over realized"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SU_TIP.iv30
  }, "Implied"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SU_TIP.erv
  }, "Expected realized"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SU_TIP.action
  }, "Strike"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SU_TIP.credit
  }, "Credit"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SU_TIP.roc
  }, "Return on collateral"), /*#__PURE__*/React.createElement("th", {
    title: SU_TIP.expiry
  }, "Expiration"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => /*#__PURE__*/React.createElement(SuBoardRow, {
    key: r.symbol,
    r: r,
    onPick: onPickTicker
  }))))) : null, skipped.length ? /*#__PURE__*/React.createElement("div", {
    className: "su-more"
  }, /*#__PURE__*/React.createElement("button", {
    className: "su-more-btn",
    "aria-expanded": showSkipped,
    title: SU_TIP.board_skip,
    onClick: () => setShowSkipped(v => !v)
  }, showSkipped ? "Hide" : "Show", " the ", skipped.length, " it refused"), showSkipped ? /*#__PURE__*/React.createElement("ul", {
    className: "su-list su-list-risk su-bskip"
  }, skipped.map(s => /*#__PURE__*/React.createElement("li", {
    key: s.symbol
  }, /*#__PURE__*/React.createElement("b", null, s.symbol), " \u2014 ", (s.why || []).join(" ")))) : null) : null);
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  BestSetupCard: React.memo(BestSetupCard),
  SellBoardCard: React.memo(SellBoardCard)
});
})();
