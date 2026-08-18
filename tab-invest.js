(function () {
// tab-invest.jsx — LAZY CHUNK (v4.42), loaded on first Investment open.
//
// The long-horizon workstation. Six questions now:
//   1. How good is this business?              -> QUALITY
//   2. How strong is its growth?               -> GROWTH
//   3. Cheap or expensive against ITSELF?      -> VALUATION (own history)
//   4. Cheap or expensive against its PEERS?   -> VALUATION (peer percentile)
//   5. Are analyst expectations improving?     -> REVISIONS
//   6. Is the cheapness actually a trap?       -> VALUE TRAP RISK
//
// The four vectors are shown side by side and NEVER blended into one score.
// A single number would let strong growth hide an expensive price, which is
// the exact mistake this layout exists to prevent.
//
// Endpoints: GET /api/invest?symbol=X · /api/invest/history
//          · /api/invest/valuation · /api/invest/peers · /api/invest/config
//
// House rules honoured here:
//   · Nothing renders 0 to mean "unknown". Missing is "N/A" with the reason
//     on hover, every time.
//   · Every value shows its source, its as-of date, its basis and whether it
//     is stale — that is what the little grey line under each number is.
//   · Dates read "March 28, 2026". Never ISO.
//   · Tooltip on everything.

const INV_VERDICT_TONE = {
  ATTRACTIVE: "up",
  WATCH: "warn",
  WAIT: "warn",
  AVOID: "down",
  "INSUFFICIENT DATA": "mut",
  "SPECIALIZED MODEL REQUIRED": "mut"
};
const INV_VERDICT_TIP = {
  ATTRACTIVE: "Good business, growing, priced at the cheap end of its OWN " + "history and of its peer group, with analysts not cutting and no cluster of " + "deterioration signals. Not a recommendation and not a score — a set of " + "conditions you can re-check by hand from the numbers on this screen.",
  WATCH: "Worth following. Either the price is reasonable but not at the cheap " + "end, or the price is fine and something in the business is not.",
  WAIT: "Nothing wrong with the business — it is priced near the expensive end " + "of its own range. The price and the earnings figure that would move the " + "answer are both stated below, and they come from THIS company's own " + "median valuation rather than from a universal multiple.",
  AVOID: "A gate failed outright: the company is losing money, or several " + "deterioration signals are firing at once. Cheapness with that pattern is " + "the classic value trap, and this dashboard will not call it attractive.",
  "INSUFFICIENT DATA": "There is not enough on file to answer honestly. This is " + "a real answer, not an error: some filers report only once a year, some " + "report in a foreign currency, and some have too little history.",
  "SPECIALIZED MODEL REQUIRED": "This is a bank, insurer, broker or property " + "trust. The generic scorecard leans on return on invested capital, free " + "cash flow and net debt, none of which mean for these businesses what they " + "mean for an operating company. A model built for them is not written yet, " + "and a generic one dressed up as an answer would be worse than none."
};

// The four vectors, in the order they are read.
const INV_DIMENSIONS = [["quality", "Quality", "How good the business is: what it earns on the " + "capital it uses, how much of that profit turns into cash, whether margins " + "are widening, whether the share count is shrinking or being diluted away, " + "how much revenue goes out as stock to employees, and how levered it is. " + "Ranked against comparable companies where a peer group exists."], ["growth", "Growth", "Revenue growth, earnings growth and the forward " + "estimate, ranked against comparable companies. The margin and share-count " + "contributions come from the earnings breakdown further down rather than " + "being recomputed, so the same movement is never counted twice."], ["valuation", "Valuation", "Cheap or expensive — but against ITSELF and " + "against comparable businesses, never against a universal multiple. 100 " + "means cheap. A great company is allowed to trade at a high multiple; what " + "matters is whether it is high FOR THIS COMPANY."], ["revisions", "Revisions", "Whether analysts are raising or cutting their " + "numbers. Shows NOT RATED below four covering analysts, because three " + "people agreeing is three people agreeing, not a signal."]];
const INV_TRAP_TONE = {
  "LOW RISK": "up",
  "MODERATE RISK": "warn",
  "HIGH RISK": "down",
  "NOT RATED": "mut"
};
const INV_TRAP_TIP = {
  "LOW RISK": "None of the deterioration signals are firing. Cheapness here " + "is not obviously the market pricing in decline.",
  "MODERATE RISK": "At least one thing is moving the wrong way. Worth reading " + "the list before treating a low valuation as an opportunity.",
  "HIGH RISK": "Several things are deteriorating at once. A stock that looks " + "cheap with this pattern is the classic value trap — the price fell " + "BECAUSE the business is getting worse, and this dashboard will not call " + "it attractive.",
  "NOT RATED": "Almost none of the deterioration signals could be measured for " + "this filer, so silence here is not evidence that nothing is wrong."
};
const INV_CYCLE_TIP = {
  "PRE-EARNINGS": "Reports within the next two weeks. Trailing figures and " + "estimates can both move sharply on the day.",
  "POST-EARNINGS FRESH": "Reported recently, so the trailing figures and the " + "estimates are as current as they get.",
  "NORMAL": "Mid-cycle: the last report is digested and the next is not close.",
  "STALE": "The last report is old enough that the trailing figures describe a " + "quarter the business has already moved past.",
  "UNKNOWN": "No earnings dates are available for this ticker."
};

// ── formatting ────────────────────────────────────────────────────────────
// Every one of these returns "N/A", never "0" and never "—", when the value
// is absent. A dash reads like a small number; N/A reads like no number.

const invNA = "N/A";
function invMoney(v, digits = 2) {
  if (v == null || !isFinite(v)) return invNA;
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)} trillion`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)} billion`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)} million`;
  return `${sign}$${a.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  })}`;
}
function invPrice(v) {
  return v == null || !isFinite(v) ? invNA : `$${Number(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`;
}
function invPct(v, digits = 1) {
  return v == null || !isFinite(v) ? invNA : `${Number(v).toFixed(digits)}%`;
}
function invSignedPct(v, digits = 1) {
  if (v == null || !isFinite(v)) return invNA;
  return `${v >= 0 ? "+" : ""}${Number(v).toFixed(digits)}%`;
}
function invRatio(v, digits = 1) {
  return v == null || !isFinite(v) ? invNA : `${Number(v).toFixed(digits)}×`;
}
function invCount(v) {
  if (v == null || !isFinite(v)) return invNA;
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)} billion`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)} million`;
  return Math.round(v).toLocaleString("en-US");
}
// House rule: Month Day, Year — never raw ISO.
function invDate(s) {
  if (!s) return invNA;
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric"
  });
}
function invShortDate(s) {
  if (!s) return invNA;
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  });
}
// 21 -> "21st". "21th percentile" undercuts every careful sentence near it.
function invOrdinal(v) {
  if (v == null || !isFinite(v)) return invNA;
  const n = Math.round(v);
  if (n % 100 >= 10 && n % 100 <= 20) return `${n}th`;
  return `${n}${{
    1: "st",
    2: "nd",
    3: "rd"
  }[n % 10] || "th"}`;
}
function invAge(hours) {
  if (hours == null || !isFinite(hours)) return "";
  if (hours < 1) return "under an hour old";
  if (hours < 48) return `${Math.round(hours)} hours old`;
  return `${Math.round(hours / 24)} days old`;
}

// ── the provenance line that sits under every number ──────────────────────
//
// This component is the whole honesty contract of the tab in one place: what
// the number is, where it came from, as of when, on what basis, and whether
// the provider was reachable when it was read.

function InvSource({
  prov,
  basis,
  asOf,
  reason
}) {
  const p = prov || {};
  const source = p.source || null;
  const when = asOf || p.as_of;
  const theBasis = basis || p.basis;
  const stale = !!p.stale;
  const bits = [];
  if (source) bits.push(source);
  if (when) bits.push(`as of ${invShortDate(when)}`);
  const tip = [source ? `Source: ${source}` : "Source: not available", when ? `As of ${invDate(when)}` : null, theBasis ? `Basis: ${theBasis}` : null, stale ? `STALE — ${invAge(p.age_hours)}. ${p.reason || ""}` : null, reason || null].filter(Boolean).join("\n");
  if (!bits.length && !reason) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: `inv-src${stale ? " inv-src-stale" : ""}`,
    title: tip
  }, stale && /*#__PURE__*/React.createElement("span", {
    className: "inv-stale-flag",
    title: `The provider was
        unreachable, so this is the last value that was successfully
        recorded — ${invAge(p.age_hours)}.`
  }, "STALE"), bits.join(" · ") || reason);
}

// One statistic: label, value, provenance. `reason` is why it is N/A.
function InvStat({
  label,
  value,
  tip,
  prov,
  basis,
  asOf,
  reason,
  tone
}) {
  const missing = value === invNA || value == null;
  const fullTip = [tip, missing && reason ? `Not available: ${reason}` : null].filter(Boolean).join("\n\n");
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-stat-label",
    title: fullTip
  }, label), /*#__PURE__*/React.createElement("b", {
    className: `inv-stat-val${tone ? ` ${tone}` : ""}${missing ? " inv-na" : ""}`,
    title: fullTip
  }, value), /*#__PURE__*/React.createElement(InvSource, {
    prov: prov,
    basis: basis,
    asOf: asOf,
    reason: missing ? reason : ""
  }));
}
function InvVerdictPill({
  verdict
}) {
  const label = verdict || "INSUFFICIENT DATA";
  const tone = INV_VERDICT_TONE[label] || "mut";
  return /*#__PURE__*/React.createElement("span", {
    className: `inv-verdict inv-verdict-${tone}`,
    title: INV_VERDICT_TIP[label]
  }, label);
}
function InvMoatTags({
  tags,
  profile
}) {
  if (!tags || !tags.length) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-moats"
  }, tags.map(t => /*#__PURE__*/React.createElement("span", {
    key: t,
    className: "inv-moat",
    title: `A durable-advantage tag read
          from this company's own annual report, filed ${invDate(profile && profile.as_of)}.
          Tags are counted from the language in Item 1, Business — they are
          descriptions of what the company claims, never a score. There is
          deliberately no 1-to-10 moat rating: reading a filing for keywords
          cannot support that kind of precision.`
  }, t)));
}

// ── Earnings Drivers ──────────────────────────────────────────────────────

function InvDrivers({
  drivers
}) {
  const d = drivers || {};
  if (!d.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty",
      title: "The breakdown is only drawn when revenue, net income and the diluted share count are all on file for both periods. It is never approximated."
    }, d.reason || "No earnings breakdown available.");
  }
  const isLog = d.method === "log";
  const contribs = d.contributions || [];
  const scale = Math.max(...contribs.map(c => Math.abs(c.value)), Math.abs(d.total), 1e-9);
  const unit = isLog ? v => `${v >= 0 ? "+" : ""}${v.toFixed(1)} pts` : v => `${v >= 0 ? "+" : "-"}$${Math.abs(v).toFixed(2)}`;
  const headTip = isLog ? `Earnings per share IS revenue times profit margin divided by the share
