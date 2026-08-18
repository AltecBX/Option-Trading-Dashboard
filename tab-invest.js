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

// Which specialized model produced a row's fair value. Named in plain words
// so a scanner row never reads as though one arithmetic was applied to a
// bank, a property trust and a software company alike.
const INV_MODEL_NAME = {
  BANK: "bank",
  REIT: "property trust",
  INSURANCE: "insurer",
  BROKER: "broker"
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

// ── Phase 4: banks ────────────────────────────────────────────────────────

const INV_BANK_TIP = {
  tbvps: "Shareholders' equity with preferred stock, goodwill and other " + "intangible assets taken out, divided by the shares outstanding. This is " + "the conservative measure of what the common shareholder owns: goodwill " + "paid for an acquisition cannot absorb a loan loss.",
  ptbv: "Share price divided by tangible book value per share. The single " + "most-used measure of what a bank costs — but the cheapest one in a " + "group is usually the least profitable one, not a bargain, which is why " + "the return below sits beside it.",
  bvps: "Shareholders' equity less preferred stock, per share. Book value " + "includes goodwill; tangible book value does not.",
  pb: "Share price divided by book value per share.",
  roe: "Net income to common shareholders over average shareholders' equity.",
  rotce: "Net income to common over average TANGIBLE common equity. This is " + "the number that decides what a bank is worth above its book: a bank " + "compounding twenty percent on its tangible equity deserves a large " + "premium to it, and one earning below what shareholders could get " + "elsewhere deserves a discount.",
  nim: "Net interest income divided by average TOTAL assets. This is NOT net " + "interest margin, which divides by average interest-EARNING assets — no " + "bank measured tags either that ratio or the earning-asset base in " + "machine-readable filings, so what is shown is what the filings support, " + "under the name of what it actually is.",
  nii_growth: "Growth in net interest income over the last twelve months " + "against the twelve before it. This is the core revenue line of a lender.",
  efficiency: "Non-interest expense as a share of revenue — what it costs to " + "run the bank per dollar it earns. LOWER IS BETTER, which is the " + "opposite direction from most percentages on this screen.",
  deposits: "Customer deposits at the latest balance-sheet date. Deposits are " + "a bank's raw material, and cheap sticky ones are the durable advantage.",
  deposit_growth: "Growth in deposits against a year earlier.",
  deposit_cost: "Interest paid on deposits over average deposits — what the " + "bank's funding costs it.",
  loans: "Loans and leases outstanding before the allowance for credit losses.",
  loan_growth: "Growth in the loan book against a year earlier. Fast growth " + "is not automatically good: loans made quickly are the ones that default " + "later.",
  nco: "Loans written off net of recoveries over the last twelve months, as a " + "share of average loans. This is realized credit loss — money already gone.",
  nco_trend: "Whether the charge-off rate is rising, falling or steady " + "against the year before. Direction matters more than the level.",
  npl: "Loans on non-accrual status as a share of loans outstanding — " + "borrowers who have stopped paying but have not yet been written off. " + "This is the leading indicator that charge-offs follow.",
  capital: "Regulatory capital as a share of risk-weighted assets. Whichever " + "ratio the bank actually tagged is named: common equity tier one and " + "tier one are different ratios, and tier one includes preferred stock.",
  shares: "Change in the diluted share count against a year earlier. " + "Negative means the count is shrinking, which raises every per-share " + "figure above."
};
function invBankVal(block, fmt) {
  const v = (block || {}).value;
  return v == null ? invNA : fmt(v);
}
function InvBank({
  bank
}) {
  const b = bank || {};
  if (!b.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "inv-fv-why",
      title: "Every measure on this panel is built from the bank's own filings. When a required one is missing it is reported as missing rather than filled in with a zero."
    }, b.reason || "This bank's book value could not be measured from its filings.");
  }
  const stat = (label, block, fmt, tip, tone) => /*#__PURE__*/React.createElement(InvStat, {
    label: label,
    value: invBankVal(block, fmt),
    tip: tip,
    basis: (block || {}).basis,
    reason: (block || {}).reason,
    tone: tone
  });
  const trend = b.charge_off_trend || {};
  const cap = b.capital_ratio || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-bank"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: "A bank is valued against what it owns and how much it earns on it, because a lender's borrowing is its raw material rather than its risk. Nothing on this panel is scored or summed."
  }, "Valued on tangible book value and the return earned on it. The generic free-cash-flow and enterprise-value measures are not shown for a bank because neither means for a lender what it means elsewhere."), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Tangible book value per share", b.tangible_book_per_share, invPrice, INV_BANK_TIP.tbvps), stat("Price to tangible book value", b.price_to_tangible_book, v => invRatio(v, 2), INV_BANK_TIP.ptbv), stat("Book value per share", b.book_per_share, invPrice, INV_BANK_TIP.bvps), stat("Price to book value", b.price_to_book, v => invRatio(v, 2), INV_BANK_TIP.pb), stat("Return on tangible common equity", b.return_on_tangible_common_equity_pct, invPct, INV_BANK_TIP.rotce), stat("Return on equity", b.return_on_equity_pct, invPct, INV_BANK_TIP.roe), stat("Net interest income to average assets", b.net_interest_income_to_average_assets_pct, invPct, INV_BANK_TIP.nim), stat("Net interest income growth", b.net_interest_income_growth_pct, invSignedPct, INV_BANK_TIP.nii_growth), stat("Efficiency ratio", b.efficiency_ratio_pct, invPct, INV_BANK_TIP.efficiency), stat("Capital ratio", cap, invPct, `${INV_BANK_TIP.capital}${cap.label ? `\n\nReported as: ${cap.label}.` : ""}`)), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: "Where a bank's money comes from and what it costs. Cheap deposits that stay put are the whole advantage."
  }, "Funding"), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Deposits", b.deposits, invMoney, INV_BANK_TIP.deposits), stat("Deposit growth", b.deposit_growth_pct, invSignedPct, INV_BANK_TIP.deposit_growth), stat("Cost of deposits", b.deposit_cost_pct, invPct, INV_BANK_TIP.deposit_cost), stat("Diluted share count change", b.diluted_share_trend_pct, invSignedPct, INV_BANK_TIP.shares)), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: "What the loan book is doing and whether it is going wrong. Non-accrual loans lead charge-offs, so the two are read together."
  }, "Credit"), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Loans outstanding", b.loans, invMoney, INV_BANK_TIP.loans), stat("Loan growth", b.loan_growth_pct, invSignedPct, INV_BANK_TIP.loan_growth), stat("Net charge-off rate", b.charge_off_rate_pct, invPct, INV_BANK_TIP.nco), stat("Non-performing loan rate", b.nonperforming_rate_pct, invPct, INV_BANK_TIP.npl)), trend.state && /*#__PURE__*/React.createElement("div", {
    className: `inv-note ${trend.state === "RISING" ? "down" : ""}`,
    title: `${INV_BANK_TIP.nco_trend}\n\n${trend.basis || ""}`
  }, "Charge-offs are ", /*#__PURE__*/React.createElement("b", null, trend.state), trend.change_pct != null && ` — ${invSignedPct(trend.change_pct)} against the year before`, "."));
}

// ── Phase 4: property trusts ──────────────────────────────────────────────

