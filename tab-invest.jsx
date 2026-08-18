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
  ATTRACTIVE: "up", WATCH: "warn", WAIT: "warn", AVOID: "down",
  "INSUFFICIENT DATA": "mut", "SPECIALIZED MODEL REQUIRED": "mut",
};

const INV_VERDICT_TIP = {
  ATTRACTIVE: "Good business, growing, priced at the cheap end of its OWN " +
    "history and of its peer group, with analysts not cutting and no cluster of " +
    "deterioration signals. Not a recommendation and not a score — a set of " +
    "conditions you can re-check by hand from the numbers on this screen.",
  WATCH: "Worth following. Either the price is reasonable but not at the cheap " +
    "end, or the price is fine and something in the business is not.",
  WAIT: "Nothing wrong with the business — it is priced near the expensive end " +
    "of its own range. The price and the earnings figure that would move the " +
    "answer are both stated below, and they come from THIS company's own " +
    "median valuation rather than from a universal multiple.",
  AVOID: "A gate failed outright: the company is losing money, or several " +
    "deterioration signals are firing at once. Cheapness with that pattern is " +
    "the classic value trap, and this dashboard will not call it attractive.",
  "INSUFFICIENT DATA": "There is not enough on file to answer honestly. This is " +
    "a real answer, not an error: some filers report only once a year, some " +
    "report in a foreign currency, and some have too little history.",
  "SPECIALIZED MODEL REQUIRED": "This is a bank, insurer, broker or property " +
    "trust. The generic scorecard leans on return on invested capital, free " +
    "cash flow and net debt, none of which mean for these businesses what they " +
    "mean for an operating company. A model built for them is not written yet, " +
    "and a generic one dressed up as an answer would be worse than none.",
};

// The four vectors, in the order they are read.
const INV_DIMENSIONS = [
  ["quality", "Quality", "How good the business is: what it earns on the " +
    "capital it uses, how much of that profit turns into cash, whether margins " +
    "are widening, whether the share count is shrinking or being diluted away, " +
    "how much revenue goes out as stock to employees, and how levered it is. " +
    "Ranked against comparable companies where a peer group exists."],
  ["growth", "Growth", "Revenue growth, earnings growth and the forward " +
    "estimate, ranked against comparable companies. The margin and share-count " +
    "contributions come from the earnings breakdown further down rather than " +
    "being recomputed, so the same movement is never counted twice."],
  ["valuation", "Valuation", "Cheap or expensive — but against ITSELF and " +
    "against comparable businesses, never against a universal multiple. 100 " +
    "means cheap. A great company is allowed to trade at a high multiple; what " +
    "matters is whether it is high FOR THIS COMPANY."],
  ["revisions", "Revisions", "Whether analysts are raising or cutting their " +
    "numbers. Shows NOT RATED below four covering analysts, because three " +
    "people agreeing is three people agreeing, not a signal."],
];

const INV_TRAP_TONE = {
  "LOW RISK": "up", "MODERATE RISK": "warn", "HIGH RISK": "down",
  "NOT RATED": "mut",
};

const INV_TRAP_TIP = {
  "LOW RISK": "None of the deterioration signals are firing. Cheapness here " +
    "is not obviously the market pricing in decline.",
  "MODERATE RISK": "At least one thing is moving the wrong way. Worth reading " +
    "the list before treating a low valuation as an opportunity.",
  "HIGH RISK": "Several things are deteriorating at once. A stock that looks " +
    "cheap with this pattern is the classic value trap — the price fell " +
    "BECAUSE the business is getting worse, and this dashboard will not call " +
    "it attractive.",
  "NOT RATED": "Almost none of the deterioration signals could be measured for " +
    "this filer, so silence here is not evidence that nothing is wrong.",
};