count. Taking logarithms turns that product into a sum, so the change in
earnings splits EXACTLY into three parts that add back to the total — this is
an identity, not an approximation. Read a bar as "this driver added this much
to the change"; a positive share-count bar means the count FELL, which lifts
earnings per share.` : `Earnings or margins went negative or crossed zero over this year, so the
logarithmic split does not exist and this tab will not fake one. Each bar is
that driver's Shapley value: its effect on earnings per share averaged over
every order in which the three drivers could have moved. Averaging over the
orderings is what removes the arbitrary choice of which driver gets credit for
the overlap. The bars add up exactly to the change in dollars per share.`;
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-drivers"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: headTip
  }, isLog ? "Earnings drivers" : "Dollar EPS Bridge", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 what moved earnings per share over the last year")), /*#__PURE__*/React.createElement("div", {
    className: "inv-drivers-sum",
    title: `Trailing twelve months ending
        ${invDate(d.period_end)}, against the twelve months ending
        ${invDate(d.prior_period_end)}.`
  }, invPrice(d.eps_prior), " ", /*#__PURE__*/React.createElement("span", {
    className: "inv-arrow"
  }, "\u2192"), " ", invPrice(d.eps_current), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 a change of ", isLog ? `${d.total >= 0 ? "+" : ""}${d.total.toFixed(1)} log points` : `${d.total >= 0 ? "+" : "-"}$${Math.abs(d.total).toFixed(2)} per share`)), /*#__PURE__*/React.createElement("div", {
    className: "inv-bars"
  }, contribs.map(c => {
    const pctW = Math.min(100, Math.abs(c.value) / scale * 100);
    const up = c.value >= 0;
    return /*#__PURE__*/React.createElement("div", {
      className: "inv-bar-row",
      key: c.driver
    }, /*#__PURE__*/React.createElement("span", {
      className: "inv-bar-label",
      title: INV_DRIVER_TIP[c.driver] || c.driver
    }, c.driver), /*#__PURE__*/React.createElement("span", {
      className: "inv-bar-track"
    }, /*#__PURE__*/React.createElement("i", {
      className: up ? "up" : "down",
      style: {
        width: `${pctW}%`
      }
    })), /*#__PURE__*/React.createElement("b", {
      className: up ? "up" : "down"
    }, unit(c.value)));
  }), /*#__PURE__*/React.createElement("div", {
    className: "inv-bar-row inv-bar-total"
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-bar-label",
    title: "The three contributions above add up to exactly this number. If they ever did not, the panel would not be drawn \u2014 the reconciliation is asserted in the test suite."
  }, "Total"), /*#__PURE__*/React.createElement("span", {
    className: "inv-bar-track"
  }), /*#__PURE__*/React.createElement("b", {
    className: d.total >= 0 ? "up" : "down"
  }, unit(d.total)))), d.warning && /*#__PURE__*/React.createElement("div", {
    className: "inv-warn",
    title: "The breakdown describes net income divided by diluted shares, because that is the only quantity the revenue-margin-shares identity can equal. Where a company's reported earnings per share differs from it, this says so rather than quietly bridging to a different number than the one at the top of the tab."
  }, d.warning), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: headTip
  }, d.note));
}
const INV_DRIVER_TIP = {
  "Revenue": "How much of the change in earnings per share came from selling more (or less).",
  "Profit margin": "How much came from keeping more (or less) of each dollar of revenue as profit.",
  "Share count": "How much came from the share count changing. Buybacks shrink the count and lift earnings per share; issuing shares does the opposite. A positive bar means the count fell.",
  "Net income": "How much of the change came from the profit or loss itself."
};

// ── Price vs earnings chart ───────────────────────────────────────────────

function InvHistoryChart({
  history,
  years,
  onYears,
  symbol
}) {
  const h = history || {};
  const series = [{
    key: "price",
    label: "Share price",
    cls: "inv-line-price",
    pts: h.price || []
  }, {
    key: "eps_ttm",
    label: "Earnings per share (trailing)",
    cls: "inv-line-eps",
    pts: h.eps_ttm || []
  }, {
    key: "eps_forward",
    label: "Forward earnings estimate",
    cls: "inv-line-fwd",
    pts: h.eps_forward || []
  }].filter(s => s.pts.length > 1);
  const W = 860,
    H = 260,
    L = 44,
    R = 12,
    T = 12,
    B = 26;
  let body = null;
  if (!series.length) {
    body = /*#__PURE__*/React.createElement("div", {
      className: "research-empty"
    }, "Not enough history to draw this chart yet.");
  } else {
    const all = series.flatMap(s => s.pts);
    const times = all.map(p => new Date(`${p.date}T12:00:00`).getTime());
    const t0 = Math.min(...times),
      t1 = Math.max(...times);
    const vals = all.map(p => p.indexed);
    const lo = Math.min(...vals),
      hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.08, 4);
    const yLo = lo - pad,
      yHi = hi + pad;
    const x = d => L + (new Date(`${d}T12:00:00`).getTime() - t0) / Math.max(t1 - t0, 1) * (W - L - R);
    const y = v => T + (1 - (v - yLo) / Math.max(yHi - yLo, 1e-9)) * (H - T - B);
    const ticks = [yLo, (yLo + yHi) / 2, yHi];
    body = /*#__PURE__*/React.createElement("svg", {
      viewBox: `0 0 ${W} ${H}`,
      className: "inv-chart",
      role: "img",
      "aria-label": `${symbol} share price against earnings per share, both indexed to 100`
    }, ticks.map(v => /*#__PURE__*/React.createElement("g", {
      key: v
    }, /*#__PURE__*/React.createElement("line", {
      x1: L,
      x2: W - R,
      y1: y(v),
      y2: y(v),
      className: "inv-grid"
    }), /*#__PURE__*/React.createElement("text", {
      x: L - 6,
      y: y(v) + 3,
      className: "inv-axis",
      textAnchor: "end"
    }, Math.round(v)))), /*#__PURE__*/React.createElement("line", {
      x1: L,
      x2: W - R,
      y1: y(100),
      y2: y(100),
      className: "inv-base"
    }), series.map(s => /*#__PURE__*/React.createElement("path", {
      key: s.key,
      className: s.cls,
      d: s.pts.map((p, i) => `${i ? "L" : "M"}${x(p.date).toFixed(1)},${y(p.indexed).toFixed(1)}`).join(" ")
    })), /*#__PURE__*/React.createElement("text", {
      x: L,
      y: H - 6,
      className: "inv-axis"
    }, invShortDate(h.start)), /*#__PURE__*/React.createElement("text", {
      x: W - R,
      y: H - 6,
      className: "inv-axis",
      textAnchor: "end"
    }, "today"));
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-histwrap"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Both lines start at 100 on the first
day of the window, so their SHAPES can be compared regardless of scale. When
the price line pulls away from the earnings line, you are paying more per
dollar of earnings than you were; when earnings outrun the price, you are
paying less.

Reported earnings are plotted on the day they were FILED, not the day the
quarter ended — a quarter that ended in March was not public until May, and
showing it in March would put information on the chart that nobody had.

Per-share figures use each filing's most recent restatement, which is the same
share basis split-adjusted prices use, so a stock split does not create a step
in either line.`
  }, "Price against earnings", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 both indexed to 100 at the start"), /*#__PURE__*/React.createElement("span", {
    className: "inv-yearsel"
  }, [3, 5].map(n => /*#__PURE__*/React.createElement("button", {
    key: n,
    className: `inv-yearbtn${years === n ? " on" : ""}`,
    onClick: () => onYears(n),
    title: `Show the last ${n} years.`
  }, n, "Y")))), /*#__PURE__*/React.createElement("div", {
    className: "inv-legend"
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-key inv-key-price",
    title: "Share price, split-adjusted, indexed to 100."
  }, "Share price"), /*#__PURE__*/React.createElement("span", {
    className: "inv-key inv-key-eps",
    title: "Trailing twelve-month earnings per share as reported to the SEC, indexed to 100, each point placed on its filing date."
  }, "Earnings per share"), /*#__PURE__*/React.createElement("span", {
    className: "inv-key inv-key-fwd",
    title: "The analyst forward estimate, recorded by this dashboard once a day. It starts on the first day one was recorded and is never back-filled \u2014 no free archive of past consensus exists, and inventing one would be a fabricated history."
  }, "Forward estimate")), body, (h.notes || []).map((n, i) => /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    key: i
  }, n)));
}

// ── the four-vector header ────────────────────────────────────────────────

function InvScoreTile({
  dimKey,
  label,
  tip,
  block,
  onOpen
}) {
  const b = block || {};
  const score = b.score;
  const has = score != null && isFinite(score);
  const tone = !has ? "mut" : score >= 60 ? "up" : score >= 40 ? "warn" : "down";
  const detail = [tip, b.coverage ? `Built from ${b.coverage}.` : null, b.peer_ranked ? "Ranked against comparable companies." : has ? "Scored on absolute bands — too few comparable companies to rank against." : null, !has && b.reason ? `Not rated: ${b.reason}` : null].filter(Boolean).join("\n\n");
  return /*#__PURE__*/React.createElement("button", {
    className: `inv-tile inv-tile-${tone}`,
    title: detail,
    onClick: () => onOpen && onOpen(dimKey)
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-tile-label"
  }, label), /*#__PURE__*/React.createElement("b", {
    className: "inv-tile-value"
  }, b.label || "NOT RATED"), /*#__PURE__*/React.createElement("span", {
    className: "inv-tile-bar",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      width: `${has ? Math.max(2, Math.min(100, score)) : 0}%`
    }
  })), /*#__PURE__*/React.createElement("span", {
    className: "inv-tile-num"
  }, has ? `${Math.round(score)} / 100` : "—"));
}
function InvTrapTile({
  trap,
  onOpen
}) {
  const t = trap || {};
  const level = t.level || "NOT RATED";
  const tone = INV_TRAP_TONE[level] || "mut";
  const detail = [INV_TRAP_TIP[level], t.n_active ? `${t.n_active} signal${t.n_active === 1 ? "" : "s"} firing.` : null, (t.unknown || []).length ? `${t.unknown.length} could not be measured.` : null, t.reason || null].filter(Boolean).join("\n\n");
  return /*#__PURE__*/React.createElement("button", {
    className: `inv-tile inv-tile-${tone}`,
    title: detail,
    onClick: () => onOpen && onOpen("trap")
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-tile-label"
  }, "Value trap risk"), /*#__PURE__*/React.createElement("b", {
    className: "inv-tile-value"
  }, level), /*#__PURE__*/React.createElement("span", {
    className: "inv-tile-sub"
  }, (t.active || []).length ? `${t.active.length} signal${t.active.length === 1 ? "" : "s"} firing` : "no deterioration signals firing"));
}
function InvCyclePill({
  cycle
}) {
  const c = cycle || {};
  const state = c.state || "UNKNOWN";
  return /*#__PURE__*/React.createElement("span", {
    className: "inv-cycle",
    title: `${INV_CYCLE_TIP[state] || ""}${c.reason ? "\n\n" + c.reason : ""}`
  }, state);
}

// ── valuation against its own history ─────────────────────────────────────

function InvRangeBar({
  dist,
  cheapHigh,
  fmt
}) {
  const d = dist || {};
  if (!d.available) return null;
  const lo = d.min,
    hi = d.max;
  const span = hi - lo || 1;
  const at = v => `${Math.max(0, Math.min(100, (v - lo) / span * 100))}%`;
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-range",
    title: `The bar spans everything this measure has been over the window: ${fmt(lo)} at the low end to ${fmt(hi)} at the high. The marks are the 10th percentile, the median and the 90th; the pin is where it stands today.`
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-range-track"
  }, /*#__PURE__*/React.createElement("i", {
    className: "inv-range-fill",
    style: {
      left: at(d.p10),
      width: `calc(${at(d.p90)} - ${at(d.p10)})`
    }
  }), /*#__PURE__*/React.createElement("em", {
    className: "inv-range-median",
    style: {
      left: at(d.median)
    }
  }), /*#__PURE__*/React.createElement("b", {
    className: "inv-range-now",
    style: {
      left: at(d.current)
    }
  })), /*#__PURE__*/React.createElement("span", {
    className: "inv-range-ends"
  }, /*#__PURE__*/React.createElement("span", null, fmt(lo)), /*#__PURE__*/React.createElement("span", null, fmt(hi))));
}
function InvValuationHistory({
  vh,
  valuation,
  symbol
}) {
  const v = vh || {};
  const [win, setWin] = useState("5y");
  if (!v.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty",
      title: "A valuation history needs a daily price for every day it measures and reported figures lined up against those days. Where either is missing it is left out rather than filled in."
    }, v.reason || `No valuation history could be built for ${symbol}.`);
  }
  const dists = v.distributions || {};
  const regime = v.regime || {};
  const measures = [["earnings_yield_pct", x => x == null ? invNA : `${x.toFixed(1)}%`], ["fcf_yield_pct", x => x == null ? invNA : `${x.toFixed(1)}%`], ["trailing_pe", x => x == null ? invNA : `${x.toFixed(1)}×`]];
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-vhist"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Where this company is valued today
against where it has been valued before, using only figures that were public
on each of those days. Reported earnings enter the history on the day they were
FILED, not the day the quarter ended, so nothing on this chart was unknowable
at the time. Prices and per-share figures both come from split-restated
sources, so a stock split leaves no step.

This is the answer to "cheap compared with itself" — the question a universal
price/earnings threshold cannot ask.`
  }, "Valuation against its own history", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", v.n_days, " trading days, ", invShortDate(v.from), " to ", invShortDate(v.to)), /*#__PURE__*/React.createElement("span", {
    className: "inv-yearsel"
  }, ["3y", "5y"].map(w => /*#__PURE__*/React.createElement("button", {
    key: w,
    className: `inv-yearbtn${win === w ? " on" : ""}`,
    onClick: () => setWin(w),
    title: `Measure against the last ${w === "3y" ? "three" : "five"} years.`
  }, w.toUpperCase())))), regime.shifted && /*#__PURE__*/React.createElement("div", {
    className: "inv-warn",
    title: `The typical level over the last
${regime.recent_years} years is ${regime.recent_median != null ? regime.recent_median.toFixed(1) : "—"},
against ${regime.earlier_median != null ? regime.earlier_median.toFixed(1) : "—"} over the period before it — a move of
${regime.shift != null ? regime.shift.toFixed(1) : "—"}, which is larger than the earlier period's own
10th-to-90th-percentile spread of ${regime.earlier_spread != null ? regime.earlier_spread.toFixed(1) : "—"}. That is a change of
level rather than a wiggle, so the older half of this history describes a
different market for this stock and the verdict leans on it less.`
  }, /*#__PURE__*/React.createElement("b", null, "REGIME SHIFT DETECTED"), " \u2014 the level this stock is valued at has moved. The older part of the history below is a weaker guide than usual, and the verdict reduces its reliance on it."), measures.map(([key, fmt]) => {
    const block = dists[key] || {};
    const d = block[win] || {};
    return /*#__PURE__*/React.createElement("div", {
      className: "inv-vrow",
      key: key
    }, /*#__PURE__*/React.createElement("div", {
      className: "inv-vrow-head"
    }, /*#__PURE__*/React.createElement("span", {
      className: "inv-vrow-label",
      title: key === "earnings_yield_pct" ? "Trailing earnings per share divided by the share price. Higher is cheaper. This is the measure the verdict uses, because a yield can be placed beside a bond yield and a multiple cannot." : key === "fcf_yield_pct" ? "Free cash flow divided by the market value of the company, on each day's price and each day's known share count. Higher is cheaper." : "Share price divided by trailing earnings. Lower is cheaper. Shown because everyone reads it."
    }, block.label || key), /*#__PURE__*/React.createElement("b", {
      className: `inv-vrow-now${d.cheap_percentile == null ? "" : d.cheap_percentile >= 60 ? " up" : d.cheap_percentile <= 40 ? " down" : ""}`
    }, fmt(block.current)), /*#__PURE__*/React.createElement("span", {
      className: "inv-vrow-pct",
      title: d.cheap_percentile == null ? "" : `Today sits at the ${invOrdinal(d.cheap_percentile)} percentile of this company's own ${win === "3y" ? "three" : "five"}-year range, measured so that 100 always means cheap. ${d.n} observations.`
    }, d.cheap_percentile == null ? invNA : `${invOrdinal(d.cheap_percentile)} pct`)), d.available ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(InvRangeBar, {
      dist: {
        ...d,
        current: block.current
      },
      cheapHigh: block.cheap_when_high,
      fmt: fmt
    }), /*#__PURE__*/React.createElement("div", {
      className: "inv-vrow-stats"
    }, /*#__PURE__*/React.createElement("span", {
      title: "The 10th percentile \u2014 cheaper than this only one day in ten."
    }, "10th ", fmt(d.p10)), /*#__PURE__*/React.createElement("span", {
      title: "The middle of the range over this window."
    }, "Median ", fmt(d.median)), /*#__PURE__*/React.createElement("span", {
      title: "The 90th percentile \u2014 more expensive than this only one day in ten."
    }, "90th ", fmt(d.p90)), /*#__PURE__*/React.createElement("span", {
      className: "muted",
      title: "How many trading days went into this range."
    }, d.n, " days"))) : /*#__PURE__*/React.createElement("div", {
      className: "inv-note"
    }, d.reason || block.reason || "Not enough history in this window."));
  }), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: "Forward price/earnings is deliberately absent from this panel. There is no free archive of what analysts expected on a past date, so a historical forward multiple would have to be invented. It stays a current-only figure until this dashboard's own daily snapshots have accumulated enough real history."
  }, "Forward price to earnings is not shown here on purpose: no free record of past analyst expectations exists, so a historical forward multiple would have to be fabricated. It becomes available once this dashboard's own daily snapshots have accumulated enough of their own history."));
}