const INV_REIT_TIP = {
  ffo: "Funds from operations: net income available to common, plus " + "depreciation and amortisation of property, less gains on property " + "sales, plus impairments. Property depreciation is a schedule rather " + "than a description of what buildings are worth, so removing it is what " + "makes the figure describe the business.\n\nThis is RECONSTRUCTED from " + "the filed components. No property trust publishes funds from operations " + "in machine-readable filings — every headline figure lives only in press " + "releases — and a trust's own headline is usually a further-adjusted " + "'core' number, which is different again.",
  ffops: "Reconstructed funds from operations divided by average diluted " + "shares, which is the share count the industry definition uses.",
  pffo: "Share price divided by reconstructed funds from operations per " + "share. This is the property equivalent of a price-to-earnings ratio and " + "is the main way trusts are valued.",
  affo: "Adjusted funds from operations subtracts recurring maintenance " + "spending, straight-line rent and lease-intangible amortisation from " + "funds from operations. It is not shown here because it cannot be " + "computed honestly — see the reason beside it.",
  growth: "Growth in reconstructed funds from operations per share over the " + "last twelve months against the twelve before.",
  dps: "Dividends declared per share over the last twelve months, as " + "reported to the SEC.",
  yield: "The distribution as a percentage of the share price.",
  payout: "The distribution as a share of funds from operations. Property " + "trusts must distribute most of their taxable income, so a high payout " + "is normal — what matters is the direction and how much room is left.",
  nd: "Net borrowings divided by funds from operations. This is NOT net debt " + "to EBITDAre: no trust measured tags EBITDAre in machine-readable form, " + "so the closest measure the filings support is shown under its own name.",
  ptype: "What kind of property this trust owns, read from its own annual " + "report. The SEC gives every property trust the same industry code, so " + "the code cannot tell a data-centre trust from a shopping-centre trust — " + "and those two have never traded at the same multiple.",
  shares: "Change in the diluted share count against a year earlier. Property " + "trusts routinely issue shares to buy buildings, so a rising count is " + "ordinary — what matters is whether funds from operations per share rose " + "with it."
};
function InvReit({
  reit
}) {
  const r = reit || {};
  if (!r.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "inv-fv-why",
      title: "Funds from operations is rebuilt from the filed components. When one of them is missing for part of the year the figure is refused rather than assembled from a partial year."
    }, r.reason || "Funds from operations could not be reconstructed from this trust's filings.");
  }
  const ffo = r.ffo || {};
  const stat = (label, block, fmt, tip, tone) => /*#__PURE__*/React.createElement(InvStat, {
    label: label,
    value: invBankVal(block, fmt),
    tip: tip,
    basis: (block || {}).basis,
    reason: (block || {}).reason,
    tone: tone
  });
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-reit"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_REIT_TIP.ffo
  }, "Valued on funds from operations rather than on reported earnings, because a property trust's earnings are mostly a depreciation schedule.", r.property_type ? /*#__PURE__*/React.createElement(React.Fragment, null, " Property type: ", /*#__PURE__*/React.createElement("b", {
    title: INV_REIT_TIP.ptype
  }, r.property_type_label || r.property_type), ".") : /*#__PURE__*/React.createElement("span", {
    title: INV_REIT_TIP.ptype
  }, " This trust's annual report does not say clearly enough what kind of property it owns for a matched peer comparison.")), !ffo.complete && ffo.caveat && /*#__PURE__*/React.createElement("div", {
    className: "inv-note down",
    title: "An incomplete reconstruction also holds the fair-value confidence down, which lowers the buy zone \u2014 the warning is priced in rather than only printed."
  }, ffo.caveat), r.reconstruction_warning && /*#__PURE__*/React.createElement("div", {
    className: "inv-note down",
    title: "A property portfolio's rents do not move this far in a year. When the reconstruction says they did and the gains component is untagged, the swing is almost certainly a one-off gain that could not be removed."
  }, r.reconstruction_warning), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Funds from operations, trailing twelve months", ffo, invMoney, INV_REIT_TIP.ffo), stat("Funds from operations per share", r.ffo_per_share, invPrice, INV_REIT_TIP.ffops), stat("Price to funds from operations", r.price_to_ffo, v => invRatio(v, 1), INV_REIT_TIP.pffo), stat("Funds from operations growth", r.ffo_growth_pct, invSignedPct, INV_REIT_TIP.growth), stat("Distribution per share", r.dividends_per_share, invPrice, INV_REIT_TIP.dps), stat("Distribution yield", r.dividend_yield_pct, invPct, INV_REIT_TIP.yield), stat("Payout of funds from operations", r.payout_of_ffo_pct, invPct, INV_REIT_TIP.payout, (r.payout_flag || {}).level === "HIGH" ? "down" : undefined), stat("Net debt to funds from operations", r.net_debt_to_ffo, v => invRatio(v, 1), INV_REIT_TIP.nd), stat("Diluted share count change", r.diluted_share_trend_pct, invSignedPct, INV_REIT_TIP.shares)), (r.payout_flag || {}).level === "HIGH" && /*#__PURE__*/React.createElement("div", {
    className: "inv-note down",
    title: INV_REIT_TIP.payout
  }, r.payout_flag.reason), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: "Measures this app deliberately does not report for property trusts, each with the measurement behind the refusal. An empty box with a reason is worth more than a number with nothing behind it."
  }, "Not reported, and why"), /*#__PURE__*/React.createElement("ul", {
    className: "inv-reasons"
  }, /*#__PURE__*/React.createElement("li", {
    title: INV_REIT_TIP.affo
  }, /*#__PURE__*/React.createElement("b", null, "Adjusted funds from operations"), " \u2014 ", (r.affo || {}).reason), /*#__PURE__*/React.createElement("li", null, /*#__PURE__*/React.createElement("b", null, "Occupancy"), " \u2014 ", (r.occupancy || {}).reason), /*#__PURE__*/React.createElement("li", null, /*#__PURE__*/React.createElement("b", null, "Same-store net operating income"), " \u2014", " ", (r.same_store_noi_growth_pct || {}).reason)));
}

// ── Phase 5: insurers ─────────────────────────────────────────────────────

const INV_INS_TIP = {
  how: "How the kind of insurance was decided. Most annual reports say what " + "the company writes in so many words, and those words are counted. Some " + "describe the company by the segments it is organised into instead — " + "General Insurance, Commercial Lines, Personal Lines, Life and " + "Retirement — and for those the segment names are read instead. The " + "second path only runs when the first one has refused: nothing is " + "loosened to reach an answer.",
  mixed: "A multiline insurer files one premium line and one claims line, " + "and where the company is genuinely two businesses the claims include " + "benefits paid by a life book whose earnings the premiums leave out. " + "Dividing one by the other then produces a number that looks like a " + "loss ratio and is a blend of two different businesses, so it is " + "refused. Book value, returns, reserves and the valuation itself are " + "unaffected — only the underwriting ratios go.",
  subtype: "What kind of insurance this company writes, read from its own " + "annual report and checked against its SEC industry code. This decides " + "which numbers below mean anything: claims divided by premiums is a loss " + "ratio for a car insurer and is not a ratio at all for a life insurer, " + "whose premiums leave out most of what it earns and whose benefits " + "include interest credited to policyholder accounts. Where the two " + "sources cannot agree, nothing is measured rather than the wrong " + "definition being applied.",
  bvps: "Shareholders' equity less preferred stock, per share. An insurer's " + "assets are mostly securities carried at what they would fetch, so book " + "value is closer to a real number here than in almost any industry.",
  pb: "Share price divided by book value per share. The main measure of what " + "an insurer costs — but the cheapest in a group is usually the least " + "profitable one, which is why the return on equity sits beside it.",
  tbvps: "Book value with goodwill and other intangibles taken out, per share.",
  ptbv: "Share price divided by tangible book value per share.",
  client_growth: "How much the money customers keep here has grown against " + "the same measure a year earlier, both read from the company's own " + "filing tables. There is no growth figure until a reading from a year " + "ago exists: filings are read forward from today and never back-filled.",
  net_new: "Money customers moved in less money they moved out, over the " + "period the filing reports. This is the number a brokerage is actually " + "judged on, and it is shown only where the company prints it in a table " + "this app can read without guessing at the label, the period or the " + "unit.",
  roe: "Net income to common over average shareholders' equity. This is what " + "decides whether an insurer deserves a premium to its book: one earning " + "exactly its cost of equity is worth exactly its book value.",
  rotce: "The same return measured against tangible common equity. For an " + "insurer carrying little goodwill it differs barely at all from the " + "return on equity beside it.",
  premium_growth: "Growth in premiums earned over the last twelve months " + "against the twelve before. Premiums are the raw material — an insurer " + "whose premiums are shrinking is either losing business or walking away " + "from underpriced business, and only the second is good news.",
  ni_growth: "Growth in net income against a year earlier. Insurer earnings " + "are lumpy by nature: a hurricane lands in one quarter and the premiums " + "that pay for it were collected over three years.",
  loss: "Claims and the cost of settling them, as a share of premiums " + "earned.\n\nThe numerator and denominator are checked for compatibility " + "first: both must cover the same twelve months. Allstate tags a " + "property-casualty premium series that stopped in 2018 next to an " + "all-claims series running to today, and dividing them gives 123%, which " + "looks like a catastrophe and is a date mismatch.",
  benefit: "The share of premiums paid out as medical and other benefits — " + "the measure health insurers are actually judged on.",
  expense: "Acquisition-cost amortisation plus other underwriting expense, " + "as a share of premiums earned.",
  combined: "The loss ratio plus the expense ratio. BELOW 100 the insurer " + "made money on the underwriting itself; above it, the investment income " + "has to cover the difference. LOWER IS BETTER, the opposite direction " + "from most percentages here.\n\nIt is blank for most insurers because " + "only five of the thirty-six measured tag the underwriting expense the " + "second half needs. The obvious substitute — total benefits and expenses " + "less claims — sweeps in interest credited and annuity costs and, for " + "some filers, the cost of dispensing prescriptions, so it is a ratio " + "assembled out of unrelated concepts rather than a measurement.",
  uwprofit: "Premiums earned less claims and underwriting expense. The money " + "the insurance itself made, before anything the investment portfolio did.",
  reserves: "The liability held for claims already incurred and the cost of " + "settling them.",
  res_mult: "Reserves as a multiple of a year's premiums. A long-tail " + "insurer — liability, workers' compensation — carries a larger multiple " + "than a car insurer because its claims take longer to settle, so the " + "level says less than the direction does.",
  development: "The change during the last twelve months in reserves held " + "for claims from EARLIER years, as a share of premiums.\n\nNegative " + "means those reserves proved more than enough and money was released " + "back into profit. Positive — adverse development — means they did not, " + "and more had to be added. Adverse development is the single most " + "important warning in this industry: it says the insurer under-estimated " + "what it already owed, and it tends to repeat.",
  nii: "Income earned on the investment portfolio over the last twelve months.",
  yield: "Investment income over the average size of the portfolio.",
  fpb: "The liability held for benefits promised under policies still in " + "force. This is what a life insurer's whole business turns on, and it " + "does not apply to a property-casualty insurer.",
  capital: "All equity on the balance sheet as a share of total assets. This " + "is NOT a risk-based capital ratio — insurers file those with their " + "state regulators, not with the SEC in machine-readable form. A life " + "insurer's figure is far smaller than a property-casualty insurer's " + "because its balance sheet carries policyholders' separate-account " + "assets as well as its own.",
  book_trend: "Growth in book value per share against a year earlier. Book " + "value compounding, plus the dividend, is most of an insurer's long-run " + "return.",
  shares: "Change in the diluted share count against a year earlier. " + "Negative means the count is shrinking, which raises every per-share " + "figure above."
};
function InvInsurance({
  insurance
}) {
  const i = insurance || {};
  if (!i.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "inv-fv-why",
      title: INV_INS_TIP.subtype
    }, i.reason || "This insurer could not be measured from its filings.");
  }
  const stat = (label, block, fmt, tip, tone) => /*#__PURE__*/React.createElement(InvStat, {
    label: label,
    value: invBankVal(block, fmt),
    tip: tip,
    basis: (block || {}).basis,
    reason: (block || {}).reason,
    tone: tone
  });
  const dev = i.reserve_development_state || {};
  const crt = i.combined_ratio_trend || {};
  const underwriting = i.metric_basis === "UNDERWRITING";
  const health = i.metric_basis === "BENEFIT";
  const spread = i.metric_basis === "SPREAD";
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-bank"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_INS_TIP.subtype
  }, "Read as a ", /*#__PURE__*/React.createElement("b", null, i.subtype_label || i.subtype), ". An insurer is valued on its book value and what it earns on it, because the money it holds between collecting a premium and paying a claim is neither debt nor spare cash \u2014 and a generic model reads it as one or the other."), !!(i.classification || {}).method && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_INS_TIP.how
  }, "How that was decided: ", (i.classification || {}).reason, (i.classification || {}).method === "segment names in the annual report" ? " This company's annual report describes itself by the segments" + " it is organised into rather than by what it writes, so the" + " segments are what was read." : "", ((i.classification || {}).secondary || []).length ? ` It is also in: ${((i.classification || {}).secondary || []).join(", ").toLowerCase()}.` : ""), (i.metric_basis_compatibility || {}).ok === false && /*#__PURE__*/React.createElement("div", {
    className: "inv-note down",
    title: INV_INS_TIP.mixed
  }, (i.metric_basis_compatibility || {}).reason), !!(i.metric_basis_compatibility || {}).note && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_INS_TIP.mixed
  }, (i.metric_basis_compatibility || {}).note), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Book value per share", i.book_per_share, invPrice, INV_INS_TIP.bvps), stat("Price to book value", i.price_to_book, v => invRatio(v, 2), INV_INS_TIP.pb), stat("Tangible book value per share", i.tangible_book_per_share, invPrice, INV_INS_TIP.tbvps), stat("Price to tangible book value", i.price_to_tangible_book, v => invRatio(v, 2), INV_INS_TIP.ptbv), stat("Return on equity", i.return_on_equity_pct, invPct, INV_INS_TIP.roe), stat("Return on tangible common equity", i.return_on_tangible_common_equity_pct, invPct, INV_INS_TIP.rotce), stat("Book value per share growth", i.book_value_per_share_trend_pct, invSignedPct, INV_INS_TIP.book_trend), stat("Diluted share count change", i.diluted_share_trend_pct, invSignedPct, INV_INS_TIP.shares)), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: "What the insurance itself is doing, before anything the investment portfolio did."
  }, health ? "Benefits and premiums" : "Underwriting"), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Premiums earned", i.premiums_earned, invMoney, INV_INS_TIP.premium_growth), stat("Premium growth", i.premium_growth_pct, invSignedPct, INV_INS_TIP.premium_growth), stat("Net income growth", i.net_income_growth_pct, invSignedPct, INV_INS_TIP.ni_growth), !spread && stat(health ? "Benefit ratio" : "Loss ratio", i.loss_ratio_pct, invPct, health ? INV_INS_TIP.benefit : INV_INS_TIP.loss), underwriting && stat("Expense ratio", i.expense_ratio_pct, invPct, INV_INS_TIP.expense), underwriting && stat("Combined ratio", i.combined_ratio_pct, invPct, INV_INS_TIP.combined), underwriting && stat("Underwriting profit", i.underwriting_profit, invMoney, INV_INS_TIP.uwprofit)), spread && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_INS_TIP.loss
  }, (i.loss_ratio_pct || {}).reason), crt.state && /*#__PURE__*/React.createElement("div", {
    className: `inv-note ${crt.state === "DETERIORATING" ? "down" : ""}`,
    title: `${INV_INS_TIP.combined}\n\n${crt.basis || ""}`
  }, "The combined ratio is ", /*#__PURE__*/React.createElement("b", null, crt.state), crt.change_pp != null && ` — ${invSignedPct(crt.change_pp)} against the year before`, "."), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_INS_TIP.development
  }, "Reserves"), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Reserves held", i.reserves, invMoney, INV_INS_TIP.reserves), stat("Reserves to premiums", i.reserves_to_premiums, v => invRatio(v, 1), INV_INS_TIP.res_mult), stat("Earlier years' reserve movement", i.reserve_development_pct_premiums, invSignedPct, INV_INS_TIP.development), (i.future_policy_benefits || {}).value != null && stat("Future policy benefits", i.future_policy_benefits, invMoney, INV_INS_TIP.fpb)), dev.state && /*#__PURE__*/React.createElement("div", {
    className: `inv-note ${dev.state === "ADVERSE" ? "down" : ""}`,
    title: `${INV_INS_TIP.development}\n\n${dev.basis || ""}`
  }, "Reserves set aside in earlier years are proving", " ", /*#__PURE__*/React.createElement("b", null, dev.state === "ADVERSE" ? "INADEQUATE" : dev.state === "FAVOURABLE" ? "MORE THAN ENOUGH" : "ABOUT RIGHT"), dev.pct_premiums != null && ` — ${invSignedPct(dev.pct_premiums)} of a year's premiums`, "."), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_INS_TIP.nii
  }, "Investments and capital"), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Investment income", i.net_investment_income, invMoney, INV_INS_TIP.nii), stat("Investment income growth", i.net_investment_income_growth_pct, invSignedPct, INV_INS_TIP.nii), stat("Investment yield", i.investment_yield_pct, invPct, INV_INS_TIP.yield), stat("Equity to total assets", i.equity_to_assets_pct, invPct, INV_INS_TIP.capital)));
}