const INV_CYCLE_TIP = {
  "PRE-EARNINGS": "Reports within the next two weeks. Trailing figures and " +
    "estimates can both move sharply on the day.",
  "POST-EARNINGS FRESH": "Reported recently, so the trailing figures and the " +
    "estimates are as current as they get.",
  "NORMAL": "Mid-cycle: the last report is digested and the next is not close.",
  "STALE": "The last report is old enough that the trailing figures describe a " +
    "quarter the business has already moved past.",
  "UNKNOWN": "No earnings dates are available for this ticker.",
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
  return `${sign}$${a.toLocaleString("en-US", { minimumFractionDigits: digits,
    maximumFractionDigits: digits })}`;
}
function invPrice(v) {
  return v == null || !isFinite(v) ? invNA
    : `$${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
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
  return Number.isNaN(d.getTime()) ? String(s)
    : d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}
function invShortDate(s) {
  if (!s) return invNA;
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s)
    : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
// 21 -> "21st". "21th percentile" undercuts every careful sentence near it.
function invOrdinal(v) {
  if (v == null || !isFinite(v)) return invNA;
  const n = Math.round(v);
  if (n % 100 >= 10 && n % 100 <= 20) return `${n}th`;
  return `${n}${({ 1: "st", 2: "nd", 3: "rd" })[n % 10] || "th"}`;
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

function InvSource({ prov, basis, asOf, reason }) {
  const p = prov || {};
  const source = p.source || null;
  const when = asOf || p.as_of;
  const theBasis = basis || p.basis;
  const stale = !!p.stale;
  const bits = [];
  if (source) bits.push(source);
  if (when) bits.push(`as of ${invShortDate(when)}`);
  const tip = [
    source ? `Source: ${source}` : "Source: not available",
    when ? `As of ${invDate(when)}` : null,
    theBasis ? `Basis: ${theBasis}` : null,
    stale ? `STALE — ${invAge(p.age_hours)}. ${p.reason || ""}` : null,
    reason || null,
  ].filter(Boolean).join("\n");
  if (!bits.length && !reason) return null;
  return (
    <div className={`inv-src${stale ? " inv-src-stale" : ""}`} title={tip}>
      {stale && <span className="inv-stale-flag" title={`The provider was
        unreachable, so this is the last value that was successfully
        recorded — ${invAge(p.age_hours)}.`}>STALE</span>}
      {bits.join(" · ") || reason}
    </div>
  );
}

// One statistic: label, value, provenance. `reason` is why it is N/A.
function InvStat({ label, value, tip, prov, basis, asOf, reason, tone }) {
  const missing = value === invNA || value == null;
  const fullTip = [tip, missing && reason ? `Not available: ${reason}` : null]
    .filter(Boolean).join("\n\n");
  return (
    <div className="inv-stat">
      <span className="inv-stat-label" title={fullTip}>{label}</span>
      <b className={`inv-stat-val${tone ? ` ${tone}` : ""}${missing ? " inv-na" : ""}`}
         title={fullTip}>{value}</b>
      <InvSource prov={prov} basis={basis} asOf={asOf}
                 reason={missing ? reason : ""} />
    </div>
  );
}

function InvVerdictPill({ verdict }) {
  const label = verdict || "INSUFFICIENT DATA";
  const tone = INV_VERDICT_TONE[label] || "mut";
  return (
    <span className={`inv-verdict inv-verdict-${tone}`} title={INV_VERDICT_TIP[label]}>
      {label}
    </span>
  );
}

function InvMoatTags({ tags, profile }) {
  if (!tags || !tags.length) return null;
  return (
    <div className="inv-moats">
      {tags.map((t) => (
        <span key={t} className="inv-moat" title={`A durable-advantage tag read
          from this company's own annual report, filed ${invDate(profile && profile.as_of)}.
          Tags are counted from the language in Item 1, Business — they are
          descriptions of what the company claims, never a score. There is
          deliberately no 1-to-10 moat rating: reading a filing for keywords
          cannot support that kind of precision.`}>{t}</span>
      ))}
    </div>
  );
}

// ── Earnings Drivers ──────────────────────────────────────────────────────

function InvDrivers({ drivers }) {
  const d = drivers || {};
  if (!d.available) {
    return (
      <div className="research-empty" title="The breakdown is only drawn when
        revenue, net income and the diluted share count are all on file for
        both periods. It is never approximated.">
        {d.reason || "No earnings breakdown available."}
      </div>
    );
  }
  const isLog = d.method === "log";
  const contribs = d.contributions || [];
  const scale = Math.max(...contribs.map((c) => Math.abs(c.value)), Math.abs(d.total), 1e-9);
  const unit = isLog
    ? (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)} pts`
    : (v) => `${v >= 0 ? "+" : "-"}$${Math.abs(v).toFixed(2)}`;
  const headTip = isLog
    ? `Earnings per share IS revenue times profit margin divided by the share
count. Taking logarithms turns that product into a sum, so the change in
earnings splits EXACTLY into three parts that add back to the total — this is
an identity, not an approximation. Read a bar as "this driver added this much
to the change"; a positive share-count bar means the count FELL, which lifts
earnings per share.`
    : `Earnings or margins went negative or crossed zero over this year, so the
logarithmic split does not exist and this tab will not fake one. Each bar is
that driver's Shapley value: its effect on earnings per share averaged over
every order in which the three drivers could have moved. Averaging over the
orderings is what removes the arbitrary choice of which driver gets credit for
the overlap. The bars add up exactly to the change in dollars per share.`;

  return (
    <div className="inv-drivers">
      <div className="inv-sechead" title={headTip}>
        {isLog ? "Earnings drivers" : "Dollar EPS Bridge"}
        <span className="muted"> · what moved earnings per share over the last year</span>
      </div>
      <div className="inv-drivers-sum" title={`Trailing twelve months ending
        ${invDate(d.period_end)}, against the twelve months ending
        ${invDate(d.prior_period_end)}.`}>
        {invPrice(d.eps_prior)} <span className="inv-arrow">→</span> {invPrice(d.eps_current)}
        <span className="muted"> · a change of {isLog
          ? `${d.total >= 0 ? "+" : ""}${d.total.toFixed(1)} log points`
          : `${d.total >= 0 ? "+" : "-"}$${Math.abs(d.total).toFixed(2)} per share`}</span>
      </div>
      <div className="inv-bars">
        {contribs.map((c) => {
          const pctW = Math.min(100, (Math.abs(c.value) / scale) * 100);
          const up = c.value >= 0;
          return (
            <div className="inv-bar-row" key={c.driver}>
              <span className="inv-bar-label" title={INV_DRIVER_TIP[c.driver] || c.driver}>
                {c.driver}
              </span>
              <span className="inv-bar-track">
                <i className={up ? "up" : "down"} style={{ width: `${pctW}%` }} />
              </span>
              <b className={up ? "up" : "down"}>{unit(c.value)}</b>
            </div>
          );
        })}
        <div className="inv-bar-row inv-bar-total">
          <span className="inv-bar-label" title="The three contributions above add
            up to exactly this number. If they ever did not, the panel would not
            be drawn — the reconciliation is asserted in the test suite.">Total</span>
          <span className="inv-bar-track" />
          <b className={d.total >= 0 ? "up" : "down"}>{unit(d.total)}</b>
        </div>
      </div>
      {d.warning && (
        <div className="inv-warn" title="The breakdown describes net income
          divided by diluted shares, because that is the only quantity the
          revenue-margin-shares identity can equal. Where a company's reported
          earnings per share differs from it, this says so rather than quietly
          bridging to a different number than the one at the top of the tab.">
          {d.warning}
        </div>
      )}
      <div className="inv-note" title={headTip}>{d.note}</div>
    </div>
  );
}

const INV_DRIVER_TIP = {
  "Revenue": "How much of the change in earnings per share came from selling more (or less).",
  "Profit margin": "How much came from keeping more (or less) of each dollar of revenue as profit.",
  "Share count": "How much came from the share count changing. Buybacks shrink the count and lift earnings per share; issuing shares does the opposite. A positive bar means the count fell.",
  "Net income": "How much of the change came from the profit or loss itself.",
};

// ── Price vs earnings chart ───────────────────────────────────────────────