// ── quality detail ────────────────────────────────────────────────────────

const INV_QUALITY_TIP = {
  "Return on invested capital": "Operating profit after tax, divided by the " + "equity and net debt funding the business. The single best one-number read " + "on whether a company turns capital into profit.",
  "Free cash flow conversion": "How much of reported profit actually shows up " + "as cash. Persistently below 100% means the earnings are being consumed by " + "working capital or capital spending.",
  "Operating margin trend": "Points of operating margin gained or lost per " + "year, fitted across the reported history. A snapshot cannot tell a " + "business holding a steady margin from one grinding downward at the same level.",
  "Share count trend": "How fast the share count is changing per year. " + "Negative is buybacks, positive is dilution — every share issued is a " + "slice of your claim on the same earnings.",
  "Stock compensation as a share of revenue": "Pay settled in stock rather " + "than cash. It does not appear in cash flow but it is a real cost, borne " + "by shareholders through dilution.",
  "Net debt to operating profit": "Borrowings, less cash, against operating " + "profit before depreciation. How many years of earnings the debt represents."
};
function InvQuality({
  quality
}) {
  const q = quality || {};
  const comps = q.components || [];
  const fmt = (key, v) => {
    if (v == null) return invNA;
    if (key === "leverage") return `${v.toFixed(1)}×`;
    if (key === "operating_margin_trend" || key === "share_count_trend") return `${v >= 0 ? "+" : ""}${v.toFixed(1)} pts/yr`;
    return `${v.toFixed(1)}%`;
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-quality"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Six inputs chosen to be
non-duplicative, each scored independently and each explaining its own result.
Coverage varies genuinely by filer and industry — Microsoft tags no combined
depreciation figure at all, and several large companies do not report operating
income in machine-readable form. A missing input is a missing input, never a
failing grade and never a zero.`
  }, "Quality", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", q.coverage || "", q.peer_ranked ? " · ranked against comparable companies" : " · scored on absolute bands")), comps.map(c => /*#__PURE__*/React.createElement("div", {
    className: "inv-qrow",
    key: c.key
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-qrow-label",
    title: INV_QUALITY_TIP[c.label] || c.label
  }, c.label), /*#__PURE__*/React.createElement("b", {
    className: `inv-qrow-val${c.value == null ? " inv-na" : ""}`,
    title: c.reason || c.scored_against || ""
  }, c.reason === "SPECIALIZED MODEL REQUIRED" ? "N/A for this business type" : fmt(c.key, c.value)), /*#__PURE__*/React.createElement("span", {
    className: "inv-qrow-bar",
    "aria-hidden": "true"
  }, c.score != null && /*#__PURE__*/React.createElement("i", {
    className: c.score >= 60 ? "up" : c.score >= 40 ? "warn" : "down",
    style: {
      width: `${Math.max(2, Math.min(100, c.score))}%`
    }
  })), /*#__PURE__*/React.createElement("span", {
    className: "inv-qrow-score",
    title: c.score == null ? c.reason || "Not scored." : `${Math.round(c.score)} of 100 — ${c.scored_against || ""}`
  }, c.score == null ? "—" : Math.round(c.score)))), q.reason && /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, q.reason));
}

// ── earnings and revisions ────────────────────────────────────────────────

function InvRevisions({
  revisions,
  snap
}) {
  const r = revisions || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-revisions"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `What analysts expect, and whether
they are raising or cutting. Below four covering analysts this shows NOT RATED
rather than a number: three analysts agreeing is three people agreeing.

These estimates are on the analysts' ADJUSTED basis. Every trailing figure
elsewhere on this tab is GAAP, exactly as filed with the SEC. The two are shown
side by side and never combined inside one ratio.`
  }, "Earnings and revisions", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", r.label || "NOT RATED", r.analyst_count ? ` · ${Math.round(r.analyst_count)} analysts` : "")), /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "This year's earnings estimate",
    value: invPrice(r.current_year_eps),
    reason: r.reason,
    basis: r.basis,
    tip: "Analyst consensus for the current fiscal year, adjusted basis."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Next year's earnings estimate",
    value: invPrice(r.next_year_eps),
    reason: r.reason,
    basis: r.basis,
    tip: "Analyst consensus for the following fiscal year."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Forward earnings growth",
    value: invSignedPct(r.forward_growth_pct),
    tone: r.forward_growth_pct == null ? "" : r.forward_growth_pct >= 0 ? "up" : "down",
    reason: r.reason,
    tip: "Next year's estimate against this year's, both on the same analyst basis."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Revisions, last 30 days",
    value: invSignedPct(r.change_30d),
    tone: r.change_30d == null ? "" : r.change_30d >= 0 ? "up" : "down",
    reason: r.reason,
    basis: r.change_basis,
    tip: "Net share of covering analysts who RAISED rather than cut in the last 30 days. This is revision breadth, not a percentage change in the estimate itself."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Revisions, last 90 days",
    value: invSignedPct(r.change_90d),
    tone: r.change_90d == null ? "" : r.change_90d >= 0 ? "up" : "down",
    reason: r.reason,
    basis: r.change_basis,
    tip: "The same measure over ninety days."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Analyst coverage",
    value: r.analyst_count == null ? invNA : `${Math.round(r.analyst_count)} analysts`,
    reason: r.reason,
    tip: "How many analysts publish an estimate. Below four, the revision rating is withheld."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Analysts raising",
    value: r.up == null ? invNA : Math.round(r.up),
    reason: r.reason,
    tip: "Analysts who raised their estimate in the window."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Analysts cutting",
    value: r.down == null ? invNA : Math.round(r.down),
    reason: r.reason,
    tip: "Analysts who cut their estimate in the window."
  })), /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, r.gaap_note));
}

// ── peers ─────────────────────────────────────────────────────────────────

function InvPeers({
  peers,
  symbol,
  snap
}) {
  const p = peers || {};
  const rows = p.rows || [];
  const val = p.valuation || {};
  if (p.status === "building") {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty",
      title: "Each peer's filings are downloaded once and then cached, so this is slow only the first time a ticker is opened."
    }, "Building the peer group for ", symbol, " \u2014 each member's filings are read once and then cached. Refresh in a few seconds.");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-peers"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Comparable companies, chosen by the
industry code the SEC itself assigns to every filer. Most specific first:
a curated list, then the same four-digit code, then the three-digit industry
group, then the two-digit sector. A group needs at least five members to be a
distribution rather than a handful of companies; below that it falls back a
level and says so here.

A broad-benchmark group is shown for context but is NOT used to rank this
company's valuation — ranking a bank's earnings yield against a software
company's is arithmetic, not comparison.`
  }, "Peers", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", p.level || "none", p.sic ? ` · industry code ${p.sic}${p.sic_description ? ` (${p.sic_description})` : ""}` : "", p.curated ? " · curated list" : "")), p.reason && /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, p.reason), val.available && /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Group price to earnings, aggregate",
    value: invRatio(val.aggregate_pe),
    basis: val.basis,
    tip: "Total market value of the profitable members divided by their total earnings \u2014 what an index-level multiple actually means. NEVER an average of the members' ratios: one member earning almost nothing produces a ratio in the hundreds and drags an average somewhere no member is."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Median member price to earnings",
    value: invRatio(val.median_member_pe),
    tip: "The middle member's ratio, shown beside the aggregate so a group carried by one enormous constituent is visible."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "This company",
    value: invRatio(snap && snap.trailing_pe),
    tip: "This company's own trailing price to earnings, for comparison against the two figures beside it."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Members priced",
    value: `${val.n_profitable} of ${val.n}`,
    reason: val.n_excluded ? `${val.n_excluded} excluded as loss-making: ${(val.excluded || []).join(", ")}` : "",
    tip: "How many members had positive earnings and so could enter the aggregate multiple. Loss-makers are excluded and named \u2014 a group where half the members lose money is a different group."
  })), rows.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Ticker."
  }, "Ticker"), /*#__PURE__*/React.createElement("th", {
    title: "Company name as registered with the SEC."
  }, "Company"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Share price times shares outstanding."
  }, "Market value"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Share price divided by trailing GAAP earnings. Blank where earnings are negative."
  }, "Price / earnings"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Trailing earnings divided by price. Higher is cheaper."
  }, "Earnings yield"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Free cash flow divided by market value."
  }, "Cash flow yield"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Operating profit as a share of revenue."
  }, "Operating margin"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Revenue against the same twelve months a year earlier."
  }, "Revenue growth"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.symbol,
    className: r.symbol === symbol ? "inv-peer-self" : ""
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.symbol)), /*#__PURE__*/React.createElement("td", {
    className: "inv-peer-name",
    title: r.name
  }, r.name), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invMoney(r.market_cap, 0)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invRatio(r.trailing_pe)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(r.earnings_yield_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(r.fcf_yield_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(r.operating_margin_pct)), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${(r.revenue_growth_pct ?? 0) >= 0 ? "up" : "down"}`
  }, invSignedPct(r.revenue_growth_pct))))))));
}

// ── value trap ────────────────────────────────────────────────────────────

function InvValueTrap({
  trap
}) {
  const t = trap || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-trap"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Cheapness is not evidence of value —
it is the question. These signals ask whether the business is deteriorating,
because a stock reaching the cheap end of its own range BECAUSE things are
getting worse is the classic value trap.

Each signal fires on a direction of travel rather than on a level: the level
that counts as bad differs by industry, and the level is already scored under
Quality. A signal that cannot be measured is listed as such, never counted as
"fine".`
  }, "Value trap check", /*#__PURE__*/React.createElement("span", {
    className: `inv-traplevel inv-traplevel-${INV_TRAP_TONE[t.level] || "mut"}`
  }, t.level || "NOT RATED")), t.reason && /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, t.reason), /*#__PURE__*/React.createElement("div", {
    className: "inv-traplist"
  }, (t.active || []).map(a => /*#__PURE__*/React.createElement("div", {
    className: "inv-trapsig inv-trapsig-on",
    key: a.key,
    title: a.detail
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-trapsig-tag"
  }, "firing"), /*#__PURE__*/React.createElement("b", null, a.label), a.detail ? /*#__PURE__*/React.createElement("span", null, " \u2014 ", a.detail) : null)), (t.inactive || []).map(a => /*#__PURE__*/React.createElement("div", {
    className: "inv-trapsig inv-trapsig-off",
    key: a.key,
    title: a.detail
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-trapsig-tag"
  }, "not firing"), a.label, a.detail ? /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \u2014 ", a.detail) : null)), (t.unknown || []).map(a => /*#__PURE__*/React.createElement("div", {
    className: "inv-trapsig inv-trapsig-unknown",
    key: a.key,
    title: "This signal could not be measured from what this company reports. That is not the same as the signal being clear."
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-trapsig-tag"
  }, "unknown"), a.label, " ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014 could not be measured")))));
}

// ── drawdowns ─────────────────────────────────────────────────────────────

function InvDrawdowns({
  dd
}) {
  const d = dd || {};
  if (!d.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "inv-note"
    }, d.reason || "No drawdown history.");
  }
  const row = (label, w, tip) => w && /*#__PURE__*/React.createElement("div", {
    className: "inv-ddrow",
    key: label
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-ddrow-label",
    title: tip
  }, label), /*#__PURE__*/React.createElement("b", {
    className: "down"
  }, w.pct.toFixed(1), "%"), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, invShortDate(w.peak_date), " \u2192 ", invShortDate(w.trough_date)));
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-dd"
  }, row("Worst fall over the window", d.max, "The largest peak-to-trough fall anywhere in the price history available."), row("Worst fall in the last year", d.recent, "The largest peak-to-trough fall inside the last twelve months."), (d.windows || []).map(w => row(w.label, w, "How this stock behaved through that period, if the history reaches back that far.")), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: "Beta is deliberately absent. It compresses a whole distribution into one number that says nothing about what this stock actually did when things went wrong \u2014 which is what these rows show."
  }, "Context only. There is no Beta here on purpose: what this stock actually did in a fall is more use than a single number summarising how it usually moves."));
}

// ══════════════════════════════════════════════════════════════════════════
// PHASE 3 — what it is worth, what today's price implies, and how to own it
// ══════════════════════════════════════════════════════════════════════════