// ── Phase 5: broker-dealers ───────────────────────────────────────────────

const INV_BROKER_TIP = {
  evidence: "What in this filer's own balance sheet says it is a " + "broker-dealer rather than an asset manager or an exchange sharing its " + "industry code.\n\nThe SEC code 6211 holds Charles Schwab, Goldman Sachs " + "AND BlackRock; code 6200 holds LPL Financial and the CME. So the " + "question is answered from the filings — receivables from customers, " + "cash segregated for customers under the SEC's customer-protection rule, " + "brokerage commissions, dealer trading revenue, underwriting revenue — " + "rather than from the code.",
  subtype: "Retail, institutional or both, read from the annual report. " + "Unlike an insurer's subtype this does not change which numbers are " + "valid: both kinds are read on book value, return on equity and their " + "own history of price to earnings. So where the report cannot separate " + "them the model still runs and the mix is reported as undetermined.",
  bvps: "Shareholders' equity less preferred stock, per share.",
  pb: "Share price divided by book value per share.",
  tbvps: "Book value with goodwill and other intangibles taken out, per share.",
  ptbv: "Share price divided by tangible book value per share.",
  client_growth: "How much the money customers keep here has grown against " + "the same measure a year earlier, both read from the company's own " + "filing tables. There is no growth figure until a reading from a year " + "ago exists: filings are read forward from today and never back-filled.",
  net_new: "Money customers moved in less money they moved out, over the " + "period the filing reports. This is the number a brokerage is actually " + "judged on, and it is shown only where the company prints it in a table " + "this app can read without guessing at the label, the period or the " + "unit.",
  roe: "Net income to common over average shareholders' equity. A broker " + "earning thirty percent on its equity deserves a large premium to book " + "and one earning six percent deserves a discount.",
  rotce: "The same return measured against tangible common equity.",
  revenue: "Total revenue over the last twelve months.",
  rev_growth: "Growth in revenue against a year earlier.",
  eps_growth: "Growth in net income against a year earlier.",
  opmargin: "Operating profit as a share of revenue. Blank where the filer " + "does not tag operating profit separately, which most brokers do not.",
  comp: "Employee compensation and benefits as a share of revenue. It is the " + "largest single cost at every broker, so the direction it moves says " + "most of what there is to say about operating leverage here.",
  nii: "Net interest income — what the firm earns lending customers money " + "and investing their cash, after what that money costs it.",
  nii_share: "Net interest income as a share of total revenue. A broker " + "earning most of its revenue this way is closer to a bank than to a " + "commission business, and its earnings move with short-term interest " + "rates rather than with trading volumes.",
  transaction: "Commissions on customer trades plus dealer trading revenue. " + "Only the components this filer actually tags are included, so it is a " + "floor rather than a total.",
  ib: "Underwriting and advisory revenue over the last twelve months.",
  receivables: "Money owed to the firm by its own customers — chiefly margin " + "lending.",
  segregated: "Cash and securities held apart from the firm's own money for " + "the benefit of customers, as the SEC's customer-protection rule requires.",
  clients: "Client assets, assets under administration and net new assets " + "are the numbers this industry actually runs on, and they are not in the " + "machine-readable filings anywhere.\n\nWhere they appear at all they are " + "in the tables of the filings themselves — usually the earnings release " + "attached to an 8-K — and that is where this figure comes from: a row " + "whose label is one of a fixed list, under a column that names a " + "period, in a table that states its own scale. A row that is ambiguous, " + "a column with no period, or a table that leaves its scale to be " + "guessed at all produce nothing rather than a number, because the " + "difference between millions and billions is a factor of a thousand.\n\n" + "Where no such table exists it stays blank. It is never estimated from " + "the balance sheet: a broker's own capital says nothing about how much " + "of its customers' money it holds.",
  leverage: "Total assets over all the equity on the balance sheet. A " + "broker-dealer is levered by design — customer margin loans are assets " + "funded by customer credit balances — so the level says less than the " + "direction.",
  deposits: "Customer deposits as a share of total assets. Above about a " + "tenth, a material part of what the firm does is banking.",
  shares: "Change in the diluted share count against a year earlier.",
  book_trend: "Growth in book value per share against a year earlier."
};
function InvBroker({
  broker
}) {
  const b = broker || {};
  const ev = b.broker_evidence || {};
  if (!b.available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "inv-fv-why",
      title: INV_BROKER_TIP.evidence
    }, b.reason || "This filer could not be measured as a broker.");
  }
  const stat = (label, block, fmt, tip, tone) => /*#__PURE__*/React.createElement(InvStat, {
    label: label,
    value: invBankVal(block, fmt),
    tip: tip,
    basis: (block || {}).basis,
    reason: (block || {}).reason,
    tone: tone
  });
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-bank"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_BROKER_TIP.evidence
  }, "Read as a ", /*#__PURE__*/React.createElement("b", null, b.subtype_label || b.subtype), ". Its own filings say it is a broker-dealer:", " ", (ev.evidence || []).map(e => e.phrase).join("; "), "."), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Book value per share", b.book_per_share, invPrice, INV_BROKER_TIP.bvps), stat("Price to book value", b.price_to_book, v => invRatio(v, 2), INV_BROKER_TIP.pb), stat("Tangible book value per share", b.tangible_book_per_share, invPrice, INV_BROKER_TIP.tbvps), stat("Price to tangible book value", b.price_to_tangible_book, v => invRatio(v, 2), INV_BROKER_TIP.ptbv), stat("Return on equity", b.return_on_equity_pct, invPct, INV_BROKER_TIP.roe), stat("Return on tangible common equity", b.return_on_tangible_common_equity_pct, invPct, INV_BROKER_TIP.rotce), stat("Book value per share growth", b.book_value_per_share_trend_pct, invSignedPct, INV_BROKER_TIP.book_trend), stat("Diluted share count change", b.diluted_share_trend_pct, invSignedPct, INV_BROKER_TIP.shares)), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: "Where the revenue comes from and what it costs to earn."
  }, "Operating economics"), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Revenue", b.revenue_ttm, invMoney, INV_BROKER_TIP.revenue), stat("Revenue growth", b.revenue_growth_pct, invSignedPct, INV_BROKER_TIP.rev_growth), stat("Earnings growth", b.eps_growth_pct, invSignedPct, INV_BROKER_TIP.eps_growth), stat("Compensation as share of revenue", b.compensation_ratio_pct, invPct, INV_BROKER_TIP.comp), stat("Operating margin", b.operating_margin_pct, invPct, INV_BROKER_TIP.opmargin), stat("Transaction revenue", b.transaction_revenue, invMoney, INV_BROKER_TIP.transaction)), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_BROKER_TIP.nii_share
  }, "Net interest and the balance sheet"), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Net interest income", b.net_interest_income, invMoney, INV_BROKER_TIP.nii), stat("Net interest share of revenue", b.net_interest_share_of_revenue_pct, invPct, INV_BROKER_TIP.nii_share), stat("Customer receivables", b.customer_receivables, invMoney, INV_BROKER_TIP.receivables), stat("Segregated customer cash", b.segregated_cash, invMoney, INV_BROKER_TIP.segregated), stat("Assets to equity", b.assets_to_equity, v => invRatio(v, 1), INV_BROKER_TIP.leverage), stat("Deposits as share of assets", b.deposits_share_of_assets_pct, invPct, INV_BROKER_TIP.deposits)), b.banking_note && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_BROKER_TIP.deposits
  }, b.banking_note), (b.client_assets || {}).value != null && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_BROKER_TIP.clients
  }, "The customer franchise"), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Client assets", b.client_assets, invMoney, INV_BROKER_TIP.clients), stat("Client asset growth over a year", b.client_asset_growth_pct, invSignedPct, INV_BROKER_TIP.client_growth), stat("Net new client money", b.net_new_assets, invMoney, INV_BROKER_TIP.net_new)), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_BROKER_TIP.clients
  }, (b.client_assets || {}).basis)), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_BROKER_TIP.clients
  }, "Not reported, and why"), /*#__PURE__*/React.createElement("ul", {
    className: "inv-reasons"
  }, (b.client_assets || {}).value == null && /*#__PURE__*/React.createElement("li", {
    title: INV_BROKER_TIP.clients
  }, /*#__PURE__*/React.createElement("b", null, "Client assets and net new assets"), " \u2014", " ", (b.client_assets || {}).reason), /*#__PURE__*/React.createElement("li", null, /*#__PURE__*/React.createElement("b", null, "Advisory and asset-management fees"), " \u2014", " ", (b.asset_management_revenue || {}).reason)));
}