function InvHistoryChart({ history, years, onYears, symbol }) {
  const h = history || {};
  const series = [
    { key: "price", label: "Share price", cls: "inv-line-price", pts: h.price || [] },
    { key: "eps_ttm", label: "Earnings per share (trailing)", cls: "inv-line-eps", pts: h.eps_ttm || [] },
    { key: "eps_forward", label: "Forward earnings estimate", cls: "inv-line-fwd", pts: h.eps_forward || [] },
  ].filter((s) => s.pts.length > 1);

  const W = 860, H = 260, L = 44, R = 12, T = 12, B = 26;
  let body = null;
  if (!series.length) {
    body = <div className="research-empty">Not enough history to draw this chart yet.</div>;
  } else {
    const all = series.flatMap((s) => s.pts);
    const times = all.map((p) => new Date(`${p.date}T12:00:00`).getTime());
    const t0 = Math.min(...times), t1 = Math.max(...times);
    const vals = all.map((p) => p.indexed);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.08, 4);
    const yLo = lo - pad, yHi = hi + pad;
    const x = (d) => L + ((new Date(`${d}T12:00:00`).getTime() - t0) / Math.max(t1 - t0, 1)) * (W - L - R);
    const y = (v) => T + (1 - (v - yLo) / Math.max(yHi - yLo, 1e-9)) * (H - T - B);
    const ticks = [yLo, (yLo + yHi) / 2, yHi];
    body = (
      <svg viewBox={`0 0 ${W} ${H}`} className="inv-chart" role="img"
           aria-label={`${symbol} share price against earnings per share, both indexed to 100`}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="inv-grid" />
            <text x={L - 6} y={y(v) + 3} className="inv-axis" textAnchor="end">{Math.round(v)}</text>
          </g>
        ))}
        <line x1={L} x2={W - R} y1={y(100)} y2={y(100)} className="inv-base" />
        {series.map((s) => (
          <path key={s.key} className={s.cls} d={s.pts.map((p, i) =>
            `${i ? "L" : "M"}${x(p.date).toFixed(1)},${y(p.indexed).toFixed(1)}`).join(" ")} />
        ))}
        <text x={L} y={H - 6} className="inv-axis">{invShortDate(h.start)}</text>
        <text x={W - R} y={H - 6} className="inv-axis" textAnchor="end">today</text>
      </svg>
    );
  }

  return (
    <div className="inv-histwrap">
      <div className="inv-sechead" title={`Both lines start at 100 on the first
day of the window, so their SHAPES can be compared regardless of scale. When
the price line pulls away from the earnings line, you are paying more per
dollar of earnings than you were; when earnings outrun the price, you are
paying less.

Reported earnings are plotted on the day they were FILED, not the day the
quarter ended — a quarter that ended in March was not public until May, and
showing it in March would put information on the chart that nobody had.

Per-share figures use each filing's most recent restatement, which is the same
share basis split-adjusted prices use, so a stock split does not create a step
in either line.`}>
        Price against earnings
        <span className="muted"> · both indexed to 100 at the start</span>
        <span className="inv-yearsel">
          {[3, 5].map((n) => (
            <button key={n} className={`inv-yearbtn${years === n ? " on" : ""}`}
                    onClick={() => onYears(n)}
                    title={`Show the last ${n} years.`}>{n}Y</button>
          ))}
        </span>
      </div>
      <div className="inv-legend">
        <span className="inv-key inv-key-price" title="Share price, split-adjusted, indexed to 100.">Share price</span>
        <span className="inv-key inv-key-eps" title="Trailing twelve-month earnings per share as reported to the SEC, indexed to 100, each point placed on its filing date.">Earnings per share</span>
        <span className="inv-key inv-key-fwd" title="The analyst forward estimate, recorded by this dashboard once a day. It starts on the first day one was recorded and is never back-filled — no free archive of past consensus exists, and inventing one would be a fabricated history.">Forward estimate</span>
      </div>
      {body}
      {(h.notes || []).map((n, i) => (
        <div className="inv-note" key={i}>{n}</div>
      ))}
    </div>
  );
}

// ── the four-vector header ────────────────────────────────────────────────

function InvScoreTile({ dimKey, label, tip, block, onOpen }) {
  const b = block || {};
  const score = b.score;
  const has = score != null && isFinite(score);
  const tone = !has ? "mut" : score >= 60 ? "up" : score >= 40 ? "warn" : "down";
  const detail = [
    tip,
    b.coverage ? `Built from ${b.coverage}.` : null,
    b.peer_ranked ? "Ranked against comparable companies."
      : (has ? "Scored on absolute bands — too few comparable companies to rank against." : null),
    !has && b.reason ? `Not rated: ${b.reason}` : null,
  ].filter(Boolean).join("\n\n");
  return (
    <button className={`inv-tile inv-tile-${tone}`} title={detail}
            onClick={() => onOpen && onOpen(dimKey)}>
      <span className="inv-tile-label">{label}</span>
      <b className="inv-tile-value">{b.label || "NOT RATED"}</b>
      <span className="inv-tile-bar" aria-hidden="true">
        <i style={{ width: `${has ? Math.max(2, Math.min(100, score)) : 0}%` }} />
      </span>
      <span className="inv-tile-num">{has ? `${Math.round(score)} / 100` : "—"}</span>
    </button>
  );
}

function InvTrapTile({ trap, onOpen }) {
  const t = trap || {};
  const level = t.level || "NOT RATED";
  const tone = INV_TRAP_TONE[level] || "mut";
  const detail = [INV_TRAP_TIP[level],
    t.n_active ? `${t.n_active} signal${t.n_active === 1 ? "" : "s"} firing.` : null,
    (t.unknown || []).length ? `${t.unknown.length} could not be measured.` : null,
    t.reason || null].filter(Boolean).join("\n\n");
  return (
    <button className={`inv-tile inv-tile-${tone}`} title={detail}
            onClick={() => onOpen && onOpen("trap")}>
      <span className="inv-tile-label">Value trap risk</span>
      <b className="inv-tile-value">{level}</b>
      <span className="inv-tile-sub">
        {(t.active || []).length
          ? `${t.active.length} signal${t.active.length === 1 ? "" : "s"} firing`
          : "no deterioration signals firing"}
      </span>
    </button>
  );
}

function InvCyclePill({ cycle }) {
  const c = cycle || {};
  const state = c.state || "UNKNOWN";
  return (
    <span className="inv-cycle" title={`${INV_CYCLE_TIP[state] || ""}${c.reason ? "\n\n" + c.reason : ""}`}>
      {state}
    </span>
  );
}

// ── valuation against its own history ─────────────────────────────────────

function InvRangeBar({ dist, cheapHigh, fmt }) {
  const d = dist || {};
  if (!d.available) return null;
  const lo = d.min, hi = d.max;
  const span = (hi - lo) || 1;
  const at = (v) => `${Math.max(0, Math.min(100, ((v - lo) / span) * 100))}%`;
  return (
    <div className="inv-range" title={`The bar spans everything this measure has been over the window: ${fmt(lo)} at the low end to ${fmt(hi)} at the high. The marks are the 10th percentile, the median and the 90th; the pin is where it stands today.`}>
      <span className="inv-range-track">
        <i className="inv-range-fill"
           style={{ left: at(d.p10), width: `calc(${at(d.p90)} - ${at(d.p10)})` }} />
        <em className="inv-range-median" style={{ left: at(d.median) }} />
        <b className="inv-range-now" style={{ left: at(d.current) }} />
      </span>
      <span className="inv-range-ends">
        <span>{fmt(lo)}</span><span>{fmt(hi)}</span>
      </span>
    </div>
  );
}