const INV_ENTRY_TONE = {
  "BUY SHARES": "up",
  "SELL PORTFOLIO SECURED PUT": "up",
  "BUY LEAPS": "up",
  "BUY-WRITE": "up",
  "BULL CALL SPREAD": "up",
  "TOSS UP": "warn",
  WAIT: "warn",
  AVOID: "down",
  "SPECIALIZED MODEL REQUIRED": "mut",
  "INSUFFICIENT DATA": "mut"
};
const INV_ENTRY_TIP = {
  "BUY SHARES": "The price is inside the buy zone and, on identical capital " + "at the same expiration, owning the shares outright beat every option " + "structure that could be priced.",
  "SELL PORTFOLIO SECURED PUT": "The price is above the buy zone, so the " + "shares are not a buy here — but a put at or below the price you actually " + "want to own it at pays enough to be worth the obligation. The strike is " + "never raised to find more premium: the acquisition price comes first.",
  "BUY LEAPS": "On identical capital at the same expiration, a long call " + "returned more than the shares. It receives no dividend and it expires, " + "both of which are already counted in the numbers below.",
  "BUY-WRITE": "Buying the shares and selling ONE call against them beat the " + "alternatives on the same account. This is a single expiration, not a " + "model of selling a call every week — that path depends on where the " + "stock went between rolls and belongs in a simulator.",
  "BULL CALL SPREAD": "A bounded structure won on the same capital. Both the " + "most it can make and the most it can lose are fixed at the outset.",
  "TOSS UP": "The ranking flips when the scenario weights move by a few " + "points, and those weights are assumptions rather than measurements. The " + "honest reading is that these structures are equivalent at this price.",
  WAIT: "Nothing here is worth doing at today's price. The exact level, and " + "the exact bid, that would change the answer are both stated.",
  AVOID: "A gate failed outright — the company is losing money, or several " + "deterioration signals are firing at once. No bullish structure is " + "recommended through that.",
  "SPECIALIZED MODEL REQUIRED": "A bank, insurer, broker or property trust. " + "Every valuation method here prices earnings or free cash flow, and " + "neither means for these businesses what it means for an operating " + "company.",
  "INSUFFICIENT DATA": "Something essential is missing — a price, readable " + "filings, or enough history. This is a real answer, not an error."
};
const INV_CONFIDENCE_TONE = {
  HIGH: "up",
  MODERATE: "warn",
  LOW: "down",
  UNRELIABLE: "down"
};
const INV_CONFIDENCE_TIP = "How far apart the valuation methods are. Within 25% between the highest " + "and lowest is HIGH, 25–50% MODERATE, 50–100% LOW, wider is UNRELIABLE. " + "A single method standing alone is capped at MODERATE, because nothing " + "disagreeing with it is not the same as something agreeing with it. " + "These bands are a stated convention, not a tested result.";
function InvEntryPill({
  entry
}) {
  const label = (entry || {}).verdict || "INSUFFICIENT DATA";
  const tone = INV_ENTRY_TONE[label] || "mut";
  return /*#__PURE__*/React.createElement("span", {
    className: `inv-entry inv-entry-${tone}`,
    title: INV_ENTRY_TIP[label]
  }, label);
}

// The six numbers §18 asks for above everything else.
function InvDecisionBar({
  snap,
  fair,
  er,
  structures
}) {
  const f = fair || {};
  const comp = (structures || {}).comparison || {};
  const zone = f.buy_zone;
  const gap = f.premium_to_buy_zone_pct;
  const cell = (label, value, tip, tone) => /*#__PURE__*/React.createElement("div", {
    className: "inv-dcell",
    key: label
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-dcell-label",
    title: tip
  }, label), /*#__PURE__*/React.createElement("b", {
    className: `inv-dcell-val${tone ? ` ${tone}` : ""}${value === invNA ? " inv-na" : ""}`,
    title: tip
  }, value));
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-decision"
  }, cell("Current price", invPrice(snap && snap.price), "The live share price every number on this page is measured against."), cell("Bear / Base / Bull", f.available ? `${invPrice(f.bear)} / ${invPrice(f.base)} / ${invPrice(f.bull)}` : invNA, "Three defensible values, not an average of methods. The base is the " + "single highest-confidence method that could be built; the bear is " + "the most pessimistic value any valid method produced and the bull " + "the most optimistic, so methods that disagree widen the range " + "instead of being blended into a false middle."), cell("Fair value confidence", f.confidence_level || invNA, INV_CONFIDENCE_TIP + (f.confidence && f.confidence.reason ? `\n\n${f.confidence.reason}` : ""), INV_CONFIDENCE_TONE[f.confidence_level] || ""), cell("Buy zone", invPrice(zone), (f.credit_note || "") + "\n\nThis is the price at which this analysis " + "says the shares are worth owning. It gates everything below it: a " + "put strike above this level is never considered, however rich the " + "premium."), cell("Discount to buy zone", gap == null ? invNA : `${gap >= 0 ? "+" : ""}${gap.toFixed(1)}%`, "How far today's price sits above (positive) or below (negative) the " + "buy zone. Below zero the shares themselves qualify.", gap == null ? "" : gap <= 0 ? "up" : "down"), cell("Expected return", (er || {}).weighted_total_cagr_pct == null ? invNA : `${er.weighted_total_cagr_pct.toFixed(1)}% a year`, "Probability-weighted total return over the scenario horizon, " + "including dividends carried to the horizon as cash at the matching " + "Treasury yield. The weights are assumptions and are shown below.", (er || {}).weighted_total_cagr_pct == null ? "" : er.weighted_total_cagr_pct >= 0 ? "up" : "down"), cell("Preferred structure", comp.preferred || invNA, "Which way of taking this position returned most on IDENTICAL " + "capital, at an identical expiration, on identical scenario prices. " + "Never ranked on return-on-premium or on buying-power reduction — " + "those denominators reward a structure for the money it did not put " + "to work."));
}

// ── fair value ────────────────────────────────────────────────────────────

function InvFairValue({
  fair,
  snap
}) {
  const f = fair || {};
  const methods = f.methods || [];
  if (!f.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty",
      title: "Every method here prices earnings or cash generation on a stated basis. Where none of them can be built from what is on file, this says so rather than producing a number with nothing behind it."
    }, f.reason || "No fair value could be built.");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-fv"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Three valuation methods, each
carrying the basis it was computed on. They are NOT averaged: averaging a good
method with a bad one produces a number worse than the good method alone and
hides which was which. The base value is the single highest-confidence method;
the others set the WIDTH of the range and set the confidence, because methods
that disagree are methods that should not be trusted to two decimal places.

Basis never mixes. Trailing GAAP earnings against comparable companies' GAAP
trailing multiples is a comparison. Trailing GAAP earnings against their
FORWARD multiples would price one company's audited past with another's
forecast future, so that combination is simply not offered.

LOWER confidence LOWERS the price we will pay. The obvious-looking "margin of
safety times confidence" runs the wrong way: it shrinks the discount demanded
exactly when the valuation is least trustworthy. Instead the confidence decides
how far ABOVE the pessimistic value the credited value is allowed to travel.`
  }, "Fair value", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 base from ", f.base_method_label || "—", " · ", f.n_methods, " of ", methods.length, " methods usable"), /*#__PURE__*/React.createElement("span", {
    className: `inv-conf inv-conf-${INV_CONFIDENCE_TONE[f.confidence_level] || "mut"}`,
    title: INV_CONFIDENCE_TIP
  }, f.confidence_level)), /*#__PURE__*/React.createElement("div", {
    className: "inv-fv-band",
    title: `Bear ${invPrice(f.bear)}, base
${invPrice(f.base)}, bull ${invPrice(f.bull)}. The pin is today's price.`
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-fv-track"
  }, /*#__PURE__*/React.createElement("i", {
    className: "inv-fv-fill"
  }), /*#__PURE__*/React.createElement("em", {
    className: "inv-fv-base",
    style: {
      left: invFvAt(f, f.base)
    }
  }), /*#__PURE__*/React.createElement("b", {
    className: "inv-fv-now",
    style: {
      left: invFvAt(f, snap && snap.price)
    }
  }), /*#__PURE__*/React.createElement("s", {
    className: "inv-fv-zone",
    style: {
      left: invFvAt(f, f.buy_zone)
    }
  })), /*#__PURE__*/React.createElement("span", {
    className: "inv-fv-ends"
  }, /*#__PURE__*/React.createElement("span", {
    title: "The most pessimistic value any valid method produced."
  }, "Bear ", invPrice(f.bear)), /*#__PURE__*/React.createElement("span", {
    title: "The single highest-confidence method's value."
  }, "Base ", invPrice(f.base)), /*#__PURE__*/React.createElement("span", {
    title: "The most optimistic value any valid method produced."
  }, "Bull ", invPrice(f.bull)))), /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Credited fair value",
    value: invPrice(f.credited),
    tip: f.credit_note,
    basis: `Bear + ${f.confidence_credit == null ? "—" : `${Math.round(f.confidence_credit * 100)}%`} × (Base − Bear)`
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Margin of safety required",
    value: f.margin_of_safety == null ? invNA : `${Math.round(f.margin_of_safety * 100)}%`,
    tip: "Taken off the credited value to set the buy zone. It is a stated requirement, not a measurement."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Buy zone",
    value: invPrice(f.buy_zone),
    tip: "Credited fair value less the margin of safety. Below this price the shares qualify; above it, only a put struck at or below this level does."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Price against the buy zone",
    value: f.premium_to_buy_zone_pct == null ? invNA : `${f.premium_to_buy_zone_pct >= 0 ? "+" : ""}${f.premium_to_buy_zone_pct.toFixed(1)}%`,
    tone: f.premium_to_buy_zone_pct == null ? "" : f.premium_to_buy_zone_pct <= 0 ? "up" : "down",
    tip: "Negative means today's price is inside the buy zone."
  })), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table inv-fv-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which valuation method."
  }, "Method"), /*#__PURE__*/React.createElement("th", {
    title: "What the method priced and at what multiple. Every method carries its basis; the two are never mixed inside one calculation."
  }, "Basis"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The pessimistic value this method produced."
  }, "Bear"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "This method's central value."
  }, "Base"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The optimistic value this method produced."
  }, "Bull"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How many observations or comparable companies stood behind it."
  }, "Observations"))), /*#__PURE__*/React.createElement("tbody", null, methods.map(m => /*#__PURE__*/React.createElement("tr", {
    key: m.key,
    className: m.key === f.base_method ? "inv-peer-self" : ""
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, m.label), m.key === f.base_method && /*#__PURE__*/React.createElement("span", {
    className: "inv-tag",
    title: "The highest-confidence method available, and the one the base value comes from."
  }, "base")), /*#__PURE__*/React.createElement("td", {
    className: "inv-peer-name",
    title: m.basis
  }, m.basis), m.available ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPrice(m.bear)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, /*#__PURE__*/React.createElement("b", null, invPrice(m.base))), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPrice(m.bull)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, m.n ? invCount(m.n) : invNA)) : /*#__PURE__*/React.createElement("td", {
    className: "inv-fv-why",
    colSpan: 4,
    title: m.reason
  }, m.reason)))))), f.normalized_fcf && !f.normalized_fcf.available && /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, f.normalized_fcf.reason));
}
function invFvAt(f, v) {
  const lo = Math.min(f.bear, f.buy_zone == null ? f.bear : f.buy_zone);
  const hi = f.bull;
  if (v == null || lo == null || hi == null || hi <= lo) return "0%";
  return `${Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100))}%`;
}

// ── expected return bridge ────────────────────────────────────────────────

const INV_SCENARIOS = [["bear", "Bear"], ["base", "Base"], ["bull", "Bull"]];
function InvExpectedReturn({
  er
}) {
  const e = er || {};
  if (!e.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty",
      title: "The bridge needs a positive price, positive trailing earnings, a growth record long enough to take percentiles of, and a multiple this company has actually traded at."
    }, e.reason || "No expected return could be built.");
  }
  const g = e.growth || {};
  const m = e.multiples || {};
  const probs = e.probabilities || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-er"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Where today's price gets you over
${e.years} years, scenario by scenario.