// ── Phase 6: which engine this company went to, and why ───────────────────

const INV_ROUTE_TIP = {
  what: "An SEC industry code is a filing convenience, not a description of " + "a business. Code 6211 holds Charles Schwab, Goldman Sachs and " + "BlackRock; code 6200 holds LPL Financial and the CME. Which of those a " + "company is gets decided here, from what is actually on its balance " + "sheet and what its revenue is made of — and that decision is what " + "picks the valuation model below.",
  cls: "The kind of business this is, judged from its filings rather than " + "from its industry code.",
  model: "The valuation model this company is sent to. An exchange and an " + "asset manager both go to the ordinary one: both have ordinary revenue, " + "ordinary margins and ordinary free cash flow, and neither is valued on " + "book value.",
  why: "The evidence behind the decision, in the order it was weighed.",
  exposure: "Every business this company is materially in, with the " + "measurement each one was judged on and the level it had to clear. " + "Shares are not comparable across the list — deposits are a share of " + "assets and premiums a share of revenue — so nothing here averages them.",
  accounts: "Whether the accounts behave like an operating company's at " + "all: capital expenditure reported, and operating cash flow reported, " + "positive and within sight of revenue. A broker fails this, because " + "customer money moves through its operating cash flow — Goldman Sachs " + "reports minus thirty-nine billion dollars of it against sixty-six " + "billion of revenue, which is the wrong measure rather than a problem.",
  chapter: "Which annual report the business description was read from, how " + "it was found in the document, and how sure the reader is. A business " + "chapter the reader is not confident it found is shown as text and is " + "not allowed to decide which model runs."
};

// "a exchange" and "a asset manager" are the two the routing labels
// actually produce; the rule is general so a new class cannot reintroduce it.
function invArticle(word) {
  return ("aeiou".includes((word || "")[0]) ? "an " : "a ") + word;
}
function InvRouting({
  routing,
  extraction
}) {
  const r = routing || {};
  if (!r.business_class) return null;
  const stat = (label, block, fmt, tip) => /*#__PURE__*/React.createElement(InvStat, {
    label: label,
    value: invBankVal(block, fmt),
    tip: tip,
    basis: (block || {}).basis,
    reason: (block || {}).reason
  });
  const corp = r.corporate_accounts || {};
  const ex = extraction || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-bank"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_ROUTE_TIP.what
  }, "Read as ", invArticle(String(r.label || "").toLowerCase()), ", and valued with the", " ", String(r.model || "standard").toLowerCase().replace(/_/g, " "), " model.", r.note ? " " + r.note : ""), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Kind of business", {
    value: r.label
  }, v => v, INV_ROUTE_TIP.cls), stat("Valuation model", {
    value: r.model
  }, v => v, INV_ROUTE_TIP.model), stat("How sure", {
    value: r.confidence
  }, v => v, INV_ROUTE_TIP.cls)), (r.why || []).length > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_ROUTE_TIP.why
  }, "Why"), /*#__PURE__*/React.createElement("ul", {
    className: "inv-reasons"
  }, (r.why || []).map((w, i) => /*#__PURE__*/React.createElement("li", {
    key: i,
    title: INV_ROUTE_TIP.why
  }, w)))), (r.exposures || []).some(e => e.material) && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_ROUTE_TIP.exposure
  }, "What it is materially in"), /*#__PURE__*/React.createElement("ul", {
    className: "inv-reasons"
  }, (r.exposures || []).filter(e => e.material).map((e, i) => /*#__PURE__*/React.createElement("li", {
    key: i,
    title: INV_ROUTE_TIP.exposure
  }, /*#__PURE__*/React.createElement("b", null, e.label), " \u2014", " ", e.share_pct == null ? e.measure : `${invPct(e.share_pct)} by ${e.measure}, against a ` + `${invPct(e.threshold_pct)} level`)))), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_ROUTE_TIP.accounts
  }, corp.ok ? corp.basis : corp.reason), ex.confidence && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_ROUTE_TIP.chapter
  }, "Business description read from the ", ex.form, " filed", " ", invDate(ex.filed), " by ", ex.method, "; confidence ", ex.confidence, "."));
}
const INV_HYBRID_TIP = {
  what: "Some companies are two financial businesses at once — a wealth " + "manager and an annuity writer, an asset manager that bought an " + "insurer — and each of those is valued a different way. Which model is " + "right depends on which business dominates, and that is stated here " + "rather than assumed.",
  case: "ONE MODEL RUNS means only one of the businesses can be valued from " + "the filings, so that one is used and the rest are disclosed. MODELS " + "AGREE means both can be valued and they land close enough together " + "that the usual confidence machinery settles it. MODELS DISAGREE means " + "they do not, and no single fair value is shown.",
  spread: "How far apart the two models are about what this company is " + "worth, measured between their base values.",
  nosotp: "No sum of the parts is attempted. Segment revenue and segment " + "income are not in the SEC's machine-readable filings at all, so a " + "segment-weighted valuation would be a guess with a decimal point on it."
};
function InvHybrid({
  hybrid
}) {
  const h = hybrid || {};
  if (!h.is_hybrid) return null;
  const stat = (label, block, fmt, tip) => /*#__PURE__*/React.createElement(InvStat, {
    label: label,
    value: invBankVal(block, fmt),
    tip: tip,
    basis: (block || {}).basis,
    reason: (block || {}).reason
  });
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-bank"
  }, /*#__PURE__*/React.createElement("div", {
    className: `inv-note ${h.reliable ? "" : "down"}`,
    title: INV_HYBRID_TIP.what
  }, h.reason), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, stat("Which case this is", {
    value: h.case
  }, v => v, INV_HYBRID_TIP.case), stat("How far apart the models are", {
    value: h.disagreement_pct
  }, invPct, INV_HYBRID_TIP.spread)), (h.valuations || []).length > 0 && /*#__PURE__*/React.createElement("ul", {
    className: "inv-reasons"
  }, (h.valuations || []).map((v, i) => /*#__PURE__*/React.createElement("li", {
    key: i,
    title: INV_HYBRID_TIP.case
  }, /*#__PURE__*/React.createElement("b", null, v.label), " \u2014", " ", v.available && v.base != null ? `base ${invPrice(v.base)} from the ` + `${String(v.model).toLowerCase()} model` : v.reason || "this model cannot be built from the filings"))), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_HYBRID_TIP.nosotp
  }, INV_HYBRID_TIP.nosotp));
}
const INV_XCHECK_TIP = {
  what: "Some measures have to be rebuilt from the machine-readable filings " + "because the filings do not tag them directly — funds from operations " + "for a property trust, the combined ratio for an insurer. Where the " + "company also prints the figure in a table of its own, the two are put " + "side by side here.",
  state: "MATCH means the rebuilt figure and the published one are the same " + "number. MINOR DIFFERENCE means they differ by less than the tolerance. " + "MATERIAL MISMATCH means they do not agree, and the valuation built on " + "the rebuilt figure is not trusted at full confidence until the " + "difference is explained. INCOMPATIBLE BASIS means the company does " + "publish the measure, on a different basis, period or window — a " + "property trust publishes funds from operations five ways and only one " + "of them is what a share is entitled to. PUBLISHED UNAVAILABLE means it " + "does not print the measure in a table this app will read. The nicer of " + "the two numbers is never quietly chosen.",
  basis: "Which definition each side of the comparison uses, and over what " + "period. A quarter compared with a year is a 300% disagreement about " + "nothing, so a comparison is only made when the basis, the period and " + "the window all match.",
  measures: "Client assets, assets under administration, advisory assets " + "and assets under management are four different things, and this app " + "never treats one as standing in for another. Assets under " + "administration include money the firm only holds; advisory assets are " + "the part it is paid to advise on; assets under management are the part " + "it actually runs. Each is stored under its own name with its own " + "period, scope and unit.",
  unit: "Where the scale of this figure was read from — the row's own " + "label, a heading above it, its column, the table, or the caption. The " + "most specific statement wins, and a figure whose scale nothing states " + "is refused rather than guessed at, because the guess is worth a factor " + "of a thousand."
};
const INV_XCHECK_BAD = ["MATERIAL MISMATCH"];
function InvCrossCheck({
  cross
}) {
  const c = cross || {};
  const checks = c.checks || [];
  const measures = (c.measures || {}).rows || [];
  if (!checks.length && !measures.length) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-bank"
  }, !!c.reason && /*#__PURE__*/React.createElement("div", {
    className: `inv-note ${c.mismatches ? "down" : ""}`,
    title: INV_XCHECK_TIP.state
  }, c.reason), !!checks.length && /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.what
  }, "Measure"), /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.what
  }, "Published by the company"), /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.what
  }, "Rebuilt from the filings"), /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.state
  }, "Difference"), /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.state
  }, "State"), /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.basis
  }, "Basis and period"))), /*#__PURE__*/React.createElement("tbody", null, checks.map((row, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, row.measure), /*#__PURE__*/React.createElement("td", null, row.unit === "percent" ? invPct(row.published) : invMoney(row.published)), /*#__PURE__*/React.createElement("td", null, row.unit === "percent" ? invPct(row.reconstructed) : invMoney(row.reconstructed)), /*#__PURE__*/React.createElement("td", null, row.difference_pct == null ? "—" : invPct(row.difference_pct)), /*#__PURE__*/React.createElement("td", {
    className: INV_XCHECK_BAD.includes(row.state) ? "down" : "",
    title: row.reason || INV_XCHECK_TIP.state
  }, row.state), /*#__PURE__*/React.createElement("td", {
    title: row.note || INV_XCHECK_TIP.basis
  }, [row.published_basis, row.published_period ? invDate(row.published_period) : ""].filter(Boolean).join(" · ") || "—"))))), !!measures.length && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_XCHECK_TIP.measures
  }, (c.measures || {}).reason), /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.measures
  }, "Measure read from a filing"), /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.measures
  }, "Amount"), /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.basis
  }, "As of"), /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.measures
  }, "Whole company or one part"), /*#__PURE__*/React.createElement("th", {
    title: INV_XCHECK_TIP.unit
  }, "Scale, and where it was read"))), /*#__PURE__*/React.createElement("tbody", null, measures.map((row, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", {
    title: row.row_label ? `Read from a row labelled "${row.row_label}"` : INV_XCHECK_TIP.measures
  }, row.label), /*#__PURE__*/React.createElement("td", null, invMoney(row.value)), /*#__PURE__*/React.createElement("td", null, invDate(row.period)), /*#__PURE__*/React.createElement("td", {
    title: INV_XCHECK_TIP.measures
  }, row.scope === "CONSOLIDATED" ? "Whole company" : row.scope === "SEGMENT" ? "One segment" : "The filing does not say"), /*#__PURE__*/React.createElement("td", {
    title: INV_XCHECK_TIP.unit
  }, [row.unit, row.unit_source].filter(Boolean).join(" · ") || "—")))))));
}

