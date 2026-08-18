(function () {
// tab-invest.jsx — LAZY CHUNK (v4.41), loaded on first Investment open.
//
// The long-horizon workstation. Four questions, in this order:
//   1. Is this a strong, profitable business?
//   2. Are revenue and earnings per share growing?
//   3. Is it cheap against its OWN fundamentals?
//   4. At what price would it be worth owning?
//
// Endpoints: GET /api/invest?symbol=X · /api/invest/history · /api/invest/config
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
  "INSUFFICIENT DATA": "mut"
};
const INV_VERDICT_TIP = {
  ATTRACTIVE: "Profitable, growing, and the earnings yield clears the 10-year " + "Treasury by the cushion set in the configuration. This is the tab saying " + "the four questions all answered yes — not a recommendation, and not a score.",
  WATCH: "Worth following. Either the valuation is close but not yet at the " + "cushion, or the valuation is fine and something in the business is not.",
  WAIT: "Nothing wrong with the business, but you are being asked to pay more " + "than this dashboard's yield rule accepts. The price and the earnings " + "figure that WOULD change the answer are both stated below.",
  AVOID: "A gate failed outright — the company is losing money, or revenue is " + "shrinking while analysts cut. The specific reason is listed.",
  "INSUFFICIENT DATA": "There is not enough on file to answer honestly. This is " + "a real answer, not an error: some filers report only once a year, some " + "report in a foreign currency, and some have too little history."
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
  tone,
  wide
}) {
  const missing = value === invNA || value == null;
  const fullTip = [tip, missing && reason ? `Not available: ${reason}` : null].filter(Boolean).join("\n\n");
  return /*#__PURE__*/React.createElement("div", {
    className: `inv-stat${wide ? " inv-stat-wide" : ""}`
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
  const d = data || {};
  const prov = d.provenance || {};
  const v = d.verdict || {};
  const md = d.metric_detail || {};
  const reasonOf = k => (md[k] || {}).reason || "";
  return /*#__PURE__*/React.createElement("div", {
    className: "card inv-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-title",
    title: "A long-horizon view of the business behind the ticker: what it earns, whether that is growing, and what you are being asked to pay for it. Everything here comes from the company's own filings with the SEC unless a line says otherwise."
  }, "Investment \xB7 is this business worth owning"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "Reported fundamentals from SEC EDGAR, priced against the live 10-year Treasury yield. No score, no rating \u2014 one of five words and the arithmetic behind it.")), /*#__PURE__*/React.createElement("div", {
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
    title: "Re-read the filings, the quote and the Treasury curve now."
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
    title: `SEC Central Index Key ${d.cik} — the
                  company's permanent identifier in EDGAR.`
  }, " \xB7 ", sym)), /*#__PURE__*/React.createElement(InvVerdictPill, {
    verdict: v.verdict
  })), /*#__PURE__*/React.createElement("ul", {
    className: "inv-reasons"
  }, (v.reasons || []).map((r, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, r))), (v.what_would_change || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "inv-change",
    title: "Every WAIT or AVOID says what would have to change. The price is simply the price at which the earnings yield reaches the threshold in the configuration; the earnings figure is the same sum solved the other way round."
  }, (v.what_would_change || []).map((c, i) => /*#__PURE__*/React.createElement("div", {
    key: i
  }, c))), d.profile && /*#__PURE__*/React.createElement(InvMoatTags, {
    tags: d.profile.moat_tags,
    profile: d.profile
  })), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: "What the company says it does, in its own words, taken from Item 1 of its most recent annual report. No language model is involved at any point \u2014 this is quoted, and the filing it came from is one click away."
  }, "The business"), d.profile ? /*#__PURE__*/React.createElement("div", {
    className: "inv-desc"
  }, d.profile.description, /*#__PURE__*/React.createElement("div", {
    className: "inv-src",
    title: `Quoted from Item 1, Business, of the
                annual report filed ${invDate(d.profile.as_of)}. Filings never
                change, so this is read once and kept.`
  }, d.profile.source, " \xB7 filed ", invShortDate(d.profile.as_of), d.profile.url && /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 ", /*#__PURE__*/React.createElement("a", {
    href: d.profile.url,
    target: "_blank",
    rel: "noopener noreferrer",
    title: "Open the annual report on EDGAR."
  }, "read the filing")))) : /*#__PURE__*/React.createElement("div", {
    className: "research-empty",
    title: "The business description is quoted from Item 1 of a 10-K. When no annual report can be read for this filer, nothing is substituted."
  }, "No annual report business section could be read for ", sym, "."), /*#__PURE__*/React.createElement("div", {
    className: "inv-sechead",
    title: "The numbers that answer the first three questions. Each carries its source, its as-of date and its basis; hover any of them."
  }, "Core snapshot"), /*#__PURE__*/React.createElement("div", {
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
    tip: "Share price multiplied by the share count on the cover page of the latest filing. Computed here from two sourced numbers rather than taken from a vendor, so both halves can be checked."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Price to earnings, trailing",
    value: invRatio(d.trailing_pe),
    prov: prov.fundamentals,
    basis: (md.eps || {}).basis,
    asOf: (md.eps || {}).period_end,
    reason: d.eps_ttm != null && d.eps_ttm <= 0 ? "Earnings are negative, and a negative price-to-earnings ratio is an arithmetic artifact rather than a cheap stock, so it is not shown." : reasonOf("eps"),
    tip: "Share price divided by the last twelve months of reported GAAP diluted earnings per share. Shown because everyone reads it; the earnings YIELD below is what the verdict actually uses, because it stays meaningful when earnings are small or negative."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Price to earnings, forward",
    value: invRatio(d.forward_pe),
    prov: prov.estimates,
    reason: d.estimates_available ? "" : (prov.estimates || {}).reason || d.estimates_reason,
    tip: "Share price divided by the analyst consensus estimate for this fiscal year. Analyst estimates are on an adjusted (non-GAAP) basis and are NEVER mixed with the GAAP trailing figures in the same ratio."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Revenue, last twelve months",
    value: invMoney(d.revenue_ttm, 0),
    prov: prov.fundamentals,
    basis: (md.revenue || {}).basis,
    asOf: (md.revenue || {}).period_end,
    reason: reasonOf("revenue"),
    tip: "Total revenue over the last four reported quarters, rebuilt from the company's own quarterly filings."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Revenue growth, year over year",
    value: invSignedPct(d.revenue_growth_pct),
    tone: d.revenue_growth_pct == null ? "" : d.revenue_growth_pct >= 0 ? "up" : "down",
    prov: prov.fundamentals,
    reason: d.revenue_growth_note || reasonOf("revenue"),
    tip: "This twelve-month revenue against the same twelve months a year earlier. When the year-ago figure was zero or negative, no percentage is shown \u2014 a percentage change from a loss is not a meaningful number, and the words describing what happened appear instead."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Earnings per share, last twelve months",
    value: invPrice(d.eps_ttm),
    prov: prov.fundamentals,
    basis: (md.eps || {}).basis,
    asOf: (md.eps || {}).period_end,
    reason: reasonOf("eps"),
    tone: d.eps_ttm == null ? "" : d.eps_ttm >= 0 ? "" : "down",
    tip: "Reported GAAP diluted earnings per share, summed over the last four quarters. This is what the company signed and filed, not an adjusted figure."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Earnings growth, year over year",
    value: invSignedPct(d.eps_growth_pct),
    tone: d.eps_growth_pct == null ? "" : d.eps_growth_pct >= 0 ? "up" : "down",
    prov: prov.fundamentals,
    reason: d.eps_growth_note || reasonOf("eps"),
    tip: "Trailing earnings per share against the same twelve months a year earlier, on the same GAAP basis."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "This year's earnings estimate",
    value: invPrice(d.eps_forward),
    prov: prov.estimates,
    reason: d.estimates_available ? "" : (prov.estimates || {}).reason || d.estimates_reason,
    tip: "What analysts currently expect the company to earn per share this fiscal year. Adjusted (non-GAAP) basis."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Next year's earnings estimate",
    value: invPrice(d.eps_next_year),
    prov: prov.estimates,
    reason: d.estimates_available ? "" : (prov.estimates || {}).reason || d.estimates_reason,
    tip: "What analysts expect for the following fiscal year."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Forward earnings growth",
    value: invSignedPct(d.forward_eps_growth_pct),
    tone: d.forward_eps_growth_pct == null ? "" : d.forward_eps_growth_pct >= 0 ? "up" : "down",
    prov: prov.estimates,
    reason: d.forward_eps_growth_note || (d.estimates_available ? "" : d.estimates_reason),
    tip: "Next year's estimate against this year's \u2014 both on the same analyst basis, so the comparison is like for like."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Estimate revisions, last 30 days",
    value: invSignedPct(d.estimate_change_30d_pct),
    tone: d.estimate_change_30d_pct == null ? "" : d.estimate_change_30d_pct >= 0 ? "up" : "down",
    prov: prov.estimates,
    reason: d.estimates_available ? "" : (prov.estimates || {}).reason || d.estimates_reason,
    tip: "The net share of covering analysts who RAISED this year's estimate rather than cut it, over the last 30 days. This is revision breadth, not a percentage change in the estimate itself: the free provider publishes revision counts, not an archive of past estimates, and this tab will not present one as the other."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Earnings yield",
    value: invPct(d.earnings_yield_pct),
    prov: prov.fundamentals,
    reason: reasonOf("eps"),
    tone: d.earnings_yield_pct == null || d.treasury_10y_pct == null ? "" : d.earnings_yield_pct >= d.treasury_10y_pct ? "up" : "down",
    tip: "Trailing earnings per share divided by the share price \u2014 the P/E turned upside down. This is the number the verdict compares against the Treasury yield, because a yield can be set beside a bond yield and a multiple cannot."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Free cash flow yield",
    value: invPct(d.fcf_yield_pct),
    prov: prov.fundamentals,
    basis: (d.free_cash_flow_detail || {}).basis,
    reason: (d.free_cash_flow_detail || {}).reason,
    tone: d.fcf_yield_pct == null || d.fcf_yield_pct >= 0 ? "" : "down",
    tip: "Cash from operations minus capital spending over the last twelve months, divided by the market value of the company. Cash is harder to shape than accounting earnings, which is why this sits next to the earnings yield rather than instead of it. Banks and some property companies do not report capital spending in a comparable way, and show N/A rather than a made-up number."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "10-year Treasury yield",
    value: invPct(d.treasury_10y_pct, 2),
    prov: prov.treasury_10y,
    tip: "The yield on a 10-year US Treasury note \u2014 what you can earn without owning a business at all. Every valuation judgement on this tab is made relative to it, from the same official daily curve the Treasuries tab draws."
  })), /*#__PURE__*/React.createElement("div", {
    className: "inv-subgrid"
  }, /*#__PURE__*/React.createElement(InvStat, {
    label: "Net profit margin",
    value: invPct(d.net_margin_pct),
    prov: prov.fundamentals,
    reason: reasonOf("net_income") || reasonOf("revenue"),
    tip: "What share of each dollar of revenue survives as profit, over the last twelve months."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Net income, last twelve months",
    value: invMoney(d.net_income_ttm, 0),
    prov: prov.fundamentals,
    reason: reasonOf("net_income"),
    tip: "Reported GAAP net income over the last four quarters."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Free cash flow, last twelve months",
    value: invMoney(d.free_cash_flow_ttm, 0),
    prov: prov.fundamentals,
    reason: (d.free_cash_flow_detail || {}).reason,
    tip: "Cash from operations minus capital spending. Quarterly cash-flow figures are reported year-to-date rather than per quarter, so each quarter is recovered by subtracting the previous one before the four are added up."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Shares outstanding",
    value: invCount(d.shares_outstanding),
    prov: prov.fundamentals,
    basis: (d.shares_detail || {}).basis,
    reason: (d.shares_detail || {}).reason,
    tip: "The share count from the cover page of the most recent filing. Companies with several share classes report it per class, which the SEC's machine-readable feed does not carry; those fall back to the average diluted count and say so."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Price to sales",
    value: invRatio(d.price_sales, 2),
    prov: prov.fundamentals,
    reason: reasonOf("revenue"),
    tip: "Market value divided by revenue. Shown because it is easy to compute and people look for it \u2014 it plays NO part in the verdict. A company can sell a great deal and never earn anything, and this ratio cannot tell the two apart."
  }), /*#__PURE__*/React.createElement(InvStat, {
    label: "Latest reported period",
    value: d.period_end ? invDate(d.period_end) : invNA,
    prov: prov.fundamentals,
    tip: "The end of the most recent quarter included in every trailing figure above, and the date its filing reached the SEC.",
    asOf: d.last_filed
  })), /*#__PURE__*/React.createElement(InvDrivers, {
    drivers: d.drivers
  }), /*#__PURE__*/React.createElement(InvHistoryChart, {
    history: d.history,
    years: years,
    onYears: setYears,
    symbol: sym
  }), /*#__PURE__*/React.createElement("div", {
    className: "inv-foot",
    title: `This tab stores one snapshot of these
numbers per day so that a future version can show how the valuation itself has
moved. ${d.stored_days || 0} day${d.stored_days === 1 ? "" : "s"} recorded so
far for ${sym}. Nothing is back-filled: a forward estimate that was not recorded
on the day it was made cannot be recovered later, and inventing one would be a
history that never happened.`
  }, d.stored_days || 0, " daily snapshot", d.stored_days === 1 ? "" : "s", " recorded for ", sym, " \xB7 configuration ", d.config_hash || "—")));
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  InvestTab: React.memo(InvestTab)
});
})();