Three refusals live in this panel. Buyback yield is NOT added on top: a
buyback shrinks the share count, which raises earnings per share, which is
already the first bar — adding it again counts the same cash twice. Dividend
yield is NOT added to the price return: a dividend is cash on a date, so it is
compounded to the horizon at the matching Treasury yield and enters terminal
wealth, which is what actually happens to it. And the multiple at the horizon
is on the SAME basis as the multiple today; a bridge that starts on trailing
GAAP and lands on a forward adjusted multiple has manufactured most of its own
answer.`
  }, "Expected return", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", e.years, "-year horizon", e.rate && e.rate.pct != null ? ` · dividends reinvested at ${e.rate.pct.toFixed(2)}%` : "")), /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Probability-weighted return",
    value: e.weighted_total_cagr_pct == null ? invNA : `${e.weighted_total_cagr_pct.toFixed(2)}% a year`,
    tone: e.weighted_total_cagr_pct == null ? "" : e.weighted_total_cagr_pct >= 0 ? "up" : "down",
    tip: "Terminal wealth across the three scenarios, weighted by the probabilities shown, expressed as a compound annual rate on today's price."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Scenario weights",
    value: `${Math.round((probs.bear || 0) * 100)} / ${Math.round((probs.base || 0) * 100)} / ${Math.round((probs.bull || 0) * 100)}`,
    tip: "Bear / base / bull. These are ASSUMPTIONS, not facts. The structure comparison below re-runs its ranking under nearby weightings and says TOSS UP when the winner changes."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Dividends, last twelve months",
    value: e.dps_ttm == null ? invNA : invPrice(e.dps_ttm),
    reason: (e.dividends_detail || {}).reason,
    basis: (e.dividends_detail || {}).basis,
    tip: "Declared per common share, as reported to the SEC. Grown with earnings along each scenario path and compounded to the horizon at the matching Treasury yield."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Analyst forward growth, for comparison",
    value: invSignedPct((e.forward_growth_context || {}).value),
    reason: (e.forward_growth_context || {}).reason,
    basis: (e.forward_growth_context || {}).basis,
    tip: (e.forward_growth_context || {}).note
  })), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which scenario."
  }, "Scenario"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: `Annual earnings growth. ${g.note || ""}`
  }, "Earnings growth"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Earnings per share at the horizon."
  }, "Earnings at horizon"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: `The multiple the shares are assumed to trade at. ${m.note || ""}`
  }, "Exit multiple"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Share price at the horizon: earnings times the exit multiple."
  }, "Price at horizon"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Dividends received over the period, carried forward to the horizon at the matching Treasury yield."
  }, "Dividends"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Share price at the horizon plus the dividends, per share."
  }, "Terminal wealth"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Compound annual total return on today's price, including the dividends."
  }, "Total return"))), /*#__PURE__*/React.createElement("tbody", null, INV_SCENARIOS.map(([key, label]) => {
    const s = (e.scenarios || {})[key] || {};
    if (!s.available) {
      return /*#__PURE__*/React.createElement("tr", {
        key: key
      }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, label)), /*#__PURE__*/React.createElement("td", {
        colSpan: 7,
        className: "inv-fv-why"
      }, s.reason || "Not available."));
    }
    return /*#__PURE__*/React.createElement("tr", {
      key: key
    }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, label), /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, " ", Math.round((probs[key] || 0) * 100), "%")), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invSignedPct(s.growth_pct)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(s.eps_end)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invRatio(s.multiple_end)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(s.price_end)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice((s.dividends || {}).value)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(s.terminal_wealth)), /*#__PURE__*/React.createElement("td", {
      className: `scan-num ${s.total_cagr_pct >= 0 ? "up" : "down"}`
    }, s.total_cagr_pct == null ? invNA : `${s.total_cagr_pct.toFixed(2)}%`));
  })))), INV_SCENARIOS.map(([key, label]) => {
    const s = (e.scenarios || {})[key] || {};
    if (!s.available || !s.contributions) return null;
    const scale = Math.max(...s.contributions.map(c => Math.abs(c.value)), 1e-9);
    const total = s.contributions.reduce((a, c) => a + c.value, 0);
    return /*#__PURE__*/React.createElement("div", {
      className: "inv-bars inv-er-bars",
      key: key
    }, /*#__PURE__*/React.createElement("div", {
      className: "inv-bar-head",
      title: s.note
    }, label, " \u2014 where the return comes from", /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, " \xB7 log points a year, and they add up exactly")), s.contributions.map(c => /*#__PURE__*/React.createElement("div", {
      className: "inv-bar-row",
      key: c.driver
    }, /*#__PURE__*/React.createElement("span", {
      className: "inv-bar-label",
      title: INV_ER_DRIVER_TIP[c.driver] || c.driver
    }, c.driver), /*#__PURE__*/React.createElement("span", {
      className: "inv-bar-track"
    }, /*#__PURE__*/React.createElement("i", {
      className: c.value >= 0 ? "up" : "down",
      style: {
        width: `${Math.min(100, Math.abs(c.value) / scale * 100)}%`
      }
    })), /*#__PURE__*/React.createElement("b", {
      className: c.value >= 0 ? "up" : "down"
    }, `${c.value >= 0 ? "+" : ""}${c.value.toFixed(2)}`))), /*#__PURE__*/React.createElement("div", {
      className: "inv-bar-row inv-bar-total"
    }, /*#__PURE__*/React.createElement("span", {
      className: "inv-bar-label",
      title: "The three bars above add up to exactly this number, because earnings times the multiple IS the price and the dividend leg is the rest of terminal wealth. The reconciliation is asserted in the test suite."
    }, "Total"), /*#__PURE__*/React.createElement("span", {
      className: "inv-bar-track"
    }), /*#__PURE__*/React.createElement("b", {
      className: total >= 0 ? "up" : "down"
    }, `${total >= 0 ? "+" : ""}${total.toFixed(2)}`)));
  }), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: g.note || ""
  }, g.history_note || g.note || "", g.horizon_matched === false && " "), (g.basis || {}).reason && /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, g.basis.reason), m.note && /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, m.note));
}
const INV_ER_DRIVER_TIP = {
  "Earnings growth": "How much of the annual return comes from the company earning more per share.",
  "Multiple change": "How much comes from the market paying a different number of times those earnings. Negative means the shares are assumed to de-rate from where they trade today.",
  "Dividends": "How much comes from cash paid out and carried to the horizon at the matching Treasury yield. It is never added to the price return as a percentage."
};

// ── implied expectations ──────────────────────────────────────────────────

function InvImplied({
  imp
}) {
  const x = imp || {};
  if (!x.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty",
      title: "The audit needs an enterprise value \u2014 market value plus net debt \u2014 and a positive normalized free cash flow. Where either is missing it says so rather than solving for a number with a hole in it."
    }, x.reason || "No implied-expectations audit could be built.");
  }
  const grid = x.grid || {};
  const dr = x.discount_rate || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-imp"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `This is an EXPECTATIONS instrument,
not a valuation. A forward discounted cash flow answers "what is it worth" with
whatever growth rate somebody typed in. This runs the same model backwards and
solves for ONE unknown — the free-cash-flow growth today's price is already
paying for — which is a question with an honest answer.

Solved by bisection on a bracketed interval rather than Newton-Raphson: the
present value rises monotonically with the growth rate for positive cash flow,
so bisection cannot diverge, cannot need a derivative and cannot wander onto a
second root.

The discount rate is deliberately NOT a per-company weighted average cost of
capital. A beta estimated from five years of daily returns moves by whole
points depending on the window chosen, and that false precision would
propagate into every cell of the grid below.`
  }, "What the market is already paying for", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", x.years, "-year reverse discounted cash flow")), /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Market-implied growth in free cash flow",
    value: (x.implied || {}).growth_pct == null ? invNA : `${x.implied.growth_pct.toFixed(1)}% a year`,
    basis: `${x.years} explicit years, then ${x.terminal_growth_pct}% for ever`,
    tip: "The compound growth rate that makes the discounted cash flows equal today's enterprise value. One unknown, solved numerically."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Across the assumption grid",
    value: grid.min_pct == null ? invNA : `${grid.min_pct.toFixed(1)}% to ${grid.max_pct.toFixed(1)}%`,
    tip: "The same solve at a discount rate one point either side and at three terminal growth rates. A single implied-growth number reads like a measurement; the range is what makes it read like the output of assumptions somebody chose."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "What it has actually delivered",
    value: x.historical_fcf_growth_pct == null ? invNA : `${x.historical_fcf_growth_pct.toFixed(1)}% a year`,
    reason: x.historical_note,
    tip: "Compound growth of trailing free cash flow across the reported history."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Expectations gap",
    value: (x.gap || {}).gap_pp == null ? invNA : `${x.gap.gap_pp >= 0 ? "+" : ""}${x.gap.gap_pp.toFixed(1)} points`,
    tone: (x.gap || {}).gap_pp == null ? "" : x.gap.gap_pp > 0 ? "down" : "up",
    reason: (x.gap || {}).reason,
    tip: (x.gap || {}).note || `The implied rate minus the
realized one. A positive gap is not a sell signal; it is the size of the
improvement being paid for in advance.`
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Enterprise value",
    value: invMoney(x.enterprise_value),
    basis: "Market value of the shares plus net debt",
    tip: "What buying the whole company outright would cost, including taking on its borrowings net of cash."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Normalized free cash flow",
    value: invMoney((x.normalized_fcf || {}).value),
    reason: (x.normalized_fcf || {}).reason,
    basis: `Median of the last ${(x.normalized_fcf || {}).n || "—"} trailing-twelve-month readings`,
    tip: "The median rather than the latest, because one quarter of unusual working capital should not become the company's permanent cash generation."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Discount rate assumed",
    value: dr.pct == null ? invNA : `${dr.pct.toFixed(2)}%`,
    basis: dr.basis,
    reason: dr.reason,
    tip: "A stated assumption, displayed and varied across the grid below \u2014 not a computed cost of capital dressed up as a measurement."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Consensus growth",
    value: invNA,
    reason: (x.consensus_growth || {}).reason,
    tip: "No free source publishes a five-year free-cash-flow consensus. This dashboard will not print one it does not have."
  })), grid.available && /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table inv-grid-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Rows are the discount rate; columns the growth rate assumed for ever after the explicit period. Both are assumptions, which is why this is a grid rather than a number."
  }, "Discount rate \uFF3C terminal growth"), (grid.terminal_growths_pct || []).map(g => /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    key: g,
    title: `Free cash flow assumed to grow ${g}% a year for ever after the explicit period.`
  }, g.toFixed(1), "%")))), /*#__PURE__*/React.createElement("tbody", null, (grid.cells || []).map((row, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, (grid.rates_pct || [])[i] == null ? invNA : `${grid.rates_pct[i].toFixed(2)}%`)), row.map((c, j) => /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    key: j,
    title: c.reason || ""
  }, c.growth_pct == null ? invNA : `${c.growth_pct.toFixed(1)}%`))))))), /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, x.note));
}

// ── the structure comparator ──────────────────────────────────────────────

const INV_STRUCT_TIP = {
  SHARES: "Buy 100 shares and hold them to the same date every option below is marked at. The only structure where the capital and the notional are the same number.",
  "PORTFOLIO SECURED PUT": "Sell one put and hold the FULL strike notional against it — strike × 100, never the broker's buying-power reduction. If the stock goes to zero the obligation is the full strike, whatever the margin clerk asked for.",
  LEAPS: "Buy one long-dated call. The unspent capital is not free money: it earns the matching Treasury yield and the comparison counts it. The call receives no dividend.",
  "BUY-WRITE": "Buy 100 shares and sell ONE call against them. A single expiration — this is deliberately not a model of selling a call every week, because that path depends on where the stock went between rolls.",
  "BULL CALL SPREAD": "Buy the lower call, sell the higher one, at the prices actually quoted. Both the maximum gain and the maximum loss are fixed at the outset."
};
function InvComparator({
  comp,
  snap
}) {
  const c = comp || {};
  const [greeks, setGreeks] = useState(false);
  if (!c.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty",
      title: "Every structure is priced from the chain the app's own loader returns. Some tickers have no listed options at all, and long-dated contracts do not exist on every name."
    }, c.reason || "No structure comparison could be built.");
  }
  const rows = c.rows || [];
  const sens = c.sensitivity || {};
  const dc = c.downside_context || {};
  const sp = c.scenario_prices || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-comp"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Identical capital. Identical
horizon. Identical scenario prices. That is the whole point of this table.

Every row starts with the same account — 100 times the current share price,
which is one round lot and therefore what one contract controls — and whatever
a structure does not spend stays in that account earning the matching Treasury
yield to expiration. Each is then judged on what the WHOLE account is worth at
expiration.

Nothing is ranked on return-on-premium, return-on-margin or
return-on-buying-power-reduction. Those denominators reward a structure for the
money it did NOT put to work, which is what makes leverage look like skill.`
  }, "Structure comparison", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", c.capital == null ? "" : invMoney(c.capital, 0), " to ", invShortDate(c.expiration), c.dte == null ? "" : `, ${Math.round(c.dte)} days`), c.toss_up && /*#__PURE__*/React.createElement("span", {
    className: "inv-conf inv-conf-warn",
    title: sens.reason
  }, "TOSS UP \u2014 PROBABILITY SENSITIVE")), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: c.horizon_note
  }, c.horizon_note), /*#__PURE__*/React.createElement("div", {
    className: "inv-scenario-strip",
    title: `The three fundamental
scenario prices at THIS expiration, built from the same fair-value assumptions
as the three-year bridge. The multiple travels only part of the way to its
target over a shorter horizon — a forty-five-day contract does not re-rate a
company — which is why these separate less than the long-horizon scenarios do.`
  }, INV_SCENARIOS.map(([k, label]) => /*#__PURE__*/React.createElement("span", {
    key: k,
    className: "inv-scenario-pill"
  }, label, " ", invPrice(sp[k])))), dc.available && /*#__PURE__*/React.createElement("div", {
    className: "inv-warn",
    title: dc.note
  }, /*#__PURE__*/React.createElement("b", null, "The fundamental scenarios are not a price distribution."), " ", dc.note), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table inv-comp-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "The structure."
  }, "Structure"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Cash actually committed to the position out of the comparison account."
  }, "Capital"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The full economic size of the obligation \u2014 strike \xD7 100 for a short put, 100 shares at today's price for stock, the width for a spread. Not the same as the maximum loss, and both are shown."
  }, "Notional"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The worst the whole account can end at, against what it started with."
  }, "Max loss"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The share price at which the position breaks even at expiration."
  }, "Breakeven"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Profit or loss on the whole comparison account in the bear scenario."
  }, "Bear"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Profit or loss on the whole comparison account in the base scenario."
  }, "Base"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Profit or loss on the whole comparison account in the bull scenario."
  }, "Bull"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Terminal wealth across the three scenarios weighted by the probabilities shown above."
  }, "Weighted wealth"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Probability-weighted return on the FULL comparison capital, annualized. This is the ranking column."
  }, "Return a year"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The worst of the three scenarios, in dollars."
  }, "Worst case"), /*#__PURE__*/React.createElement("th", {
    title: "Whether the contract could actually be traded: open interest and the bid-ask spread."
  }, "Liquidity"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How many earnings reports the position is expected to sit through. Only the first date is published; the rest step forward a quarter at a time."
  }, "Events"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => {
    const t = r.terminal || {};
    const liq = r.liquidity || {};
    if (!r.eligible) {
      return /*#__PURE__*/React.createElement("tr", {
        key: r.kind,
        className: "inv-row-off"
      }, /*#__PURE__*/React.createElement("td", {
        title: INV_STRUCT_TIP[r.kind]
      }, /*#__PURE__*/React.createElement("b", null, r.kind)), /*#__PURE__*/React.createElement("td", {
        colSpan: 12,
        className: "inv-fv-why",
        title: r.reason
      }, r.reason));
    }
    const top = r.kind === c.preferred;
    return /*#__PURE__*/React.createElement("tr", {
      key: r.kind,
      className: top ? "inv-peer-self" : ""
    }, /*#__PURE__*/React.createElement("td", {
      title: INV_STRUCT_TIP[r.kind]
    }, /*#__PURE__*/React.createElement("b", null, r.kind), top && /*#__PURE__*/React.createElement("span", {
      className: "inv-tag",
      title: "Highest return on the full comparison capital."
    }, "preferred"), /*#__PURE__*/React.createElement("div", {
      className: "inv-contract-line",
      title: r.notional_note
    }, invContractLine(r))), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invMoney(r.capital_allocated, 0)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num",
      title: r.notional_note
    }, invMoney(r.notional, 0)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num down"
    }, invMoney(r.max_loss, 0)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(r.breakeven)), INV_SCENARIOS.map(([k]) => /*#__PURE__*/React.createElement("td", {
      className: `scan-num ${((t[k] || {}).pnl || 0) >= 0 ? "up" : "down"}`,
      key: k,
      title: `Ends at ${invPrice((t[k] || {}).stock_price)} a share.`
    }, invMoney((t[k] || {}).pnl, 0))), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invMoney(r.weighted_wealth, 0)), /*#__PURE__*/React.createElement("td", {
      className: `scan-num ${(r.weighted_annualized_pct || 0) >= 0 ? "up" : "down"}`
    }, /*#__PURE__*/React.createElement("b", null, r.weighted_annualized_pct == null ? invNA : `${r.weighted_annualized_pct.toFixed(2)}%`)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num down"
    }, invMoney(r.worst_pnl, 0)), /*#__PURE__*/React.createElement("td", {
      title: liq.label || ""
    }, liq.ok == null ? "—" : liq.ok ? "tradeable" : "thin"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num",
      title: (r.events_crossed || {}).reason || (r.events_crossed || {}).note || ""
    }, (r.events_crossed || {}).count == null ? invNA : (r.events_crossed || {}).count));
  })))), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: sens.reason
  }, sens.reason), /*#__PURE__*/React.createElement("details", {
    className: "inv-exp inv-exp-inner",
    open: greeks,
    onToggle: ev => setGreeks(ev.target.open)
  }, /*#__PURE__*/React.createElement("summary", {
    title: "Delta, implied volatility and the rest. Useful to read, and deliberately not what decides anything here."
  }, "Greeks and contract detail"), /*#__PURE__*/React.createElement("div", {
    className: "inv-exp-body"
  }, /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "The structure."
  }, "Structure"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How much the contract moves per dollar of share price. Filled from the app's own Black-Scholes when the feed does not carry it \u2014 the source is on the cell."
  }, "Delta"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The contract's own implied volatility."
  }, "Implied volatility"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "What the contract would be worth if it expired today."
  }, "Intrinsic"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Everything above intrinsic value \u2014 what time and volatility are being charged for."
  }, "Extrinsic"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Extrinsic value divided by the years to expiration: what the optionality costs per year."
  }, "Extrinsic a year"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Open interest."
  }, "Open interest"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Bid-ask spread as a share of the mid price."
  }, "Spread"))), /*#__PURE__*/React.createElement("tbody", null, rows.filter(r => r.eligible && r.contract).map(r => {
    const k = r.contract || {};
    const liq = r.liquidity || {};
    return /*#__PURE__*/React.createElement("tr", {
      key: r.kind
    }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.kind)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num",
      title: k.delta_source || ""
    }, k.delta == null ? invNA : k.delta.toFixed(3)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, k.iv == null ? invNA : `${(k.iv * 100).toFixed(1)}%`), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, k.intrinsic == null ? invNA : invPrice(k.intrinsic)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, k.extrinsic == null ? invNA : invPrice(k.extrinsic)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, k.extrinsic_per_year == null ? invNA : invPrice(k.extrinsic_per_year)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, liq.open_interest == null ? invNA : invCount(liq.open_interest)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, liq.spread_pct == null ? invNA : `${liq.spread_pct.toFixed(1)}%`));
  })))), rows.filter(r => r.eligible).map(r => (r.notes || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    key: r.kind
  }, /*#__PURE__*/React.createElement("b", null, r.kind), " \u2014 ", (r.notes || []).join(" "))))));
}
function invContractLine(r) {
  const k = r.contract || {};
  if (k.long_strike != null) {
    return `${invPrice(k.long_strike)} / ${invPrice(k.short_strike)} · debit ${invPrice(k.net_debit)}`;
  }
  if (k.call_strike != null) {
    return `call ${invPrice(k.call_strike)} · credit ${invPrice(k.call_credit)}`;
  }
  if (k.credit != null) {
    return `${invPrice(k.strike)} put · credit ${invPrice(k.credit)} · assigned at ${invPrice(k.effective_assignment_cost)}`;
  }
  if (k.debit != null) {
    return `${invPrice(k.strike)} call · debit ${invPrice(k.debit)}`;
  }
  return `${k.shares || 100} shares at ${invPrice(k.entry)}`;
}