// ── Phase 4: the covered-call simulator ───────────────────────────────────

const INV_CC_TIP = {
  what: "Owning a hundred shares and selling calls against them, over and " + "over, through the actual lifecycle: sell a call, the call expires or is " + "assigned or is bought back or is rolled, sell the next one. The " + "single-expiration buy-write in the structure comparison answers a " + "different question — which structure to open today — and repeating its " + "result twelve times is not a model of doing this for a year.",
  tw: "What the account is worth at the end: shares still held, plus cash, " + "less the value of any call still open.",
  vs: "Terminal wealth against simply owning the same hundred shares over the " + "same days on the same starting capital, with dividends treated " + "identically. THIS is the comparison that decides whether the strategy " + "worked. Everything else on the row is context for it.",
  cagr: "The compound annual rate that turns the starting capital into the " + "terminal wealth over this run's length.",
  dd: "The deepest fall from a previous high in account value, marking any " + "open call at what it would cost to close.",
  premium: "Total credit received from selling calls, before the cost of " + "buying any of them back.",
  forfeited: "The money left on the table when a call capped a rally: the " + "share price above the strike at expiration, times a hundred. This is the " + "cost of the strategy, and it does not appear anywhere in an option win " + "rate.",
  win: "The share of individual calls that made money. This is NOT whether " + "the strategy worked. A ninety-five percent win rate on calls that keep " + "capping a rising stock still loses to owning the shares — and that is " + "the exact case this column exists to stop you reading as success.",
  assigned: "How often the shares were called away at the strike.",
  rolled: "How often a call was bought back and replaced. A roll never erases " + "a loss here: the loss on the option being closed is realized and stays " + "realized, and the credit on the new one is a separate event.",
  days: "Average calendar days from selling a call to closing it.",
  prem_notional: "Each call's credit as a percentage of the FULL value of the " + "hundred shares it was sold against — never of a margin or " + "buying-power figure, which would flatter the number by the money that " + "was not put to work.",
  basis: "Where the option prices came from. REAL CHAIN BACKTEST means every " + "fill came from an end-of-day chain snapshot this app recorded on that " + "trading day. MODEL-BASED ESTIMATE means there was no snapshot and the " + "price is a Black-Scholes value against a modelled volatility path. " + "Historical option quotes are never invented — where there is no " + "snapshot, the price is a model output and says so.",
  policy: "The tenor, the strike rule, the roll rule and what happens on " + "assignment. No combination is treated as correct: that is the point of " + "running them side by side."
};
const INV_READY_TIP = {
  days: "How many separate trading days of real end-of-day option chains this " + "app has captured for this ticker. It grows by one for every day the app " + "runs after the close and can NEVER grow backwards: there is no source " + "of historical option chains this dashboard can reach, and inventing one " + "would poison every backtest built on top of it.",
  coverage: "The share of the days this run walks through that have a " + "captured chain behind them. The rest were priced by the model.",
  mode: "REAL CHAIN BACKTEST — every fill came from a chain this app " + "recorded that day.\nPART REAL — some fills are real and some are model " + "prices, counted separately below.\nMODEL-BASED ESTIMATE — no chain was " + "captured, so every price is a Black-Scholes value against a modelled " + "volatility path.\n\nA real fill and a model fill are different KINDS of " + "number rather than the same number known to different precisions, so " + "they are never blended into one accuracy figure.",
  contracts: "How many individual option quotes are stored across all the " + "captured days."
};
function InvReadiness({
  readiness
}) {
  const r = readiness || {};
  if (!r.mode) return null;
  const real = r.mode === "REAL CHAIN BACKTEST";
  return /*#__PURE__*/React.createElement("div", {
    className: `inv-note ${real ? "" : "down"}`,
    title: INV_READY_TIP.mode
  }, /*#__PURE__*/React.createElement("b", null, r.mode), " \u2014 ", /*#__PURE__*/React.createElement("span", {
    title: INV_READY_TIP.days
  }, r.days, " day", r.days === 1 ? "" : "s", " of real option chains captured", r.first && /*#__PURE__*/React.createElement(React.Fragment, null, " since ", invShortDate(r.first))), r.window_coverage_pct != null && /*#__PURE__*/React.createElement("span", {
    title: INV_READY_TIP.coverage
  }, ", covering ", invPct(r.window_coverage_pct, 0), " of the days this run walks through"), r.contracts > 0 && /*#__PURE__*/React.createElement("span", {
    title: INV_READY_TIP.contracts
  }, " ", "(", r.contracts.toLocaleString(), " stored quotes)"), ". ", /*#__PURE__*/React.createElement("span", {
    title: INV_READY_TIP.days
  }, r.backfill_note));
}
function InvCoveredCall({
  apiFetch,
  symbol
}) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [years, setYears] = useState(3);
  const load = React.useCallback(yrs => {
    if (!symbol) return;
    setBusy(true);
    apiFetch(`/api/invest/covered_call?symbol=${encodeURIComponent(symbol)}&years=${yrs}`).then(r => r.json()).then(j => setData(j)).catch(e => setData({
      available: false,
      reason: String(e.message || e)
    })).finally(() => setBusy(false));
  }, [apiFetch, symbol]);
  useEffect(() => {
    setData(null);
  }, [symbol]);
  const d = data || {};
  const rows = d.rows || [];
  const hold = d.buy_and_hold || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-cc"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_CC_TIP.what
  }, "Covered call income simulator", /*#__PURE__*/React.createElement("span", {
    className: "inv-cc-ctrl"
  }, /*#__PURE__*/React.createElement("select", {
    value: years,
    onChange: e => setYears(Number(e.target.value)),
    title: "How far back to run the simulation."
  }, /*#__PURE__*/React.createElement("option", {
    value: 1
  }, "1 year"), /*#__PURE__*/React.createElement("option", {
    value: 2
  }, "2 years"), /*#__PURE__*/React.createElement("option", {
    value: 3
  }, "3 years"), /*#__PURE__*/React.createElement("option", {
    value: 5
  }, "5 years")), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => load(years),
    disabled: busy,
    title: "Walk every policy through this ticker's own price history, day by day."
  }, busy ? "Running…" : data ? "Run again" : "Run"))), !data && !busy && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_CC_TIP.what
  }, "Runs ten covered-call policies through ", symbol, "'s own price history \u2014 three tenors, four strike rules, four roll rules \u2014 and compares each one against simply owning the shares. Not run automatically because it walks several years of prices for every policy."), data && !d.available && /*#__PURE__*/React.createElement("div", {
    className: "inv-fv-why"
  }, d.reason || "This simulation could not be run."), data && d.available && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_CC_TIP.vs
  }, "Over ", (d.years || 0).toFixed(1), " years from ", invShortDate(d.from), " to", " ", invShortDate(d.to), ", ", /*#__PURE__*/React.createElement("b", null, d.n_beat_buy_and_hold, " of ", d.n_policies), " ", "policies finished ahead of simply owning the shares, which ended at", " ", /*#__PURE__*/React.createElement("b", null, invMoney(hold.terminal_wealth, 0)), " on", " ", invMoney(hold.starting_capital, 0), " of starting capital."), /*#__PURE__*/React.createElement(InvReadiness, {
    readiness: d.readiness
  }), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_CC_TIP.basis
  }, (rows[0] || {}).fill_basis, " \xB7 ", d.fill_note || ""), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: INV_CC_TIP.policy
  }, "Policy"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.tw
  }, "Ends with"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.vs
  }, "Against owning"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.cagr
  }, "Yearly rate"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.dd
  }, "Worst fall"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.premium
  }, "Premium"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.forfeited
  }, "Upside given up"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How many calls were sold over the run."
  }, "Calls"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.win
  }, "Calls that won"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.assigned
  }, "Assigned"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.rolled
  }, "Rolled"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.days
  }, "Days held"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_CC_TIP.prem_notional
  }, "Credit of notional"))), /*#__PURE__*/React.createElement("tbody", null, /*#__PURE__*/React.createElement("tr", {
    className: "inv-row-hold"
  }, /*#__PURE__*/React.createElement("td", {
    title: "The same money in the same shares over the same days, doing nothing. Every row above is measured against this one."
  }, /*#__PURE__*/React.createElement("b", null, "Owning the shares and doing nothing")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, /*#__PURE__*/React.createElement("b", null, invMoney(hold.terminal_wealth, 0))), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "\u2014"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(hold.cagr_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(hold.max_drawdown_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "\u2014"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "\u2014"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "0"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "\u2014"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "\u2014"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "\u2014"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "\u2014"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "\u2014")), rows.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    title: r.label
  }, /*#__PURE__*/React.createElement("td", null, r.label), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invMoney(r.terminal_wealth, 0)), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${(r.versus_buy_and_hold || 0) >= 0 ? "up" : "down"}`
  }, r.versus_buy_and_hold == null ? invNA : `${r.versus_buy_and_hold >= 0 ? "+" : "−"}${invMoney(Math.abs(r.versus_buy_and_hold), 0)}`), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(r.cagr_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down"
  }, invPct(r.max_drawdown_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invMoney(r.premium_income, 0)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invMoney(r.upside_forfeited, 0)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invCount(r.calls_sold)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(r.call_win_rate_pct, 0)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(r.assignment_rate_pct, 0)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(r.roll_rate_pct, 0)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invCount(r.average_days_in_trade)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, invPct(r.average_premium_pct_of_notional, 2))))))), (d.stranded_in_cash || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "inv-note down",
    title: "After the shares were called away, the strike they were sold at no longer bought a hundred shares back. The position could not be rebuilt without adding money, so the run finished holding cash \u2014 which is arithmetic rather than a decision, and worth knowing before reading the result as a choice the strategy made."
  }, d.stranded_in_cash.length, " of ", d.n_policies, " policies ended holding cash because the assignment proceeds no longer bought the shares back: ", d.stranded_in_cash.join("; "), "."), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: "One company over one stretch of one market is a single observation. It describes what happened here; it is not evidence that any of these rules works in general."
  }, d.verdict_note), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: "The fair-value-aware strike rules need a fair value on each historical day, and the only honest source of one is what this app actually recorded that day. Applying today's valuation to a past day would be exactly the lookahead the validation panel exists to prevent."
  }, d.fair_value_note)));
}

// ── Phase 4: forward validation ───────────────────────────────────────────

const INV_FWD_TIP = {
  what: "Since this tab first ran it has written one snapshot per ticker per " + "day: the price, the verdict, the preferred structure, the Bear/Base/Bull " + "range, the buy zone and the exact contracts it recommended. This panel " + "reads that store FORWARD. Nothing is recomputed and nothing is " + "rewritten — each recommendation is judged exactly as it was written " + "down, against prices that came after it.",
  sample: "How many completed observations sit behind the row. Sample size " + "comes first here on purpose: below the minimum, the row says " + "INSUFFICIENT SAMPLE and shows nothing else, because a median of eleven " + "things is an anecdote wearing a decimal point.",
  median: "The middle outcome, not the average — one exceptional result " + "should not move a summary of what usually happened.",
  hit: "The share of observations that ended higher than where they started.",
  excess: "The median outcome less the benchmark's return over the same days.",
  sector: "The median outcome less the median of the OTHER companies in the " + "same industry recorded on the same day. The company is left out of the " + "group it is measured against.",
  mae: "Maximum adverse excursion: the worst the position got before the " + "horizon ended. A recommendation that finished up ten percent after " + "falling thirty is not the same experience as one that rose steadily.",
  contained: "How often the actual price at the horizon landed inside the " + "Bear-to-Bull range that was published on the day. Too high means the " + "range is so wide it says nothing; too low means it is too narrow to " + "trust.",
  horizon: "Only COMPLETED horizons are counted. A ninety-day outcome appears " + "ninety days after the recommendation and not a day sooner — a partial " + "window would quietly favour whatever the market did most recently.",
  contracts: "The EXACT contract that was recommended, scored at its own " + "expiration: that strike, that expiry, that credit. Never a better one " + "chosen after seeing the outcome, which is the easiest way there is to " + "manufacture a good result.",
  config: "The hash of the settings that produced each recommendation. " + "Results under materially different rule sets are shown separately rather " + "than combined and called one strategy.",
  tossup: "How often the structure comparison said the winner was too close " + "to call. A high frequency is information about the structures being " + "genuinely similar, not a fault.",
  noscore: "There is deliberately no accuracy score. A single number blending " + "a hit rate, a median return and a calibration check is one nobody can " + "act on and everybody can be reassured by."
};
function invFwdCell(s) {
  if (!s || !s.sufficient) return /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, (s || {}).verdict || invNA);
  return /*#__PURE__*/React.createElement(React.Fragment, null, invSignedPct(s.median_return_pct));
}
function InvFwdTable({
  title,
  tip,
  groups
}) {
  const keys = Object.keys(groups || {});
  if (!keys.length) return null;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: tip
  }, title), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: tip
  }, title), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_FWD_TIP.sample
  }, "Observations"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_FWD_TIP.median
  }, "Median outcome"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_FWD_TIP.hit
  }, "Ended higher"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_FWD_TIP.excess
  }, "Against benchmark"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: INV_FWD_TIP.mae
  }, "Median worst point"))), /*#__PURE__*/React.createElement("tbody", null, keys.map(k => {
    const s = groups[k] || {};
    return /*#__PURE__*/React.createElement("tr", {
      key: k,
      title: s.sufficient ? "" : s.reason
    }, /*#__PURE__*/React.createElement("td", null, k), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invCount(s.n)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invFwdCell(s)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, s.sufficient ? invPct(s.hit_rate_pct, 0) : "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, s.sufficient && s.median_excess_return_pct != null ? invSignedPct(s.median_excess_return_pct) : "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num down"
    }, s.sufficient ? invPct(s.median_max_adverse_excursion_pct) : "—"));
  })))));
}
const INV_RECORDING_TIP = "Whether what is being written down TODAY carries everything a future " + "scoring pass will need to settle up against the recommendation exactly " + "as it was made — the share price, the exact rule version, the " + "recommendation, the preferred structure, the exact contract and the quote " + "it carried, the benchmark it will be measured against, the fair value and " + "the buy zone.\n\nThis looks FORWARD. Rows already on disk are never " + "rewritten, so a row written before a field existed will always lack it and " + "that is correct. What matters is whether today's rows are complete, " + "because nothing can be filled in after the fact.";
function InvRecording({
  recording
}) {
  const r = recording || {};
  if (!r.fields || !r.fields.length) return null;
  const gaps = r.fields.filter(f => !f.complete);
  return /*#__PURE__*/React.createElement("div", {
    className: `inv-note ${gaps.length ? "down" : ""}`,
    title: INV_RECORDING_TIP
  }, /*#__PURE__*/React.createElement("b", null, "Recording ", r.complete, " of ", r.fields.length, " required fields"), " across", " ", r.tickers, " ticker", r.tickers === 1 ? "" : "s", ". ", r.reason, gaps.length > 0 && /*#__PURE__*/React.createElement("ul", {
    className: "inv-reasons"
  }, gaps.map(f => /*#__PURE__*/React.createElement("li", {
    key: f.field,
    title: INV_RECORDING_TIP
  }, /*#__PURE__*/React.createElement("b", null, f.what), " \u2014 recorded for ", f.n, " of ", f.of, f.missing.length > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, " (missing: ", f.missing.join(", "), ")")))));
}
function InvValidation({
  apiFetch
}) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [horizon, setHorizon] = useState("90");
  const load = React.useCallback(() => {
    setBusy(true);
    apiFetch("/api/invest/validation").then(r => r.json()).then(j => setData(j)).catch(e => setData({
      available: false,
      reason: String(e.message || e)
    })).finally(() => setBusy(false));
  }, [apiFetch]);
  useEffect(() => {
    load();
  }, [load]);
  const d = data || {};
  const cal = d.calibration || {};
  const horizons = cal.horizons || {};
  const available = Object.keys(horizons);
  const blk = horizons[horizon] || horizons[available[0]] || {};
  const overall = blk.overall || {};
  const contest = blk.attractive_versus_wait || {};
  const avoid = blk.avoid_versus_rest || {};
  const structures = (d.structures || {}).structures || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-fwd"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_FWD_TIP.what
  }, "Forward validation", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " ", "\xB7 ", d.stored_rows || 0, " recorded day", d.stored_rows === 1 ? "" : "s", " across", " ", d.tickers_with_history || 0, " ticker", d.tickers_with_history === 1 ? "" : "s", cal.total_observations != null && ` · ${cal.total_observations} completed observations`), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost inv-scan-refresh",
    onClick: load,
    disabled: busy,
    title: "Re-read the snapshot store and recompute the outcomes."
  }, busy ? "Reading…" : "Refresh")), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_FWD_TIP.noscore
  }, "No accuracy score. Sample size first, then what the sample says, then nothing where it says nothing."), /*#__PURE__*/React.createElement(InvRecording, {
    recording: d.recording
  }), !cal.total_observations && /*#__PURE__*/React.createElement("div", {
    className: "inv-fv-why",
    title: INV_FWD_TIP.horizon
  }, cal.reason || d.reason || "Nothing has aged far enough to score yet."), cal.total_observations > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "inv-fwd-tabs",
    title: INV_FWD_TIP.horizon
  }, ["30", "90", "180", "365"].map(h => /*#__PURE__*/React.createElement("button", {
    key: h,
    className: `btn ghost ${horizon === h ? "active" : ""}`,
    disabled: !horizons[h],
    onClick: () => setHorizon(h),
    title: horizons[h] ? `${(horizons[h].overall || {}).n || 0} completed ${h}-day observations.` : `No ${h}-day horizon has completed yet.`
  }, h, " days"))), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Observations",
    value: invCount(overall.n),
    tip: INV_FWD_TIP.sample
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Median outcome",
    value: overall.sufficient ? invSignedPct(overall.median_return_pct) : overall.verdict || invNA,
    tip: INV_FWD_TIP.median,
    reason: overall.sufficient ? "" : overall.reason
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Ended higher",
    value: overall.sufficient ? invPct(overall.hit_rate_pct, 0) : invNA,
    tip: INV_FWD_TIP.hit
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Against benchmark",
    value: overall.median_excess_return_pct != null ? invSignedPct(overall.median_excess_return_pct) : invNA,
    tip: INV_FWD_TIP.excess
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Against its own industry",
    value: overall.median_sector_relative_pct != null ? invSignedPct(overall.median_sector_relative_pct) : invNA,
    tip: INV_FWD_TIP.sector
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Median worst point",
    value: overall.sufficient ? invPct(overall.median_max_adverse_excursion_pct) : invNA,
    tip: INV_FWD_TIP.mae
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Outcome inside Bear to Bull",
    value: overall.range_contained_pct != null ? invPct(overall.range_contained_pct, 0) : invNA,
    tip: INV_FWD_TIP.contained
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Too close to call",
    value: (blk.toss_up || {}).frequency_pct != null ? invPct(blk.toss_up.frequency_pct, 0) : invNA,
    tip: INV_FWD_TIP.tossup
  })), contest.reason && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: "Whether the recommendations to own actually did better than the ones told to wait. This is the question the whole tab is answering about itself."
  }, /*#__PURE__*/React.createElement("b", null, "Owned against waited: ", contest.verdict), " \u2014 ", contest.reason), avoid.reason && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: "Whether the names this app told you to avoid actually did worse than everything else."
  }, /*#__PURE__*/React.createElement("b", null, "Avoided against the rest: ", avoid.verdict), " \u2014 ", avoid.reason), /*#__PURE__*/React.createElement(InvFwdTable, {
    title: "By verdict",
    tip: "How each verdict actually did.",
    groups: blk.by_verdict
  }), /*#__PURE__*/React.createElement(InvFwdTable, {
    title: "By preferred structure",
    tip: "How the outcome differed by which structure won the comparison that day.",
    groups: blk.by_preferred_structure
  }), /*#__PURE__*/React.createElement(InvFwdTable, {
    title: "By valuation percentile",
    tip: "Whether buying at the cheap end of a company's own history actually worked.",
    groups: blk.by_valuation_percentile
  }), /*#__PURE__*/React.createElement(InvFwdTable, {
    title: "By quality",
    tip: "Outcomes split by the quality reading on the day.",
    groups: blk.by_quality
  }), /*#__PURE__*/React.createElement(InvFwdTable, {
    title: "By revision state",
    tip: "Outcomes split by whether analysts were raising or cutting.",
    groups: blk.by_revisions
  }), /*#__PURE__*/React.createElement(InvFwdTable, {
    title: "By value trap risk",
    tip: "Whether the value-trap check identified the outcomes it was meant to.",
    groups: blk.by_value_trap
  }), /*#__PURE__*/React.createElement(InvFwdTable, {
    title: "By fair value confidence",
    tip: "Whether a HIGH-confidence valuation actually produced better outcomes than a LOW one.",
    groups: blk.by_fair_value_confidence
  }), blk.by_config && /*#__PURE__*/React.createElement(InvFwdTable, {
    title: "By configuration",
    tip: INV_FWD_TIP.config,
    groups: blk.by_config
  }), cal.config_note && /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_FWD_TIP.config
  }, cal.config_note)), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: INV_FWD_TIP.contracts
  }, "The exact contracts that were recommended"), (d.structures || {}).reason && /*#__PURE__*/React.createElement("div", {
    className: "inv-fv-why"
  }, d.structures.reason), Object.keys(structures).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: INV_FWD_TIP.contracts
  }, "Structure"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Recommended contracts that have reached their expiration."
  }, "Settled"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How many of those were the structure that won the comparison that day."
  }, "Was preferred"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The middle profit or loss per hundred shares of exposure."
  }, "Median result"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "The share of settled contracts that finished profitable."
  }, "Profitable"))), /*#__PURE__*/React.createElement("tbody", null, Object.keys(structures).map(k => {
    const s = structures[k] || {};
    return /*#__PURE__*/React.createElement("tr", {
      key: k,
      title: s.sufficient ? "" : s.reason
    }, /*#__PURE__*/React.createElement("td", null, k), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invCount(s.n)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, invCount(s.n_when_preferred)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, s.median_profit != null ? invMoney(s.median_profit, 0) : invNA, !s.sufficient && /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, " \xB7  ", s.verdict)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, s.win_rate_pct != null ? invPct(s.win_rate_pct, 0) : invNA));
  })))));
}

// ── Phase 7: is the prospective capture actually happening? ───────────────
//
// Everything the forward work rests on is collected going forward and can
// never be back-filled, so the only two questions worth a panel are how much
// there is and whether today added to it. Deliberately compact and behind an
// expander: this is operational, and it must not crowd the screen the buy or
// wait decision is made on.

const INV_CAPTURE_TIP = {
  what: "How much real, prospectively captured data this app holds, and " + "whether it is still being captured. None of it can be back-filled: " + "there is no source of historical option chains this app can reach, so " + "a trading day that goes uncaptured stays uncaptured. That is why a " + "missed day is reported here the next morning rather than discovered " + "months later in a backtest.",
  state: "HEALTHY means the last trading day captured everything expected. " + "PARTIAL means some of it was missed. CAPTURE FAILURE means a whole " + "trading day produced nothing, which is the state worth acting on. A " + "weekend or a market holiday is NOT EXPECTED rather than missed.",
  snapshots: "Days on which the whole valuation state was recorded for at " + "least one followed ticker. This is what forward validation scores " + "later, so a day without it is a day that can never be scored.",
  chains: "Days on which a real end-of-day option chain was captured. This " + "is the one that can never be recovered.",
  leaps: "Days on which the long-dated contracts around the money and their " + "implied volatility were recorded, which is what gives a long-dated " + "option its own volatility history instead of a borrowed one.",
  last: "The most recent trading day on which everything expected was " + "captured. If this is not the last trading day, something was missed.",
  coverage: "The share of trading days since capture began that have a real " + "chain behind them. Days before the first capture are not counted as " + "missed, because nothing was expected of them.",
  missing: "Followed tickers that were expected today and have not been " + "captured. Before the capture window this is simply everything; after " + "it, it is a list of failures.",
  forward: "Forward validation scores a recommendation only once its whole " + "horizon has passed. Nothing is scored early and no verdict is given " + "until enough observations have completed, so what is shown here is " + "when the first result can exist — not a result.",
  symbol: "Per ticker: how many days of each kind exist, when the real " + "chain history starts and ends, and which expected trading days have no " + "chain. Weekends and market holidays are not counted as missed."
};
const INV_CAPTURE_CLASS = {
  HEALTHY: "up",
  PARTIAL: "",
  "CAPTURE FAILURE": "down",
  COMPLETE: "up",
  MISSED: "down",
  "NOT EXPECTED": "muted"
};
function InvCaptureStat({
  label,
  value,
  tip
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "inv-stat-label",
    title: tip
  }, label), /*#__PURE__*/React.createElement("b", {
    className: "inv-stat-val",
    title: tip
  }, value));
}
function InvDataReadiness({
  apiFetch,
  symbol
}) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = React.useCallback(() => {
    setBusy(true);
    apiFetch("/api/invest/readiness").then(r => r.json()).then(j => setData(j)).catch(() => setData({
      error: true
    })).finally(() => setBusy(false));
  }, [apiFetch]);
  useEffect(() => {
    load();
  }, [load]);
  const d = data || {};
  const today = d.today || {};
  const health = d.health || {};
  const fwd = d.forward || {};
  const rows = d.symbol_rows || [];
  const mine = rows.filter(r => r.symbol === (symbol || "").toUpperCase());
  const show = mine.length ? mine.concat(rows.filter(r => !mine.includes(r))) : rows;
  if (d.error) {
    return /*#__PURE__*/React.createElement("div", {
      className: "inv-note",
      title: INV_CAPTURE_TIP.what
    }, "The capture-health report could not be read.");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "inv-bank"
  }, /*#__PURE__*/React.createElement("div", {
    className: `inv-note ${INV_CAPTURE_CLASS[health.state] || ""}`,
    title: INV_CAPTURE_TIP.state
  }, busy && !data ? "Loading…" : `${health.state || "—"} — ${health.reason || ""}`), !!health.alert && /*#__PURE__*/React.createElement("div", {
    className: "inv-note down",
    title: INV_CAPTURE_TIP.state
  }, health.alert), /*#__PURE__*/React.createElement("div", {
    className: "inv-grid"
  }, /*#__PURE__*/React.createElement(InvCaptureStat, {
    label: "Investment snapshots",
    tip: INV_CAPTURE_TIP.snapshots,
    value: `${d.investment_snapshot_days || 0} days`
  }), /*#__PURE__*/React.createElement(InvCaptureStat, {
    label: "Real option chains",
    tip: INV_CAPTURE_TIP.chains,
    value: `${d.real_chain_days || 0} days`
  }), /*#__PURE__*/React.createElement(InvCaptureStat, {
    label: "Long-dated observations",
    tip: INV_CAPTURE_TIP.leaps,
    value: `${d.leaps_observation_days || 0} days`
  }), /*#__PURE__*/React.createElement(InvCaptureStat, {
    label: "Last successful capture",
    tip: INV_CAPTURE_TIP.last,
    value: d.last_successful_capture ? invDate(d.last_successful_capture) : "None yet"
  }), /*#__PURE__*/React.createElement(InvCaptureStat, {
    label: "Today's capture status",
    tip: INV_CAPTURE_TIP.state,
    value: d.trading_day ? today.state || "—" : `Not expected — ${d.not_trading_because || "not a trading day"}`
  }), /*#__PURE__*/React.createElement(InvCaptureStat, {
    label: "Symbols missing today",
    tip: INV_CAPTURE_TIP.missing,
    value: (d.symbols_missing_today || []).length ? (d.symbols_missing_today || []).join(", ") : "None"
  }), /*#__PURE__*/React.createElement(InvCaptureStat, {
    label: "Real chain coverage",
    tip: INV_CAPTURE_TIP.coverage,
    value: d.chain_coverage_pct == null ? "Nothing captured yet" : invPct(d.chain_coverage_pct)
  }), /*#__PURE__*/React.createElement(InvCaptureStat, {
    label: "Earliest thirty-day validation result",
    tip: INV_CAPTURE_TIP.forward,
    value: (() => {
      const h = (fwd.horizons || []).find(x => x.days === 30);
      return h ? h.first_eligible_pretty : "No snapshot recorded yet";
    })()
  })), !!(fwd.horizons || []).length && /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.forward
  }, "Horizon"), /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.forward
  }, "Earliest possible result"), /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.forward
  }, "Still ageing toward it"), /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.forward
  }, "Horizon complete"))), /*#__PURE__*/React.createElement("tbody", null, (fwd.horizons || []).map(h => /*#__PURE__*/React.createElement("tr", {
    key: h.days
  }, /*#__PURE__*/React.createElement("td", null, h.days, " days"), /*#__PURE__*/React.createElement("td", null, h.first_eligible_pretty), /*#__PURE__*/React.createElement("td", null, h.ageing), /*#__PURE__*/React.createElement("td", null, h.complete))))), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_CAPTURE_TIP.forward
  }, fwd.reason), !!show.length && /*#__PURE__*/React.createElement("table", {
    className: "inv-peer-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.symbol
  }, "Ticker"), /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.snapshots
  }, "Snapshot days"), /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.chains
  }, "Chain days"), /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.chains
  }, "First chain"), /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.chains
  }, "Last chain"), /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.coverage
  }, "Real chain coverage"), /*#__PURE__*/React.createElement("th", {
    title: INV_CAPTURE_TIP.symbol
  }, "Expected days with no chain"))), /*#__PURE__*/React.createElement("tbody", null, show.slice(0, 40).map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.symbol
  }, /*#__PURE__*/React.createElement("td", null, r.symbol), /*#__PURE__*/React.createElement("td", null, r.snapshot_days), /*#__PURE__*/React.createElement("td", null, r.chain_days), /*#__PURE__*/React.createElement("td", null, r.first_chain ? invShortDate(r.first_chain) : "—"), /*#__PURE__*/React.createElement("td", null, r.last_chain ? invShortDate(r.last_chain) : "—"), /*#__PURE__*/React.createElement("td", null, r.chain_coverage_pct == null ? "—" : invPct(r.chain_coverage_pct)), /*#__PURE__*/React.createElement("td", {
    title: (r.missing_expected_days || []).length ? (r.missing_expected_days || []).map(invShortDate).join(", ") : INV_CAPTURE_TIP.symbol
  }, (r.missing_expected_days || []).length || "None"))))), /*#__PURE__*/React.createElement("div", {
    className: "inv-note",
    title: INV_CAPTURE_TIP.what
  }, d.backfill_note), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: load,
    disabled: busy,
    title: "Re-read the capture log."
  }, busy ? "Loading…" : "Refresh"));
}
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
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, head("symbol", "Ticker", "Ticker symbol."), head("price", "Price", "Current share price.", true), head("quality_label", "Quality", "How good the business is. Not blended with anything else."), head("growth_label", "Growth", "Revenue and earnings growth."), head("valuation_label", "Valuation", "Cheap or expensive against its own history and its peers. 100 means cheap."), head("headline_multiple", "What it costs", "The valuation measure that belongs to this kind of business: price to tangible book for a bank, price to funds from operations for a property trust, price to book for an insurer or a broker, price to earnings for everything else. Hover a row to see which one it is — they are not comparable across kinds.", true), head("revisions_label", "Revisions", "Whether analysts are raising or cutting."), head("value_trap_level", "Trap risk", "Whether the business is deteriorating."), head("fair_value_base", "Base fair value", "The highest-confidence method's value.", true), head("buy_zone", "Buy zone", "The price at which the shares are worth owning.", true), head("premium_to_buy_zone_pct", "To buy zone", "How far the price sits above (positive) or below (negative) the buy zone.", true), head("preferred_structure", "Preferred structure", "Which way of taking the position won on identical capital."), head("entry_verdict", "Verdict", "The Phase 3 entry answer."))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => {
    if (r.status !== "recorded") {
      return /*#__PURE__*/React.createElement("tr", {
        key: r.symbol,
        className: "inv-row-off"
      }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.symbol)), /*#__PURE__*/React.createElement("td", {
        colSpan: 12,
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
    }, invPrice(r.price)), /*#__PURE__*/React.createElement("td", null, r.quality_label || invNA), /*#__PURE__*/React.createElement("td", null, r.growth_label || invNA), /*#__PURE__*/React.createElement("td", null, r.valuation_label || invNA), /*#__PURE__*/React.createElement("td", {
      className: "scan-num",
      title: r.headline_multiple_label ? `${r.headline_multiple_label}${r.fair_value_model && r.fair_value_model !== "STANDARD" ? ` · valued by the ${INV_MODEL_NAME[r.fair_value_model] || r.fair_value_model.toLowerCase()} model` : ""}` : "No valuation multiple could be built for this company."
    }, r.headline_multiple == null ? invNA : invRatio(r.headline_multiple, 1)), /*#__PURE__*/React.createElement("td", null, r.revisions_label || invNA), /*#__PURE__*/React.createElement("td", {
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
    onChange: e => setSym(
    // Hyphens belong here. The SEC writes a class share as
    // BRK-B, and stripping the hyphen turned every lookup of
    // one into a search for a ticker that does not exist.
    e.target.value.toUpperCase().replace(/[^A-Z.\-]/g, "")),
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
  }, "read the filing")))), d.routing && d.routing.business_class && section("routing", "Which model this company gets, and why", "The industry code starts the answer and the filings finish it. Code 6211 holds Charles Schwab, Goldman Sachs and BlackRock; what separates them is the customer money on the balance sheet and what the revenue is made of.", /*#__PURE__*/React.createElement(InvRouting, {
    routing: d.routing,
    extraction: (d.profile || {}).extraction
  })), d.hybrid && d.hybrid.is_hybrid && section("hybrid", "More than one business at once", "A company that is two financial businesses gets no single fair value unless the two models agree about one. Which case this is, and what each model says, are both shown.", /*#__PURE__*/React.createElement(InvHybrid, {
    hybrid: d.hybrid
  })), d.cross_check && ((d.cross_check.checks || []).length > 0 || ((d.cross_check.measures || {}).rows || []).length > 0) && section("crosscheck", "Rebuilt against published", "Where a measure had to be rebuilt from the machine-readable filings and the company also prints it in a table of its own, the two are put side by side. A comparison is only made when the basis, the period and the window all match, and a disagreement lowers confidence rather than being resolved in favour of the nicer number.", /*#__PURE__*/React.createElement(InvCrossCheck, {
    cross: d.cross_check
  })), section("readiness", "Data readiness", "How much real, prospectively captured data this app holds, and whether today added to it. None of it can be back-filled, so a missed trading day is reported here the next morning rather than discovered months later in a backtest.", /*#__PURE__*/React.createElement(InvDataReadiness, {
    apiFetch: apiFetch,
    symbol: d.symbol
  })), d.bank && section("bank", "Bank measures", "What a lender is actually made of: tangible book value, the return earned on it, what its deposits cost and what its loan book is doing.", /*#__PURE__*/React.createElement(InvBank, {
    bank: d.bank
  })), d.reit && section("reit", "Property trust measures", "Funds from operations rather than reported earnings, the distribution it supports, and what the filings will not support at all.", /*#__PURE__*/React.createElement(InvReit, {
    reit: d.reit
  })), d.insurance && section("insurance", "Insurer measures", "What kind of insurance this is, what the underwriting is doing, whether the reserves set aside for earlier years are proving enough, and the book value all of it earns a return on.", /*#__PURE__*/React.createElement(InvInsurance, {
    insurance: d.insurance
  })), d.broker && section("broker", "Broker measures", "Whether this filer is a broker-dealer at all, where its revenue comes from, what it costs to earn, and the customer numbers the filings will not support.", /*#__PURE__*/React.createElement(InvBroker, {
    broker: d.broker
  })), section("fairvalue", "Fair value", "Bear, base and bull from methods that are never averaged, the confidence that comes from how far apart they are, and the price this analysis says the shares are worth.", /*#__PURE__*/React.createElement(InvFairValue, {
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
  })), section("coveredcall", "Covered call simulator", "Owning a hundred shares and selling calls against them repeatedly, through the real lifecycle, measured against simply owning them.", /*#__PURE__*/React.createElement(InvCoveredCall, {
    apiFetch: apiFetch,
    symbol: sym
  })), section("validation", "Forward validation", "How the recommendations this tab has already recorded actually turned out. Nothing is recomputed and nothing is rewritten.", /*#__PURE__*/React.createElement(InvValidation, {
    apiFetch: apiFetch
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