function InvValuationHistory({ vh, valuation, symbol }) {
  const v = vh || {};
  const [win, setWin] = useState("5y");
  if (!v.available) {
    return (
      <div className="research-empty" title="A valuation history needs a daily
        price for every day it measures and reported figures lined up against
        those days. Where either is missing it is left out rather than filled in.">
        {v.reason || `No valuation history could be built for ${symbol}.`}
      </div>
    );
  }
  const dists = v.distributions || {};
  const regime = v.regime || {};
  const measures = [
    ["earnings_yield_pct", (x) => x == null ? invNA : `${x.toFixed(1)}%`],
    ["fcf_yield_pct", (x) => x == null ? invNA : `${x.toFixed(1)}%`],
    ["trailing_pe", (x) => x == null ? invNA : `${x.toFixed(1)}×`],
  ];
  return (
    <div className="inv-vhist">
      <div className="inv-sechead" title={`Where this company is valued today
against where it has been valued before, using only figures that were public
on each of those days. Reported earnings enter the history on the day they were
FILED, not the day the quarter ended, so nothing on this chart was unknowable
at the time. Prices and per-share figures both come from split-restated
sources, so a stock split leaves no step.

This is the answer to "cheap compared with itself" — the question a universal
price/earnings threshold cannot ask.`}>
        Valuation against its own history
        <span className="muted"> · {v.n_days} trading days, {invShortDate(v.from)} to {invShortDate(v.to)}</span>
        <span className="inv-yearsel">
          {["3y", "5y"].map((w) => (
            <button key={w} className={`inv-yearbtn${win === w ? " on" : ""}`}
                    onClick={() => setWin(w)}
                    title={`Measure against the last ${w === "3y" ? "three" : "five"} years.`}>
              {w.toUpperCase()}
            </button>
          ))}
        </span>
      </div>

      {regime.shifted && (
        <div className="inv-warn" title={`The typical level over the last
${regime.recent_years} years is ${regime.recent_median != null ? regime.recent_median.toFixed(1) : "—"},
against ${regime.earlier_median != null ? regime.earlier_median.toFixed(1) : "—"} over the period before it — a move of
${regime.shift != null ? regime.shift.toFixed(1) : "—"}, which is larger than the earlier period's own
10th-to-90th-percentile spread of ${regime.earlier_spread != null ? regime.earlier_spread.toFixed(1) : "—"}. That is a change of
level rather than a wiggle, so the older half of this history describes a
different market for this stock and the verdict leans on it less.`}>
          <b>REGIME SHIFT DETECTED</b> — the level this stock is valued at has
          moved. The older part of the history below is a weaker guide than
          usual, and the verdict reduces its reliance on it.
        </div>
      )}

      {measures.map(([key, fmt]) => {
        const block = dists[key] || {};
        const d = block[win] || {};
        return (
          <div className="inv-vrow" key={key}>
            <div className="inv-vrow-head">
              <span className="inv-vrow-label"
                    title={key === "earnings_yield_pct"
                      ? "Trailing earnings per share divided by the share price. Higher is cheaper. This is the measure the verdict uses, because a yield can be placed beside a bond yield and a multiple cannot."
                      : key === "fcf_yield_pct"
                        ? "Free cash flow divided by the market value of the company, on each day's price and each day's known share count. Higher is cheaper."
                        : "Share price divided by trailing earnings. Lower is cheaper. Shown because everyone reads it."}>
                {block.label || key}
              </span>
              <b className={`inv-vrow-now${d.cheap_percentile == null ? "" :
                d.cheap_percentile >= 60 ? " up" : d.cheap_percentile <= 40 ? " down" : ""}`}>
                {fmt(block.current)}
              </b>
              <span className="inv-vrow-pct"
                    title={d.cheap_percentile == null ? "" :
                      `Today sits at the ${invOrdinal(d.cheap_percentile)} percentile of this company's own ${win === "3y" ? "three" : "five"}-year range, measured so that 100 always means cheap. ${d.n} observations.`}>
                {d.cheap_percentile == null ? invNA
                  : `${invOrdinal(d.cheap_percentile)} pct`}
              </span>
            </div>
            {d.available ? (
              <>
                <InvRangeBar dist={{ ...d, current: block.current }}
                             cheapHigh={block.cheap_when_high} fmt={fmt} />
                <div className="inv-vrow-stats">
                  <span title="The 10th percentile — cheaper than this only one day in ten.">10th {fmt(d.p10)}</span>
                  <span title="The middle of the range over this window.">Median {fmt(d.median)}</span>
                  <span title="The 90th percentile — more expensive than this only one day in ten.">90th {fmt(d.p90)}</span>
                  <span className="muted" title="How many trading days went into this range.">{d.n} days</span>
                </div>
              </>
            ) : (
              <div className="inv-note">{d.reason || block.reason || "Not enough history in this window."}</div>
            )}
          </div>
        );
      })}
      <div className="inv-note" title="Forward price/earnings is deliberately
        absent from this panel. There is no free archive of what analysts
        expected on a past date, so a historical forward multiple would have to
        be invented. It stays a current-only figure until this dashboard's own
        daily snapshots have accumulated enough real history.">
        Forward price to earnings is not shown here on purpose: no free record
        of past analyst expectations exists, so a historical forward multiple
        would have to be fabricated. It becomes available once this dashboard's
        own daily snapshots have accumulated enough of their own history.
      </div>
    </div>
  );
}

// ── quality detail ────────────────────────────────────────────────────────

const INV_QUALITY_TIP = {
  "Return on invested capital": "Operating profit after tax, divided by the " +
    "equity and net debt funding the business. The single best one-number read " +
    "on whether a company turns capital into profit.",
  "Free cash flow conversion": "How much of reported profit actually shows up " +
    "as cash. Persistently below 100% means the earnings are being consumed by " +
    "working capital or capital spending.",
  "Operating margin trend": "Points of operating margin gained or lost per " +
    "year, fitted across the reported history. A snapshot cannot tell a " +
    "business holding a steady margin from one grinding downward at the same level.",
  "Share count trend": "How fast the share count is changing per year. " +
    "Negative is buybacks, positive is dilution — every share issued is a " +
    "slice of your claim on the same earnings.",
  "Stock compensation as a share of revenue": "Pay settled in stock rather " +
    "than cash. It does not appear in cash flow but it is a real cost, borne " +
    "by shareholders through dilution.",
  "Net debt to operating profit": "Borrowings, less cash, against operating " +
    "profit before depreciation. How many years of earnings the debt represents.",
};