// ── the short-dated put optimizer ─────────────────────────────────────────

function InvBestPut({
  put,
  market,
  snap
}) {
  const p = put || {};
  const best = p.best;
  const rows = p.candidates || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-put"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `The acquisition price comes FIRST. A
strike above the buy zone is never considered however rich the premium — a put
sold above the price you wanted to pay is not a way of buying the business
cheaply, it is a bet with the business attached.

Nothing here is decided by a delta band, an implied-volatility rank or a
preferred number of days to expiration. Those are all displayed because they
are useful to read, but the choice is made on what the whole comparison
account is worth, which is a measurement rather than a convention.

Where no put at or below the buy zone pays enough, the answer is WAIT —
INSUFFICIENT PREMIUM AT DESIRED OWNERSHIP PRICE. The strike is never raised to
find premium.

Risk is sized at strike × 100 — the full notional — never the buying-power
reduction a broker shows.`
  }, "Best put at or below the buy zone", p.buy_zone != null && /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 buy zone ", invPrice(p.buy_zone))), p.headline && /*#__PURE__*/React.createElement("div", {
    className: "inv-warn",
    title: "The strike is never raised to find premium. That would be selling a put above the price this analysis says the shares are worth, which is the opposite of the point."
  }, /*#__PURE__*/React.createElement("b", null, p.headline)), p.reason && /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, p.reason), best && /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Strike",
    value: invPrice((best.contract || {}).strike),
    basis: `${invShortDate(best.expiration)}, ${Math.round(best.dte || 0)} days`,
    tip: "At or below the buy zone by construction."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Premium",
    value: invPrice((best.contract || {}).credit),
    basis: "Bid \u2014 the only price a resting seller is promised",
    tip: "The credit basis is the BID throughout this dashboard. A mid-price fill is a hope, not a floor."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Effective purchase price",
    value: invPrice((best.contract || {}).effective_assignment_cost),
    tip: "The strike less the premium already received. This is what the shares actually cost if the put is assigned."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Full notional risk",
    value: invMoney(best.notional, 0),
    basis: "Strike \xD7 100",
    tip: "What can be put to you. The position is sized against this, never against the buying-power reduction a broker shows \u2014 if the stock goes to zero the obligation is the full strike whatever the margin clerk asked for."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Premium on full notional, annualized",
    value: invPct((best.contract || {}).annualized_on_notional_pct),
    tip: "The credit as a share of the full strike notional, scaled to a year. Shown for reading, not for ranking."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Below the buy zone",
    value: invPct((best.contract || {}).below_buy_zone_pct),
    tip: "How far under the price this analysis says the shares are worth the strike sits."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Return on the full comparison capital",
    value: best.weighted_annualized_pct == null ? invNA : `${best.weighted_annualized_pct.toFixed(2)}% a year`,
    tone: p.clears_hurdle ? "up" : "down",
    tip: "Probability-weighted, on the same account every other structure is measured on."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Hurdle",
    value: p.hurdle_pct == null ? invNA : `${p.hurdle_pct.toFixed(2)}% a year`,
    reason: p.clears_hurdle === false ? "This put does not clear it." : "",
    tip: "The matching Treasury yield plus a stated cushion \u2014 what the same cash earns doing nothing. A put that pays less than this is not worth the obligation."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Bid that would qualify",
    value: p.required_bid == null ? invNA : invPrice(p.required_bid),
    tip: "Solved rather than searched for: the wealth of a secured put is (capital + premium) \xD7 (1+r)^T less the expected obligation, so the premium that reaches the hurdle is plain algebra."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Delta",
    value: (best.contract || {}).delta == null ? invNA : (best.contract || {}).delta.toFixed(3),
    basis: (best.contract || {}).delta_source,
    tip: "Displayed, not used to choose. There is deliberately no 0.15\u20130.25 delta rule here."
  })), best && best.market_risk && /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Chance of finishing in the money",
    value: invPct(best.market_risk.p_itm_model == null ? null : best.market_risk.p_itm_model * 100),
    basis: best.market_risk.prob_basis,
    tip: "A MODEL probability from the app's own volatility forecast, not a market-implied one and not a real-world frequency. It answers a different question from the fundamental scenarios and is kept in its own column."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Chance of touching the strike",
    value: invPct(best.market_risk.p_touch_model == null ? null : best.market_risk.p_touch_model * 100),
    tip: "The probability the price trades through the strike at any point before expiration, not just at the end."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Expected shortfall in the worst 5%",
    value: invPrice(best.market_risk.es5_per_share),
    tip: "The average loss per share across the worst one-in-twenty outcomes, after the credit. This is what the fundamental bear case does not tell you."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Model edge per share",
    value: invPrice(best.market_risk.ev_per_share),
    tone: (best.market_risk.ev_per_share || 0) >= 0 ? "up" : "down",
    tip: "The bid less the model's fair value at the volatility forecast, less costs. Positive means the market is paying more than the app's own volatility model says the risk is worth."
  })), market && market.available && /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Implied volatility, 30 day",
    value: market.iv30 == null ? invNA : `${(market.iv30 * 100).toFixed(1)}%`,
    basis: market.iv30_method,
    tip: "What the option market is charging for the next month."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Expected realized volatility, 30 day",
    value: market.erv30 == null ? invNA : `${(market.erv30 * 100).toFixed(1)}%`,
    basis: market.erv_method,
    tip: "What the app's own walk-forward volatility model expects over the same month. This is a THIRTY-DAY forecast and is deliberately never held up against a one-year contract."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Volatility risk premium",
    value: (market.vrp || {}).vrp_points == null ? invNA : `${market.vrp.vrp_points.toFixed(1)} points`,
    tone: ((market.vrp || {}).vrp_points || 0) >= 0 ? "up" : "down",
    tip: "Implied minus expected realized. Positive is the seller being paid more than the model thinks the risk costs."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Premium type",
    value: (market.classification || {}).class || invNA,
    tip: "Whether the premium on offer is compensation for ordinary volatility, for a scheduled event, or both."
  })), rows.length > 1 && /*#__PURE__*/React.createElement("details", {
    className: "inv-exp inv-exp-inner"
  }, /*#__PURE__*/React.createElement("summary", {
    title: "Every put at or below the buy zone with a bid on it, best first."
  }, "All qualifying strikes (", rows.length, ")"), /*#__PURE__*/React.createElement("div", {
    className: "inv-exp-body"
  }, /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Expiration date."
  }, "Expires"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Days to expiration."
  }, "Days"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Strike price."
  }, "Strike"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The bid \u2014 what a resting seller is promised."
  }, "Bid"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Strike less the premium: what the shares cost if assigned."
  }, "Assigned at"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How far the strike sits under the buy zone."
  }, "Below zone"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Full strike notional, strike \xD7 100."
  }, "Notional"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Credit as a share of full notional, scaled to a year."
  }, "Annualized"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Probability-weighted return on the full comparison capital."
  }, "On capital"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Model chance of finishing in the money."
  }, "In the money"), /*#__PURE__*/React.createElement("th", {
    title: "Whether it could actually be traded."
  }, "Liquidity"))), /*#__PURE__*/React.createElement("tbody", null, rows.map((r, i) => {
    const k = r.contract || {};
    const liq = r.liquidity || {};
    return /*#__PURE__*/React.createElement("tr", {
      key: `${r.expiration}-${k.strike}-${i}`
    }, /*#__PURE__*/React.createElement("td", null, invShortDate(r.expiration)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, Math.round(r.dte || 0)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(k.strike)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(k.credit)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(k.effective_assignment_cost)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPct(k.below_buy_zone_pct)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invMoney(r.notional, 0)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPct(k.annualized_on_notional_pct)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, r.weighted_annualized_pct == null ? invNA : `${r.weighted_annualized_pct.toFixed(2)}%`), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPct((r.market_risk || {}).p_itm_model == null ? null : r.market_risk.p_itm_model * 100)), /*#__PURE__*/React.createElement("td", {
      title: liq.label
    }, liq.ok ? "tradeable" : "thin"));
  })))))));
}

// ── the LEAPS optimizer ───────────────────────────────────────────────────

function InvBestLeaps({
  comp
}) {
  const c = comp || {};
  const pool = c.leaps_pool || [];
  const ivh = c.iv_context || {};
  const rv = c.realized_vol || {};
  if (!pool.length) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty",
      title: "Long-dated contracts do not exist on every ticker, and the ones that do are not always quoted two-sided."
    }, c.reason || "No long-dated calls could be priced for this ticker.");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-leaps"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Long-dated calls, chosen through the
structure comparison rather than by a preferred delta. There is deliberately no
0.75–0.85 rule, no implied-volatility percentile gate and no implied-to-realized
ceiling: those are conventions, and the economics on identical capital are a
measurement.

ExpectedRV30 is deliberately ABSENT from this panel. It is a thirty-day
volatility forecast, and holding a two-year contract's implied volatility up
against it and calling the difference an edge compares a two-year price with a
one-month forecast. What a long contract can honestly be judged against is what
this stock's volatility has actually been over windows of the same length.`
  }, "Best long-dated calls", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", pool.length, " contracts priced")), /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Realized volatility over this tenor",
    value: rv.rv_tenor == null ? invNA : `${(rv.rv_tenor * 100).toFixed(1)}%`,
    reason: rv.reason,
    basis: rv.tenor_trading_days ? `${rv.tenor_trading_days} trading days — the same length as the contract` : "",
    tip: "Measured over a window the same length as the contract, which is the only realized-volatility figure a long-dated implied volatility can honestly be read against."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Realized volatility, one year",
    value: rv.rv_1y == null ? invNA : `${(rv.rv_1y * 100).toFixed(1)}%`,
    tip: "Long-run context."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Realized volatility, three years",
    value: rv.rv_3y == null ? invNA : `${(rv.rv_3y * 100).toFixed(1)}%`,
    tip: "Longer-run context."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Where this implied volatility sits in its own history",
    value: ivh.percentile == null ? invNA : `${invOrdinal(ivh.percentile)} percentile`,
    reason: ivh.reason,
    basis: ivh.n ? `${ivh.n} recorded observations near this tenor` : "",
    tip: "Against this dashboard's OWN record of long-dated implied volatility at a similar tenor. It starts on the day this was first recorded and is never back-filled \u2014 no free archive of past long-dated option prices exists, and inventing one would be a fabricated history."
  })), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Expiration date."
  }, "Expires"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Days to expiration."
  }, "Days"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Strike price."
  }, "Strike"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How much the contract moves per dollar of share price. Filled from Black-Scholes where the feed does not carry it."
  }, "Delta"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The bid."
  }, "Bid"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The offer \u2014 the honest execution price for a buyer."
  }, "Ask"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "What it would be worth if it expired today."
  }, "Intrinsic"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Everything above intrinsic value."
  }, "Extrinsic"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Extrinsic value per year to expiration: what the optionality costs annually."
  }, "Extrinsic a year"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The share price above which the contract makes money at expiration."
  }, "Breakeven"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The contract's own implied volatility."
  }, "Implied volatility"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Open interest."
  }, "Open interest"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Profit or loss on the whole comparison account in the bear scenario."
  }, "Bear"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Base scenario."
  }, "Base"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Bull scenario."
  }, "Bull"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Probability-weighted terminal wealth on identical capital."
  }, "Weighted wealth"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The worst the account can end at against what it started with."
  }, "Max loss"))), /*#__PURE__*/React.createElement("tbody", null, pool.map((r, i) => {
    const k = r.contract || {};
    const liq = r.liquidity || {};
    const t = r.terminal || {};
    return /*#__PURE__*/React.createElement("tr", {
      key: `${r.expiration}-${k.strike}-${i}`
    }, /*#__PURE__*/React.createElement("td", null, invShortDate(r.expiration)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, Math.round(r.dte || 0)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(k.strike)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num",
      title: k.delta_source || ""
    }, k.delta == null ? invNA : k.delta.toFixed(3)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(liq.bid)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(liq.ask)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(k.intrinsic)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(k.extrinsic)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(k.extrinsic_per_year)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(r.breakeven)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, k.iv == null ? invNA : `${(k.iv * 100).toFixed(1)}%`), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, liq.open_interest == null ? invNA : invCount(liq.open_interest)), INV_SCENARIOS.map(([s]) => /*#__PURE__*/React.createElement("td", {
      className: `scan-num ${((t[s] || {}).pnl || 0) >= 0 ? "up" : "down"}`,
      key: s
    }, invMoney((t[s] || {}).pnl, 0))), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invMoney(r.weighted_wealth, 0)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num down"
    }, invMoney(r.max_loss, 0)));
  })))), /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, "Every row is marked on the same account of 100 times the current share price, so a cheap contract is not flattered by the money it leaves uninvested \u2014 that money earns the matching Treasury yield and the comparison counts it."));
}