function InvQuality({ quality }) {
  const q = quality || {};
  const comps = q.components || [];
  const fmt = (key, v) => {
    if (v == null) return invNA;
    if (key === "leverage") return `${v.toFixed(1)}×`;
    if (key === "operating_margin_trend" || key === "share_count_trend")
      return `${v >= 0 ? "+" : ""}${v.toFixed(1)} pts/yr`;
    return `${v.toFixed(1)}%`;
  };
  return (
    <div className="inv-quality">
      <div className="inv-sechead" title={`Six inputs chosen to be
non-duplicative, each scored independently and each explaining its own result.
Coverage varies genuinely by filer and industry — Microsoft tags no combined
depreciation figure at all, and several large companies do not report operating
income in machine-readable form. A missing input is a missing input, never a
failing grade and never a zero.`}>
        Quality
        <span className="muted"> · {q.coverage || ""}{q.peer_ranked
          ? " · ranked against comparable companies"
          : " · scored on absolute bands"}</span>
      </div>
      {comps.map((c) => (
        <div className="inv-qrow" key={c.key}>
          <span className="inv-qrow-label" title={INV_QUALITY_TIP[c.label] || c.label}>
            {c.label}
          </span>
          <b className={`inv-qrow-val${c.value == null ? " inv-na" : ""}`}
             title={c.reason || c.scored_against || ""}>
            {c.reason === "SPECIALIZED MODEL REQUIRED"
              ? "N/A for this business type" : fmt(c.key, c.value)}
          </b>
          <span className="inv-qrow-bar" aria-hidden="true">
            {c.score != null && (
              <i className={c.score >= 60 ? "up" : c.score >= 40 ? "warn" : "down"}
                 style={{ width: `${Math.max(2, Math.min(100, c.score))}%` }} />
            )}
          </span>
          <span className="inv-qrow-score"
                title={c.score == null ? (c.reason || "Not scored.")
                  : `${Math.round(c.score)} of 100 — ${c.scored_against || ""}`}>
            {c.score == null ? "—" : Math.round(c.score)}
          </span>
        </div>
      ))}
      {q.reason && <div className="inv-note">{q.reason}</div>}
    </div>
  );
}

// ── earnings and revisions ────────────────────────────────────────────────

function InvRevisions({ revisions, snap }) {
  const r = revisions || {};
  return (
    <div className="inv-revisions">
      <div className="inv-sechead" title={`What analysts expect, and whether
they are raising or cutting. Below four covering analysts this shows NOT RATED
rather than a number: three analysts agreeing is three people agreeing.

These estimates are on the analysts' ADJUSTED basis. Every trailing figure
elsewhere on this tab is GAAP, exactly as filed with the SEC. The two are shown
side by side and never combined inside one ratio.`}>
        Earnings and revisions
        <span className="muted"> · {r.label || "NOT RATED"}
          {r.analyst_count ? ` · ${Math.round(r.analyst_count)} analysts` : ""}</span>
      </div>
      <div className="inv-subgrid">
        <InvStat label="This year's earnings estimate" value={invPrice(r.current_year_eps)}
                 reason={r.reason} basis={r.basis}
                 tip="Analyst consensus for the current fiscal year, adjusted basis." />
        <InvStat label="Next year's earnings estimate" value={invPrice(r.next_year_eps)}
                 reason={r.reason} basis={r.basis}
                 tip="Analyst consensus for the following fiscal year." />
        <InvStat label="Forward earnings growth" value={invSignedPct(r.forward_growth_pct)}
                 tone={r.forward_growth_pct == null ? "" : (r.forward_growth_pct >= 0 ? "up" : "down")}
                 reason={r.reason}
                 tip="Next year's estimate against this year's, both on the same analyst basis." />
        <InvStat label="Revisions, last 30 days" value={invSignedPct(r.change_30d)}
                 tone={r.change_30d == null ? "" : (r.change_30d >= 0 ? "up" : "down")}
                 reason={r.reason} basis={r.change_basis}
                 tip="Net share of covering analysts who RAISED rather than cut in the last 30 days. This is revision breadth, not a percentage change in the estimate itself." />
        <InvStat label="Revisions, last 90 days" value={invSignedPct(r.change_90d)}
                 tone={r.change_90d == null ? "" : (r.change_90d >= 0 ? "up" : "down")}
                 reason={r.reason} basis={r.change_basis}
                 tip="The same measure over ninety days." />
        <InvStat label="Analyst coverage" value={r.analyst_count == null ? invNA : `${Math.round(r.analyst_count)} analysts`}
                 reason={r.reason}
                 tip="How many analysts publish an estimate. Below four, the revision rating is withheld." />
        <InvStat label="Analysts raising" value={r.up == null ? invNA : Math.round(r.up)}
                 reason={r.reason} tip="Analysts who raised their estimate in the window." />
        <InvStat label="Analysts cutting" value={r.down == null ? invNA : Math.round(r.down)}
                 reason={r.reason} tip="Analysts who cut their estimate in the window." />
      </div>
      <div className="inv-note">{r.gaap_note}</div>
    </div>
  );
}

// ── peers ─────────────────────────────────────────────────────────────────

function InvPeers({ peers, symbol, snap }) {
  const p = peers || {};
  const rows = p.rows || [];
  const val = p.valuation || {};
  if (p.status === "building") {
    return (
      <div className="research-empty" title="Each peer's filings are downloaded
        once and then cached, so this is slow only the first time a ticker is
        opened.">
        Building the peer group for {symbol} — each member's filings are read
        once and then cached. Refresh in a few seconds.
      </div>
    );
  }
  return (
    <div className="inv-peers">
      <div className="inv-sechead" title={`Comparable companies, chosen by the
industry code the SEC itself assigns to every filer. Most specific first:
a curated list, then the same four-digit code, then the three-digit industry
group, then the two-digit sector. A group needs at least five members to be a
distribution rather than a handful of companies; below that it falls back a
level and says so here.

A broad-benchmark group is shown for context but is NOT used to rank this
company's valuation — ranking a bank's earnings yield against a software
company's is arithmetic, not comparison.`}>
        Peers
        <span className="muted"> · {p.level || "none"}
          {p.sic ? ` · industry code ${p.sic}${p.sic_description ? ` (${p.sic_description})` : ""}` : ""}
          {p.curated ? " · curated list" : ""}</span>
      </div>
      {p.reason && <div className="inv-note">{p.reason}</div>}
      {val.available && (
        <div className="inv-subgrid">
          <InvStat label="Group price to earnings, aggregate"
                   value={invRatio(val.aggregate_pe)} basis={val.basis}
                   tip="Total market value of the profitable members divided by their total earnings — what an index-level multiple actually means. NEVER an average of the members' ratios: one member earning almost nothing produces a ratio in the hundreds and drags an average somewhere no member is." />
          <InvStat label="Median member price to earnings"
                   value={invRatio(val.median_member_pe)}
                   tip="The middle member's ratio, shown beside the aggregate so a group carried by one enormous constituent is visible." />
          <InvStat label="This company" value={invRatio(snap && snap.trailing_pe)}
                   tip="This company's own trailing price to earnings, for comparison against the two figures beside it." />
          <InvStat label="Members priced"
                   value={`${val.n_profitable} of ${val.n}`}
                   reason={val.n_excluded ? `${val.n_excluded} excluded as loss-making: ${(val.excluded || []).join(", ")}` : ""}
                   tip="How many members had positive earnings and so could enter the aggregate multiple. Loss-makers are excluded and named — a group where half the members lose money is a different group." />
        </div>
      )}
      {rows.length > 0 && (
        <div className="scan-table-wrap">
          <table className="inv-peer-table">
            <thead>
              <tr>
                <th title="Ticker.">Ticker</th>
                <th title="Company name as registered with the SEC.">Company</th>
                <th className="scan-num" title="Share price times shares outstanding.">Market value</th>
                <th className="scan-num" title="Share price divided by trailing GAAP earnings. Blank where earnings are negative.">Price / earnings</th>
                <th className="scan-num" title="Trailing earnings divided by price. Higher is cheaper.">Earnings yield</th>
                <th className="scan-num" title="Free cash flow divided by market value.">Cash flow yield</th>
                <th className="scan-num" title="Operating profit as a share of revenue.">Operating margin</th>
                <th className="scan-num" title="Revenue against the same twelve months a year earlier.">Revenue growth</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className={r.symbol === symbol ? "inv-peer-self" : ""}>
                  <td><b>{r.symbol}</b></td>
                  <td className="inv-peer-name" title={r.name}>{r.name}</td>
                  <td className="scan-num">{invMoney(r.market_cap, 0)}</td>
                  <td className="scan-num">{invRatio(r.trailing_pe)}</td>
                  <td className="scan-num">{invPct(r.earnings_yield_pct)}</td>
                  <td className="scan-num">{invPct(r.fcf_yield_pct)}</td>
                  <td className="scan-num">{invPct(r.operating_margin_pct)}</td>
                  <td className={`scan-num ${(r.revenue_growth_pct ?? 0) >= 0 ? "up" : "down"}`}>
                    {invSignedPct(r.revenue_growth_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── value trap ────────────────────────────────────────────────────────────

function InvValueTrap({ trap }) {
  const t = trap || {};
  return (
    <div className="inv-trap">
      <div className="inv-sechead" title={`Cheapness is not evidence of value —
it is the question. These signals ask whether the business is deteriorating,
because a stock reaching the cheap end of its own range BECAUSE things are
getting worse is the classic value trap.

Each signal fires on a direction of travel rather than on a level: the level
that counts as bad differs by industry, and the level is already scored under
Quality. A signal that cannot be measured is listed as such, never counted as
"fine".`}>
        Value trap check
        <span className={`inv-traplevel inv-traplevel-${INV_TRAP_TONE[t.level] || "mut"}`}>
          {t.level || "NOT RATED"}
        </span>
      </div>
      {t.reason && <div className="inv-note">{t.reason}</div>}
      <div className="inv-traplist">
        {(t.active || []).map((a) => (
          <div className="inv-trapsig inv-trapsig-on" key={a.key} title={a.detail}>
            <span className="inv-trapsig-tag">firing</span>
            <b>{a.label}</b>{a.detail ? <span> — {a.detail}</span> : null}
          </div>
        ))}
        {(t.inactive || []).map((a) => (
          <div className="inv-trapsig inv-trapsig-off" key={a.key} title={a.detail}>
            <span className="inv-trapsig-tag">not firing</span>
            {a.label}{a.detail ? <span className="muted"> — {a.detail}</span> : null}
          </div>
        ))}
        {(t.unknown || []).map((a) => (
          <div className="inv-trapsig inv-trapsig-unknown" key={a.key}
               title="This signal could not be measured from what this company reports. That is not the same as the signal being clear.">
            <span className="inv-trapsig-tag">unknown</span>
            {a.label} <span className="muted">— could not be measured</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── drawdowns ─────────────────────────────────────────────────────────────

function InvDrawdowns({ dd }) {
  const d = dd || {};
  if (!d.available) {
    return <div className="inv-note">{d.reason || "No drawdown history."}</div>;
  }
  const row = (label, w, tip) => w && (
    <div className="inv-ddrow" key={label}>
      <span className="inv-ddrow-label" title={tip}>{label}</span>
      <b className="down">{w.pct.toFixed(1)}%</b>
      <span className="muted">
        {invShortDate(w.peak_date)} → {invShortDate(w.trough_date)}
      </span>
    </div>
  );
  return (
    <div className="inv-dd">
      {row("Worst fall over the window", d.max,
        "The largest peak-to-trough fall anywhere in the price history available.")}
      {row("Worst fall in the last year", d.recent,
        "The largest peak-to-trough fall inside the last twelve months.")}
      {(d.windows || []).map((w) => row(w.label, w,
        "How this stock behaved through that period, if the history reaches back that far."))}
      <div className="inv-note" title="Beta is deliberately absent. It compresses
        a whole distribution into one number that says nothing about what this
        stock actually did when things went wrong — which is what these rows show.">
        Context only. There is no Beta here on purpose: what this stock actually
        did in a fall is more use than a single number summarising how it
        usually moves.
      </div>
    </div>
  );
}

// ── the tab ───────────────────────────────────────────────────────────────

function InvestTab({ apiFetch, ticker, onOpenTicker }) {
  const [sym, setSym] = useState(() => (ticker || "AAPL").toUpperCase());
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [years, setYears] = useState(3);
  // A set, not a single key: an accordion where opening one section closes
  // another fights the reader, and a controlled `open` prop on <details>
  // conflicts with the browser toggling it itself.
  const [open, setOpen] = useState(() => new Set(["valuation"]));
  const toggle = (key, want) => setOpen((prev) => {
    const next = new Set(prev);
    if (want === undefined ? next.has(key) : !want) next.delete(key);
    else next.add(key);
    return next;
  });

  useEffect(() => {
    if (ticker && ticker.toUpperCase() !== sym) setSym(ticker.toUpperCase());
  }, [ticker]);

  // useCallback is not among the hooks app-lib publishes on window, so it is
  // reached through React itself — same as app.jsx does.
  const load = React.useCallback((symbol, yrs, force) => {
    if (!symbol) return;
    setBusy(true); setErr(null);
    apiFetch(`/api/invest?symbol=${encodeURIComponent(symbol)}&years=${yrs}${force ? "&force=1" : ""}`)
      .then((r) => r.json())
      .then((j) => { if (j && j.error) { setErr(j.error); setData(null); } else { setData(j); } })
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setBusy(false));
  }, [apiFetch]);

  useEffect(() => { load(sym, years, false); }, [sym, years, load]);

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
  const reasonOf = (k) => (md[k] || {}).reason || "";
  const section = (key, label, tip, node) => (
    <details className="inv-exp" key={key} open={open.has(key)}
             onToggle={(e) => toggle(key, e.target.open)}>
      <summary title={tip}>{label}</summary>
      <div className="inv-exp-body">{node}</div>
    </details>
  );

  return (
    <div className="card inv-card">
      <div className="card-head">
        <div>
          <div className="card-title" title="A long-horizon view of the business
            behind the ticker: how good it is, whether it is growing, what you
            are being asked to pay for it against its own history and against
            comparable businesses, and whether the cheapness is real.">
            Investment · is this business worth owning
          </div>
          <div className="card-sub">
            Reported fundamentals from SEC EDGAR. Four independent readings, never
            blended into one score, plus a check on whether cheap means broken.
          </div>
        </div>
        <div className="inv-ctrl">
          <input className="inv-sym" value={sym} maxLength={8}
                 onChange={(e) => setSym(e.target.value.toUpperCase().replace(/[^A-Z.]/g, ""))}
                 onKeyDown={(e) => { if (e.key === "Enter") load(sym, years, true); }}
                 title="Type a ticker and press Enter. Follows the dashboard's
                   selected ticker automatically." aria-label="Ticker" />
          <button className="btn" onClick={() => load(sym, years, true)} disabled={busy}
                  title="Re-read the filings, the quote, the peer group and the Treasury curve now.">
            {busy ? "Loading…" : "Refresh"}
          </button>
          {onOpenTicker && (
            <button className="btn ghost" onClick={() => onOpenTicker(sym)}
                    title="Open this ticker on the Trade tab.">Trade tab →</button>
          )}
        </div>
      </div>

      {err && <div className="card-error">{err}</div>}
      {!data && !err && <div className="card-loading">Reading {sym}'s filings…</div>}

      {data && !d.ok && (
        <div className="research-empty" title="This is a real answer, not a
          failure. Some companies file only once a year, some report in a
          currency the share price is not quoted in, and exchange-traded funds
          do not file company accounts at all.">
          <b>{sym}</b> — {d.unavailable_reason || "No reported fundamentals available."}
        </div>
      )}

      {data && d.ok && (
        <div className="inv-body">
          {/* ── the decision, first ── */}
          <div className="inv-hero">
            <div className="inv-hero-top">
              <div className="inv-hero-name">
                <b title="Company name exactly as it registers with the SEC.">
                  {d.entity_name || sym}</b>
                <span className="muted" title={`SEC Central Index Key ${d.cik}${
                  d.sic ? ` · industry code ${d.sic}, ${d.sic_description}` : ""}`}>
                  {" "}· {sym} · {invPrice(d.price)}</span>
                {d.business_type && d.business_type.type !== "STANDARD" && (
                  <span className="inv-btype" title={d.business_type.note}>
                    {d.business_type.label}
                  </span>
                )}
                <InvCyclePill cycle={d.earnings_cycle} />
              </div>
              <InvVerdictPill verdict={v.verdict} />
            </div>

            <div className="inv-tiles">
              {INV_DIMENSIONS.map(([key, label, tip]) => (
                <InvScoreTile key={key} dimKey={key} label={label} tip={tip}
                              block={d[key]} onOpen={(k) => toggle(k, true)} />
              ))}
              <InvTrapTile trap={d.value_trap} onOpen={(k) => toggle(k, true)} />
            </div>

            <ul className="inv-reasons">
              {(v.reasons || []).map((r, i) => <li key={i}>{r}</li>)}
            </ul>
            {(v.what_would_change || []).length > 0 && (
              <div className="inv-change" title="Every WAIT or AVOID says what
                would have to change. The price is the level at which the
                earnings yield reaches this company's OWN median valuation —
                not a universal multiple.">
                {(v.what_would_change || []).map((c, i) => <div key={i}>{c}</div>)}
              </div>
            )}
            {d.profile && <InvMoatTags tags={d.profile.moat_tags} profile={d.profile} />}
          </div>

          {/* ── headline numbers ── */}
          <div className="inv-grid">
            <InvStat label="Share price" value={invPrice(d.price)} prov={prov.price}
                     tip="The current share price, used for every ratio below." />
            <InvStat label="Market value of the company" value={invMoney(d.market_cap)}
                     prov={prov.price}
                     basis={`Share price times ${invCount(d.shares_outstanding)} shares — ${(d.shares_detail || {}).basis || ""}`}
                     reason={(d.shares_detail || {}).reason}
                     tip="Share price multiplied by the share count on the cover
                       page of the latest filing — two sourced numbers, so both
                       halves can be checked." />
            <InvStat label="Price to earnings, trailing" value={invRatio(d.trailing_pe)}
                     prov={prov.fundamentals} basis={(md.eps || {}).basis}
                     asOf={(md.eps || {}).period_end}
                     reason={d.eps_ttm != null && d.eps_ttm <= 0
                       ? "Earnings are negative, and a negative price-to-earnings ratio is an arithmetic artifact rather than a cheap stock, so it is not shown."
                       : reasonOf("eps")}
                     tip="Share price divided by the last twelve months of
                       reported GAAP diluted earnings per share." />
            <InvStat label="Price to earnings, forward" value={invRatio(d.forward_pe)}
                     prov={prov.estimates}
                     reason={d.estimates_available ? "" : (prov.estimates || {}).reason || d.estimates_reason}
                     tip="Share price divided by the analyst consensus for this
                       fiscal year. Adjusted basis — never mixed with the GAAP
                       trailing figures inside one ratio." />
            <InvStat label="Earnings yield" value={invPct(d.earnings_yield_pct)}
                     prov={prov.fundamentals} reason={reasonOf("eps")}
                     tip="Trailing earnings per share divided by the share price.
                       This is what the valuation percentile is measured on." />
            <InvStat label="Free cash flow yield" value={invPct(d.fcf_yield_pct)}
                     prov={prov.fundamentals}
                     basis={(d.free_cash_flow_detail || {}).basis}
                     reason={(d.free_cash_flow_detail || {}).reason}
                     tone={d.fcf_yield_pct == null || d.fcf_yield_pct >= 0 ? "" : "down"}
                     tip="Cash from operations minus capital spending over the
                       last twelve months, divided by the market value of the
                       company." />
            <InvStat label="Revenue, last twelve months" value={invMoney(d.revenue_ttm, 0)}
                     prov={prov.fundamentals} basis={(md.revenue || {}).basis}
                     asOf={(md.revenue || {}).period_end} reason={reasonOf("revenue")}
                     tip="Total revenue over the last four reported quarters." />
            <InvStat label="Revenue growth, year over year"
                     value={invSignedPct(d.revenue_growth_pct)}
                     tone={d.revenue_growth_pct == null ? "" : (d.revenue_growth_pct >= 0 ? "up" : "down")}
                     prov={prov.fundamentals}
                     reason={d.revenue_growth_note || reasonOf("revenue")}
                     tip="This twelve-month revenue against the same twelve
                       months a year earlier." />
            <InvStat label="Earnings per share, last twelve months"
                     value={invPrice(d.eps_ttm)} prov={prov.fundamentals}
                     basis={(md.eps || {}).basis} asOf={(md.eps || {}).period_end}
                     reason={reasonOf("eps")}
                     tone={d.eps_ttm == null ? "" : (d.eps_ttm >= 0 ? "" : "down")}
                     tip="Reported GAAP diluted earnings per share, summed over
                       the last four quarters." />
            <InvStat label="Earnings growth, year over year"
                     value={invSignedPct(d.eps_growth_pct)}
                     tone={d.eps_growth_pct == null ? "" : (d.eps_growth_pct >= 0 ? "up" : "down")}
                     prov={prov.fundamentals}
                     reason={d.eps_growth_note || reasonOf("eps")}
                     tip="Trailing earnings per share against the same twelve
                       months a year earlier, on the same GAAP basis." />
            <InvStat label="Net profit margin" value={invPct(d.net_margin_pct)}
                     prov={prov.fundamentals}
                     reason={reasonOf("net_income") || reasonOf("revenue")}
                     tip="What share of each dollar of revenue survives as profit." />
            <InvStat label="Price to sales" value={invRatio(d.price_sales, 2)}
                     prov={prov.fundamentals} reason={reasonOf("revenue")}
                     tip="Market value divided by revenue. Shown because it is
                       easy to compute and people look for it — it plays NO part
                       in the verdict. A company can sell a great deal and never
                       earn anything, and this ratio cannot tell the two apart." />
            <InvStat label="10-year Treasury yield" value={invPct(d.treasury_10y_pct, 2)}
                     prov={prov.treasury_10y}
                     tip="What a government bond pays. Shown for context — it is
                       no longer a hurdle the stock has to clear, because a
                       universal yield threshold cannot tell an excellent
                       business priced fairly from a poor one priced cheaply." />
          </div>

          {/* ── the business ── */}
          {d.profile && (
            <div className="inv-desc">
              {d.profile.description}
              <div className="inv-src" title={`Quoted from Item 1, Business, of
                the annual report filed ${invDate(d.profile.as_of)}.`}>
                {d.profile.source} · filed {invShortDate(d.profile.as_of)}
                {d.profile.url && <> · <a href={d.profile.url} target="_blank"
                  rel="noopener noreferrer" title="Open the annual report on EDGAR.">read the filing</a></>}
              </div>
            </div>
          )}

          {/* ── the expandable detail, in decision order ── */}
          {section("valuation", "Valuation against its own history",
            "Where this company is priced today against where it has been priced before, using only what was public on each of those days.",
            <InvValuationHistory vh={d.valuation_history} valuation={d.valuation} symbol={sym} />)}

          {section("revisions", "Earnings and revisions",
            "What analysts expect and whether they are raising or cutting.",
            <InvRevisions revisions={d.revisions} snap={d} />)}

          {section("quality", "Quality",
            "The six inputs behind the quality reading, each scored or each explaining its absence.",
            <InvQuality quality={d.quality} />)}

          {section("peers", "Peers",
            "Comparable companies by the SEC's own industry code, and what the group is being valued at.",
            <InvPeers peers={d.peers} symbol={sym} snap={d} />)}

          {section("trap", "Value trap check",
            "Whether the business is deteriorating — the question cheapness cannot answer on its own.",
            <InvValueTrap trap={d.value_trap} />)}

          {section("growth", "Earnings drivers",
            "What moved earnings per share over the last year, split between revenue, margin and share count.",
            <InvDrivers drivers={d.drivers} />)}

          {section("chart", "Price against earnings",
            "Price and trailing earnings indexed to 100, so their shapes can be compared.",
            <InvHistoryChart history={d.history} years={years} onYears={setYears}
                             symbol={sym} />)}

          {section("stress", "Drawdown history",
            "What this stock actually did when things went wrong.",
            <InvDrawdowns dd={d.drawdowns} />)}

          {section("experimental", "Revision underreaction (experimental)",
            "An untested idea, recorded daily so it can be tested honestly later. It takes no part in the verdict.",
            <InvUnderreaction u={d.underreaction} />)}

          <div className="inv-foot" title={`This tab stores one snapshot of every
number above per day, so a later phase can test whether any of this predicted
anything. ${d.stored_days || 0} day${d.stored_days === 1 ? "" : "s"} recorded so far
for ${sym}. Nothing is ever back-filled or overwritten.`}>
            {d.stored_days || 0} daily snapshot{d.stored_days === 1 ? "" : "s"} recorded
            for {sym} · configuration {d.config_hash || "—"}
          </div>
        </div>
      )}
    </div>
  );
}

function InvUnderreaction({ u }) {
  const x = u || {};
  return (
    <div className="inv-under">
      <div className="inv-warn" title="This is recorded, not believed. The
        prospective snapshot store is what will eventually test whether it
        predicts anything at all.">
        <b>EXPERIMENTAL — unvalidated.</b> This dashboard has not tested whether
        this predicts anything, and it takes no part in the verdict.
      </div>
      {!x.available ? (
        <div className="inv-note">{x.reason || "Not computable yet."}</div>
      ) : (
        <div className="inv-subgrid">
          <InvStat label="Underreaction score" value={x.score == null ? invNA : x.score.toFixed(2)}
                   tip="How strongly estimates have been raised, minus how much the share price has already moved for it. Both halves are standardised across comparable companies first, so this is a relative reading, not an absolute one." />
          <InvStat label="Revision strength" value={x.revision_z == null ? invNA : x.revision_z.toFixed(2)}
                   tip="Change in this year's consensus over ninety days, scaled by the SHARE PRICE rather than by the old estimate — dividing by a near-zero estimate produces a number in the hundreds of percent that says more about the denominator than the revision." />
          <InvStat label="Price reaction" value={x.price_reaction_z == null ? invNA : x.price_reaction_z.toFixed(2)}
                   tip="This stock's ninety-day return minus its sector's, standardised the same way." />
        </div>
      )}
      <div className="inv-subgrid">
        <InvStat label="Revision breadth, 30 days" value={invSignedPct(x.revision_breadth_30d_pct)}
                 tip="Net share of covering analysts raising rather than cutting." />
        <InvStat label="Analyst coverage" value={x.analyst_count == null ? invNA : Math.round(x.analyst_count)}
                 tip="How many analysts publish an estimate." />
        <InvStat label="Earnings report inside the window"
                 value={x.earnings_inside_window == null ? invNA : (x.earnings_inside_window ? "Yes" : "No")}
                 tip="Whether the company reported inside the ninety days being measured. A revision that follows a report is a different animal from one that does not." />
      </div>
      {x.note && <div className="inv-note">{x.note}</div>}
    </div>
  );
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, { InvestTab: React.memo(InvestTab) });