// ── the management plan ───────────────────────────────────────────────────

function InvPlan({
  plan
}) {
  const p = plan || {};
  if (!p.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty",
      title: "A management plan describes how a position ends. Where no position is recommended there is nothing to manage, and the conditions that would change the answer are stated with the verdict instead."
    }, p.reason || "No position is recommended.");
  }
  const row = (r, i) => /*#__PURE__*/React.createElement("div", {
    className: "inv-planrow",
    key: `${r.trigger}-${i}`
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-planrow-trigger"
  }, r.trigger), /*#__PURE__*/React.createElement("span", {
    className: "inv-planrow-detail"
  }, r.detail));
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-plan"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `What would end this position,
decided before it is opened. No orders are placed anywhere in this dashboard
and nothing here is an alert — it is a written condition per exit, so the
decision to close is made on the thesis rather than on the screen colour of the
day.

No orders are placed anywhere in this dashboard. This is a written plan, not an
automation, and nothing here fires an alert.`
  }, "Management plan", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 for ", p.verdict)), (p.specific || []).map(row), (p.common || []).map(row), /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, p.note));
}

// ── the watchlist scanner ─────────────────────────────────────────────────

function InvScanner({
  apiFetch,
  onPick
}) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sortKey, setSortKey] = useState("premium_to_buy_zone_pct");
  const [asc, setAsc] = useState(true);
  const load = React.useCallback(() => {
    setBusy(true);
    apiFetch("/api/invest/scan").then(r => r.json()).then(j => setData(j)).catch(() => setData({
      rows: [],
      error: true
    })).finally(() => setBusy(false));
  }, [apiFetch]);
  useEffect(() => {
    load();
  }, [load]);
  const d = data || {};
  const rows = (d.rows || []).slice();
  rows.sort((a, b) => {
    const x = a[sortKey],
      y = b[sortKey];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === "string") return asc ? x.localeCompare(y) : y.localeCompare(x);
    return asc ? x - y : y - x;
  });
  const head = (key, label, tip, num) => /*#__PURE__*/React.createElement("th", {
    className: num ? "scan-num" : "",
    key: key,
    title: `${tip}\n\nClick to sort.`,
    onClick: () => {
      if (key === sortKey) setAsc(!asc);else {
        setSortKey(key);
        setAsc(true);
      }
    },
    style: {
      cursor: "pointer"
    }
  }, label, sortKey === key ? asc ? " ▲" : " ▼" : "");
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-scanner"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: `Every starred watchlist name, read
from the snapshots this tab has already stored. A full build reads SEC filings,
a peer group and an option chain, so doing that for a whole watchlist inside
one page load would be a multi-minute wait — names with nothing stored say so
and a few are built in the background per visit.

There is deliberately NO summed investment score. A column that added Quality
to Growth to Valuation would let one strong reading carry a weak one, and it
would be sortable, which is worse: it would become the column everybody sorts
by.`
  }, "Watchlist scan", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", d.n_recorded || 0, " of ", d.n || 0, " recorded", d.n_missing ? `, ${d.n_missing} not yet` : ""), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost inv-scan-refresh",
    onClick: load,
    disabled: busy,
    title: "Re-read the stored snapshots."
  }, busy ? "Loading…" : "Refresh")), (d.building || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, "Building ", (d.building || []).join(", "), " in the background \u2014 refresh in a moment."), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, head("symbol", "Ticker", "Ticker symbol."), head("price", "Price", "Current share price.", true), head("quality_label", "Quality", "How good the business is. Not blended with anything else."), head("growth_label", "Growth", "Revenue and earnings growth."), head("valuation_label", "Valuation", "Cheap or expensive against its own history and its peers. 100 means cheap."), head("revisions_label", "Revisions", "Whether analysts are raising or cutting."), head("value_trap_level", "Trap risk", "Whether the business is deteriorating."), head("fair_value_base", "Base fair value", "The highest-confidence method's value.", true), head("buy_zone", "Buy zone", "The price at which the shares are worth owning.", true), head("premium_to_buy_zone_pct", "To buy zone", "How far the price sits above (positive) or below (negative) the buy zone.", true), head("preferred_structure", "Preferred structure", "Which way of taking the position won on identical capital."), head("entry_verdict", "Verdict", "The Phase 3 entry answer."))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => {
    if (r.status !== "recorded") {
      return /*#__PURE__*/React.createElement("tr", {
        key: r.symbol,
        className: "inv-row-off"
      }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.symbol)), /*#__PURE__*/React.createElement("td", {
        colSpan: 11,
        className: "inv-fv-why",
        title: r.reason
      }, r.reason));
    }
    return /*#__PURE__*/React.createElement("tr", {
      key: r.symbol,
      onClick: () => onPick && onPick(r.symbol),
      style: {
        cursor: onPick ? "pointer" : "default"
      },
      title: r.entry_reason || r.name
    }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.symbol)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(r.price)), /*#__PURE__*/React.createElement("td", null, r.quality_label || invNA), /*#__PURE__*/React.createElement("td", null, r.growth_label || invNA), /*#__PURE__*/React.createElement("td", null, r.valuation_label || invNA), /*#__PURE__*/React.createElement("td", null, r.revisions_label || invNA), /*#__PURE__*/React.createElement("td", {
      className: INV_TRAP_TONE[r.value_trap_level] === "down" ? "down" : ""
    }, r.value_trap_level || invNA), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(r.fair_value_base)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invPrice(r.buy_zone)), /*#__PURE__*/React.createElement("td", {
      className: `scan-num ${(r.premium_to_buy_zone_pct || 0) <= 0 ? "up" : "down"}`
    }, r.premium_to_buy_zone_pct == null ? invNA : `${r.premium_to_buy_zone_pct >= 0 ? "+" : ""}${r.premium_to_buy_zone_pct.toFixed(1)}%`), /*#__PURE__*/React.createElement("td", null, r.preferred_structure || invNA), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
      className: `inv-entry inv-entry-sm inv-entry-${INV_ENTRY_TONE[r.entry_verdict] || "mut"}`
    }, r.entry_verdict || invNA)));
  })))), /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, d.note));
}

// ── the tab ───────────────────────────────────────────────────────────────

function InvestTab({
  apiFetch,
  ticker,
  onOpenTicker
}) {
  const [sym, setSym] = useState(() => (ticker || "AAPL").toUpperCase());
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [years, setYears] = useState(3);
  // A set, not a single key: an accordion where opening one section closes
  // another fights the reader, and a controlled `open` prop on <details>
  // conflicts with the browser toggling it itself.
  const [open, setOpen] = useState(() => new Set(["fairvalue", "structures"]));
  const toggle = (key, want) => setOpen(prev => {
    const next = new Set(prev);
    if (want === undefined ? next.has(key) : !want) next.delete(key);else next.add(key);
    return next;
  });
  useEffect(() => {
    if (ticker && ticker.toUpperCase() !== sym) setSym(ticker.toUpperCase());
  }, [ticker]);

  // useCallback is not among the hooks app-lib publishes on window, so it is
  // reached through React itself — same as app.jsx does.
  const load = React.useCallback((symbol, yrs, force) => {
    if (!symbol) return;
    setBusy(true);
    setErr(null);
    apiFetch(`/api/invest?symbol=${encodeURIComponent(symbol)}&years=${yrs}${force ? "&force=1" : ""}`).then(r => r.json()).then(j => {
      if (j && j.error) {
        setErr(j.error);
        setData(null);
      } else {
        setData(j);
      }
    }).catch(e => setErr(String(e.message || e))).finally(() => setBusy(false));
  }, [apiFetch]);
  useEffect(() => {
    load(sym, years, false);
  }, [sym, years, load]);

  // The peer group builds in a background thread on a cold cache, so one
  // quiet re-read fills it in rather than leaving "building…" on screen.
  useEffect(() => {
    if (!data || (data.peers || {}).status !== "building") return undefined;
    const t = setTimeout(() => load(sym, years, false), 6000);
    return () => clearTimeout(t);
  }, [data, sym, years, load]);
  const d = data || {};
  const prov = d.provenance || {};
  const v = d.verdict || {};
  const md = d.metric_detail || {};
  const reasonOf = k => (md[k] || {}).reason || "";
  const section = (key, label, tip, node) => /*#__PURE__*/React.createElement("details", {
    className: "inv-exp",
    key: key,
    open: open.has(key),
    onToggle: e => toggle(key, e.target.open)
  }, /*#__PURE__*/React.createElement("summary", {
    title: tip
  }, label), /*#__PURE__*/React.createElement("div", {
    className: "inv-exp-body"
  }, node));
  return /*#__PURE__*/React.createElement("div", {
    className: "card inv-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-title",
    title: "A long-horizon view of the business behind the ticker: how good it is, whether it is growing, what you are being asked to pay for it against its own history and against comparable businesses, and whether the cheapness is real."
  }, "Investment \xB7 is this business worth owning"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "Reported fundamentals from SEC EDGAR. Four independent readings, never blended into one score, plus a check on whether cheap means broken.")), /*#__PURE__*/React.createElement("div", {
    className: "inv-ctrl"
  }, /*#__PURE__*/React.createElement("input", {
    className: "inv-sym",
    value: sym,
    maxLength: 8,
    onChange: e => setSym(e.target.value.toUpperCase().replace(/[^A-Z.]/g, "")),
    onKeyDown: e => {
      if (e.key === "Enter") load(sym, years, true);
    },
    title: "Type a ticker and press Enter. Follows the dashboard's selected ticker automatically.",
    "aria-label": "Ticker"
  }), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: () => load(sym, years, true),
    disabled: busy,
    title: "Re-read the filings, the quote, the peer group and the Treasury curve now."
  }, busy ? "Loading…" : "Refresh"), onOpenTicker && /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => onOpenTicker(sym),
    title: "Open this ticker on the Trade tab."
  }, "Trade tab \u2192"))), err && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, err), !data && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Reading ", sym, "'s filings\u2026"), data && !d.ok && /*#__PURE__*/React.createElement("div", {
    className: "research-empty",
    title: "This is a real answer, not a failure. Some companies file only once a year, some report in a currency the share price is not quoted in, and exchange-traded funds do not file company accounts at all."
  }, /*#__PURE__*/React.createElement("b", null, sym), " \u2014 ", d.unavailable_reason || "No reported fundamentals available."), data && d.ok && /*#__PURE__*/React.createElement("div", {
    className: "inv-body"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-hero"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-hero-top"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-hero-name"
  }, /*#__PURE__*/React.createElement("b", {
    title: "Company name exactly as it registers with the SEC."
  }, d.entity_name || sym), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    title: `SEC Central Index Key ${d.cik}${d.sic ? ` · industry code ${d.sic}, ${d.sic_description}` : ""}`
  }, " ", "\xB7 ", sym, " \xB7 ", invPrice(d.price)), d.business_type && d.business_type.type !== "STANDARD" && /*#__PURE__*/React.createElement("span", {
    className: "inv-btype",
    title: d.business_type.note
  }, d.business_type.label), /*#__PURE__*/React.createElement(InvCyclePill, {
    cycle: d.earnings_cycle
  })), /*#__PURE__*/React.createElement("div", {
    className: "inv-hero-verdicts"
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-vpair",
    title: "What to DO at today's price: which structure, or why nothing."
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-vpair-label"
  }, "Action"), /*#__PURE__*/React.createElement(InvEntryPill, {
    entry: d.entry
  })), /*#__PURE__*/React.createElement("span", {
    className: "inv-vpair",
    title: "What the BUSINESS looks like, independent of what it costs today \u2014 the four-vector reading from the section below."
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-vpair-label"
  }, "Business"), /*#__PURE__*/React.createElement(InvVerdictPill, {
    verdict: v.verdict
  })))), /*#__PURE__*/React.createElement(InvDecisionBar, {
    snap: d,
    fair: d.fair_value,
    er: d.expected_return,
    structures: d.structures
  }), d.entry && (d.entry.reasons || []).length > 0 && /*#__PURE__*/React.createElement("ul", {
    className: "inv-reasons inv-entry-reasons"
  }, (d.entry.reasons || []).map((r, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, r))), d.entry && (d.entry.what_would_change || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "inv-change",
    title: "Every WAIT, AVOID and TOSS UP names the exact thing that would change it. A verdict that cannot say what it is waiting for is a mood."
  }, (d.entry.what_would_change || []).map((c, i) => /*#__PURE__*/React.createElement("div", {
    key: i
  }, c))), /*#__PURE__*/React.createElement("div", {
    className: "inv-tiles"
  }, INV_DIMENSIONS.map(([key, label, tip]) => /*#__PURE__*/React.createElement(InvScoreTile, {
    key: key,
    dimKey: key,
    label: label,
    tip: tip,
    block: d[key],
    onOpen: k => toggle(k, true)
  })), /*#__PURE__*/React.createElement(InvTrapTile, {
    trap: d.value_trap,
    onOpen: k => toggle(k, true)
  })), /*#__PURE__*/React.createElement("details", {
    className: "inv-exp inv-exp-inner inv-why",
    open: open.has("why"),
    onToggle: e => toggle("why", e.target.open)
  }, /*#__PURE__*/React.createElement("summary", {
    title: "The business reading behind the decision above: the four independent vectors and what each of them found."
  }, "Why \u2014 the business verdict is ", v.verdict || "INSUFFICIENT DATA"), /*#__PURE__*/React.createElement("div", {
    className: "inv-exp-body"
  }, /*#__PURE__*/React.createElement("ul", {
    className: "inv-reasons"
  }, (v.reasons || []).map((r, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, r))), (v.what_would_change || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "inv-change",
    title: "Every WAIT or AVOID says what would have to change. The price is the level at which the earnings yield reaches this company's OWN median valuation \u2014 not a universal multiple."
  }, (v.what_would_change || []).map((c, i) => /*#__PURE__*/React.createElement("div", {
    key: i
  }, c))))), d.profile && /*#__PURE__*/React.createElement(InvMoatTags, {
    tags: d.profile.moat_tags,
    profile: d.profile
  })), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Share price",
    value: invPrice(d.price),
    prov: prov.price,
    tip: "The current share price, used for every ratio below."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Market value of the company",
    value: invMoney(d.market_cap),
    prov: prov.price,
    basis: `Share price times ${invCount(d.shares_outstanding)} shares — ${(d.shares_detail || {}).basis || ""}`,
    reason: (d.shares_detail || {}).reason,
    tip: "Share price multiplied by the share count on the cover page of the latest filing \u2014 two sourced numbers, so both halves can be checked."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Price to earnings, trailing",
    value: invRatio(d.trailing_pe),
    prov: prov.fundamentals,
    basis: (md.eps || {}).basis,
    asOf: (md.eps || {}).period_end,
    reason: d.eps_ttm != null && d.eps_ttm <= 0 ? "Earnings are negative, and a negative price-to-earnings ratio is an arithmetic artifact rather than a cheap stock, so it is not shown." : reasonOf("eps"),
    tip: "Share price divided by the last twelve months of reported GAAP diluted earnings per share."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Price to earnings, forward",
    value: invRatio(d.forward_pe),
    prov: prov.estimates,
    reason: d.estimates_available ? "" : (prov.estimates || {}).reason || d.estimates_reason,
    tip: "Share price divided by the analyst consensus for this fiscal year. Adjusted basis \u2014 never mixed with the GAAP trailing figures inside one ratio."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Earnings yield",
    value: invPct(d.earnings_yield_pct),
    prov: prov.fundamentals,
    reason: reasonOf("eps"),
    tip: "Trailing earnings per share divided by the share price. This is what the valuation percentile is measured on."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Free cash flow yield",
    value: invPct(d.fcf_yield_pct),
    prov: prov.fundamentals,
    basis: (d.free_cash_flow_detail || {}).basis,
    reason: (d.free_cash_flow_detail || {}).reason,
    tone: d.fcf_yield_pct == null || d.fcf_yield_pct >= 0 ? "" : "down",
    tip: "Cash from operations minus capital spending over the last twelve months, divided by the market value of the company."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Revenue, last twelve months",
    value: invMoney(d.revenue_ttm, 0),
    prov: prov.fundamentals,
    basis: (md.revenue || {}).basis,
    asOf: (md.revenue || {}).period_end,
    reason: reasonOf("revenue"),
    tip: "Total revenue over the last four reported quarters."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Revenue growth, year over year",
    value: invSignedPct(d.revenue_growth_pct),
    tone: d.revenue_growth_pct == null ? "" : d.revenue_growth_pct >= 0 ? "up" : "down",
    prov: prov.fundamentals,
    reason: d.revenue_growth_note || reasonOf("revenue"),
    tip: "This twelve-month revenue against the same twelve months a year earlier."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Earnings per share, last twelve months",
    value: invPrice(d.eps_ttm),
    prov: prov.fundamentals,
    basis: (md.eps || {}).basis,
    asOf: (md.eps || {}).period_end,
    reason: reasonOf("eps"),
    tone: d.eps_ttm == null ? "" : d.eps_ttm >= 0 ? "" : "down",
    tip: "Reported GAAP diluted earnings per share, summed over the last four quarters."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Earnings growth, year over year",
    value: invSignedPct(d.eps_growth_pct),
    tone: d.eps_growth_pct == null ? "" : d.eps_growth_pct >= 0 ? "up" : "down",
    prov: prov.fundamentals,
    reason: d.eps_growth_note || reasonOf("eps"),
    tip: "Trailing earnings per share against the same twelve months a year earlier, on the same GAAP basis."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Net profit margin",
    value: invPct(d.net_margin_pct),
    prov: prov.fundamentals,
    reason: reasonOf("net_income") || reasonOf("revenue"),
    tip: "What share of each dollar of revenue survives as profit."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Price to sales",
    value: invRatio(d.price_sales, 2),
    prov: prov.fundamentals,
    reason: reasonOf("revenue"),
    tip: "Market value divided by revenue. Shown because it is easy to compute and people look for it \u2014 it plays NO part in the verdict. A company can sell a great deal and never earn anything, and this ratio cannot tell the two apart."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "10-year Treasury yield",
    value: invPct(d.treasury_10y_pct, 2),
    prov: prov.treasury_10y,
    tip: "What a government bond pays. Shown for context \u2014 it is no longer a hurdle the stock has to clear, because a universal yield threshold cannot tell an excellent business priced fairly from a poor one priced cheaply."
  })), d.profile && /*#__PURE__*/React.createElement("div", {
    className: "inv-desc"
  }, d.profile.description, /*#__PURE__*/React.createElement("div", {
    className: "inv-src",
    title: `Quoted from Item 1, Business, of
                the annual report filed ${invDate(d.profile.as_of)}.`
  }, d.profile.source, " \xB7 filed ", invShortDate(d.profile.as_of), d.profile.url && /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 ", /*#__PURE__*/React.createElement("a", {
    href: d.profile.url,
    target: "_blank",
    rel: "noopener noreferrer",
    title: "Open the annual report on EDGAR."
  }, "read the filing")))), section("fairvalue", "Fair value", "Bear, base and bull from methods that are never averaged, the confidence that comes from how far apart they are, and the price this analysis says the shares are worth.", /*#__PURE__*/React.createElement(InvFairValue, {
    fair: d.fair_value,
    snap: d
  })), section("expected", "Expected return", "Where today's price gets you over the scenario horizon, with dividends handled as cash rather than added to the price return.", /*#__PURE__*/React.createElement(InvExpectedReturn, {
    er: d.expected_return
  })), section("implied", "What the market is already paying for", "The reverse discounted cash flow, solved for one unknown and shown across the grid of assumptions it depends on.", /*#__PURE__*/React.createElement(InvImplied, {
    imp: d.implied_expectations
  })), section("structures", "Structure comparison", "Every way of taking this position, on identical capital at an identical expiration on identical scenario prices.", /*#__PURE__*/React.createElement(InvComparator, {
    comp: (d.structures || {}).comparison,
    snap: d
  })), section("bestput", "Best put", "The short-dated put optimizer, gated at the buy zone and never above it.", /*#__PURE__*/React.createElement(InvBestPut, {
    put: (d.structures || {}).put,
    market: (d.structures || {}).market_risk,
    snap: d
  })), section("bestleaps", "Best long-dated calls", "Long-dated calls chosen through the comparison rather than by a preferred delta, with tenor-matched volatility context.", /*#__PURE__*/React.createElement(InvBestLeaps, {
    comp: (d.structures || {}).comparison
  })), section("plan", "Management plan", "What would end this position, decided before it is opened.", /*#__PURE__*/React.createElement(InvPlan, {
    plan: d.plan
  })), section("valuation", "Valuation against its own history", "Where this company is priced today against where it has been priced before, using only what was public on each of those days.", /*#__PURE__*/React.createElement(InvValuationHistory, {
    vh: d.valuation_history,
    valuation: d.valuation,
    symbol: sym
  })), section("revisions", "Earnings and revisions", "What analysts expect and whether they are raising or cutting.", /*#__PURE__*/React.createElement(InvRevisions, {
    revisions: d.revisions,
    snap: d
  })), section("quality", "Quality", "The six inputs behind the quality reading, each scored or each explaining its absence.", /*#__PURE__*/React.createElement(InvQuality, {
    quality: d.quality
  })), section("peers", "Peers", "Comparable companies by the SEC's own industry code, and what the group is being valued at.", /*#__PURE__*/React.createElement(InvPeers, {
    peers: d.peers,
    symbol: sym,
    snap: d
  })), section("trap", "Value trap check", "Whether the business is deteriorating — the question cheapness cannot answer on its own.", /*#__PURE__*/React.createElement(InvValueTrap, {
    trap: d.value_trap
  })), section("growth", "Earnings drivers", "What moved earnings per share over the last year, split between revenue, margin and share count.", /*#__PURE__*/React.createElement(InvDrivers, {
    drivers: d.drivers
  })), section("chart", "Price against earnings", "Price and trailing earnings indexed to 100, so their shapes can be compared.", /*#__PURE__*/React.createElement(InvHistoryChart, {
    history: d.history,
    years: years,
    onYears: setYears,
    symbol: sym
  })), section("stress", "Drawdown history", "What this stock actually did when things went wrong.", /*#__PURE__*/React.createElement(InvDrawdowns, {
    dd: d.drawdowns
  })), section("experimental", "Revision underreaction (experimental)", "An untested idea, recorded daily so it can be tested honestly later. It takes no part in the verdict.", /*#__PURE__*/React.createElement(InvUnderreaction, {
    u: d.underreaction
  })), section("scanner", "Watchlist scan", "Every starred name, read from the snapshots already stored. No column here is a total.", /*#__PURE__*/React.createElement(InvScanner, {
    apiFetch: apiFetch,
    onPick: setSym
  })), /*#__PURE__*/React.createElement("div", {
    className: "inv-foot",
    title: `This tab stores one snapshot of every
number above per day, so a later phase can test whether any of this predicted
anything. ${d.stored_days || 0} day${d.stored_days === 1 ? "" : "s"} recorded so far
for ${sym}. Nothing is ever back-filled or overwritten.`
  }, d.stored_days || 0, " daily snapshot", d.stored_days === 1 ? "" : "s", " recorded for ", sym, " \xB7 configuration ", d.config_hash || "—")));
}
function InvUnderreaction({
  u
}) {
  const x = u || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-under"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-warn",
    title: "This is recorded, not believed. The prospective snapshot store is what will eventually test whether it predicts anything at all."
  }, /*#__PURE__*/React.createElement("b", null, "EXPERIMENTAL \u2014 unvalidated."), " This dashboard has not tested whether this predicts anything, and it takes no part in the verdict."), !x.available ? /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, x.reason || "Not computable yet.") : /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Underreaction score",
    value: x.score == null ? invNA : x.score.toFixed(2),
    tip: "How strongly estimates have been raised, minus how much the share price has already moved for it. Both halves are standardised across comparable companies first, so this is a relative reading, not an absolute one."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Revision strength",
    value: x.revision_z == null ? invNA : x.revision_z.toFixed(2),
    tip: "Change in this year's consensus over ninety days, scaled by the SHARE PRICE rather than by the old estimate \u2014 dividing by a near-zero estimate produces a number in the hundreds of percent that says more about the denominator than the revision."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Price reaction",
    value: x.price_reaction_z == null ? invNA : x.price_reaction_z.toFixed(2),
    tip: "This stock's ninety-day return minus its sector's, standardised the same way."
  })), /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Revision breadth, 30 days",
    value: invSignedPct(x.revision_breadth_30d_pct),
    tip: "Net share of covering analysts raising rather than cutting."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Analyst coverage",
    value: x.analyst_count == null ? invNA : Math.round(x.analyst_count),
    tip: "How many analysts publish an estimate."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Earnings report inside the window",
    value: x.earnings_inside_window == null ? invNA : x.earnings_inside_window ? "Yes" : "No",
    tip: "Whether the company reported inside the ninety days being measured. A revision that follows a report is a different animal from one that does not."
  })), x.note && /*#__PURE__*/React.createElement("div", {
    className: "inv-note"
  }, x.note));
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  InvestTab: React.memo(InvestTab)
});
})();
