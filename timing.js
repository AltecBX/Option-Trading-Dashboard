(function () {
/* timing.jsx — Friday 0DTE timing card (spec v3 §19) + portfolio strip +
 * read-only thresholds section for the tweaks panel.
 *
 * Inside the Trade (Analyze) experience, not a new ecosystem. First mobile
 * screen answers the decision: SELL NOW or WAIT, score, confidence,
 * executable credit, expected extra in MY dollars, both probabilities,
 * minutes remaining, distance — plus WHAT CHANGED and the portfolio strip.
 * Expandable sections carry the depth. Every number wears its layer badge
 * (§21). Loaded eagerly (before app-cards) so the trade tab can mount it.
 */

const TM_STATE_TONE = {
  "WAIT": "wait",
  "GETTING CLOSE": "close",
  "SELL ZONE": "sell",
  "STRONG SELL ZONE": "strong",
  "TOO LATE": "late",
  "BLOCKED": "blocked"
};
function tmMoney(v, digits = 2) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toFixed(digits);
}
function tmPct(v) {
  if (v == null || isNaN(v)) return "—";
  return `${Math.round(v * 100)}%`;
}
function tmNextFriday() {
  const now = new Date();
  const d = new Date(now);
  const dow = d.getDay();
  let add = (5 - dow + 7) % 7;
  if (add === 0 && d.getHours() >= 16) add = 7;
  d.setDate(d.getDate() + add);
  return d.toISOString().slice(0, 10);
}
function TmSection({
  label,
  badge,
  open,
  onToggle,
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "tm-sect"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "tm-sect-btn",
    onClick: onToggle,
    "aria-expanded": open
  }, open ? "▾" : "▸", " ", label, badge ? /*#__PURE__*/React.createElement("span", {
    className: "tm-sect-badge"
  }, badge) : null), open && /*#__PURE__*/React.createElement("div", {
    className: "tm-sect-body"
  }, children));
}
function TmMetric({
  lbl,
  val,
  tone,
  title
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "metric",
    title: title || ""
  }, /*#__PURE__*/React.createElement("div", {
    className: "lbl"
  }, lbl), /*#__PURE__*/React.createElement("div", {
    className: `val${tone ? ` tm-${tone}` : ""}`
  }, val));
}

/* ── Contract picker ─────────────────────────────────────────────────── */

function TmPicker({
  ticker,
  currentPrice,
  onPick,
  saving
}) {
  const [strike, setStrike] = React.useState("");
  const [kind, setKind] = React.useState("call");
  const [expiry, setExpiry] = React.useState(tmNextFriday());
  const [contracts, setContracts] = React.useState("2");
  return /*#__PURE__*/React.createElement("div", {
    className: "tm-picker"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tm-picker-row"
  }, /*#__PURE__*/React.createElement("select", {
    className: "tm-inp tm-kind",
    value: kind,
    onChange: e => setKind(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "call"
  }, "Call"), /*#__PURE__*/React.createElement("option", {
    value: "put"
  }, "Put")), /*#__PURE__*/React.createElement("input", {
    className: "tm-inp tm-strike",
    type: "number",
    inputMode: "decimal",
    placeholder: currentPrice ? `strike (spot ${Math.round(currentPrice)})` : "strike",
    value: strike,
    onChange: e => setStrike(e.target.value)
  }), /*#__PURE__*/React.createElement("input", {
    className: "tm-inp tm-exp",
    type: "date",
    value: expiry,
    onChange: e => setExpiry(e.target.value)
  }), /*#__PURE__*/React.createElement("input", {
    className: "tm-inp tm-qty",
    type: "number",
    inputMode: "numeric",
    min: "1",
    title: "contracts (for my-dollar amounts)",
    value: contracts,
    onChange: e => setContracts(e.target.value)
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "tm-btn tm-go",
    disabled: !strike || saving,
    onClick: () => onPick({
      symbol: ticker,
      strike: parseFloat(strike),
      kind,
      expiry,
      contracts: parseInt(contracts, 10) || 1
    })
  }, "Watch")), /*#__PURE__*/React.createElement("div", {
    className: "tm-picker-hint muted"
  }, "Pick the short ", kind, " you're considering \u2014 the engine watches it, records its tape, and answers sell-now-or-wait. Saved contracts alert your phone even with the app closed."));
}

/* ── Decision hero ───────────────────────────────────────────────────── */

function TmHero({
  st
}) {
  const tone = TM_STATE_TONE[st.state] || "wait";
  const c = st.credit || {};
  const p = st.probabilities || {};
  const w = st.wait || {};
  const r = st.risk || {};
  return /*#__PURE__*/React.createElement("div", {
    className: `tm-hero tm-hero-${tone}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "tm-hero-top"
  }, /*#__PURE__*/React.createElement("span", {
    className: `tm-state tm-state-${tone}`
  }, st.state), st.score != null && /*#__PURE__*/React.createElement("span", {
    className: "tm-score",
    title: "0-100 stopping-summary score (\xA717) \u2014 the probabilities and dollars below are the decision"
  }, st.score), st.confidence != null && /*#__PURE__*/React.createElement("span", {
    className: "tm-conf",
    title: "model confidence (Phase A simulation prior, uncalibrated)"
  }, "conf ", Math.round(st.confidence * 100), "%"), /*#__PURE__*/React.createElement("span", {
    className: "tm-layer",
    title: "which layer produced this state (\xA721)"
  }, st.layer), st.hysteresis && st.hysteresis.held && /*#__PURE__*/React.createElement("span", {
    className: "tm-held",
    title: `raw state ${st.state_raw}; displayed state held by hysteresis (§34)`
  }, "held")), /*#__PURE__*/React.createElement("div", {
    className: "tm-reason"
  }, st.reason), (st.what_changed || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "tm-changed"
  }, st.what_changed.map((x, i) => /*#__PURE__*/React.createElement("span", {
    key: i,
    className: "tm-chg"
  }, x))), /*#__PURE__*/React.createElement("div", {
    className: "metric-grid tm-heromet"
  }, /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Executable credit",
    val: tmMoney(c.bid),
    title: "current BID \u2014 the price that matters (\xA72), never last"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Extra if waiting",
    val: w.expected_extra_dollars != null ? `$${Math.round(w.expected_extra_dollars)}` : tmMoney(w.expected_extra_credit),
    title: "expected additional ADMISSIBLE credit ahead (per your size when contracts set)"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "P better @ next look",
    val: tmPct(p.p_better_next_look),
    title: "probability a better admissible premium is capturable at your next look (\xA731)"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "P(touch)",
    val: tmPct(p.p_touch),
    tone: p.p_touch > 0.4 ? "warn" : null,
    title: `simulation P(strike touch before close); analytic prior ${tmPct(p.p_touch_analytic)}`
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "P(ITM)",
    val: tmPct(p.p_itm),
    tone: p.p_itm > 0.25 ? "warn" : null,
    title: `simulation P(expiration ITM); analytic prior ${tmPct(p.p_itm_analytic)}`
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Minutes left",
    val: r.minutes_remaining != null ? Math.round(r.minutes_remaining) : "—"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "To strike",
    val: r.distance ? `${tmMoney(r.distance.dollars)} (${r.distance.sigma}σ)` : "—"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Intent",
    val: (st.intent || "").replace("_", " "),
    title: "\xA729 \u2014 wheel-acceptable loosens limits; income-only tightens them"
  })), !(st.risk || {}).admissible && !st.blocked && /*#__PURE__*/React.createElement("div", {
    className: "tm-inadm"
  }, "\u26A0 Selling now sits OUTSIDE your ", (st.intent || "").replace("_", " "), " limits \u2014 hazardous premium, not opportunity."));
}

/* ── Expandable detail sections ──────────────────────────────────────── */

function TmDetails({
  st
}) {
  const [open, setOpen] = React.useState({});
  const t = k => setOpen(o => ({
    ...o,
    [k]: !o[k]
  }));
  const c = st.credit || {};
  const w = st.wait || {};
  const r = st.risk || {};
  const lim = st.limits || {};
  const sess = c.session || null;
  const dec = w.decomposition || null;
  return /*#__PURE__*/React.createElement("div", {
    className: "tm-details"
  }, /*#__PURE__*/React.createElement(TmSection, {
    label: "Premium opportunity",
    open: !!open.prem,
    onToggle: () => t("prem"),
    badge: sess ? "tape live" : "no tape yet"
  }, /*#__PURE__*/React.createElement("div", {
    className: "metric-grid"
  }, /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Bid / Mid / Last",
    val: `${tmMoney(c.bid)} / ${tmMoney(c.mid)} / ${tmMoney(c.last)}`
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Spread",
    val: c.spread_pct != null ? `${c.spread_pct}%` : "—"
  }), sess && /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Session exec high",
    val: tmMoney(sess.exec_high),
    title: "highest BID this session (resting-limit benchmark, \xA732)"
  }), sess && /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Durable high (60s)",
    val: tmMoney(sess.durable_high),
    title: "highest bid sustained \u226560s \u2014 the honest chase benchmark (\xA72/Amendment E)"
  }), sess && /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Admissible high",
    val: tmMoney(sess.admissible_high),
    title: "best bid while inside your risk limits (\xA75)"
  }), w.hazardous_premium != null && /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Hazardous premium",
    val: tmMoney(w.hazardous_premium),
    tone: w.hazardous_premium > 0.2 ? "warn" : null,
    title: "expected extra that exists only OUTSIDE your limits \u2014 shown, never scored (\xA75)"
  })), lim.likely && /*#__PURE__*/React.createElement("div", {
    className: "tm-limits"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tm-limits-title"
  }, "Three resting limits (\xA714 \u2014 the spike fills you; you don't chase it)"), /*#__PURE__*/React.createElement("div", {
    className: "tm-tblwrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tm-tbl"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null), /*#__PURE__*/React.createElement("th", null, "limit"), /*#__PURE__*/React.createElement("th", null, "fill prob"), /*#__PURE__*/React.createElement("th", null, "~mins"), /*#__PURE__*/React.createElement("th", null, "spot req."), /*#__PURE__*/React.createElement("th", null, "\u0394 if filled"), /*#__PURE__*/React.createElement("th", null, "P(ITM) if filled"))), /*#__PURE__*/React.createElement("tbody", null, ["likely", "balanced", "stretch"].map(k => lim[k] ? /*#__PURE__*/React.createElement("tr", {
    key: k
  }, /*#__PURE__*/React.createElement("td", {
    className: "tm-limname"
  }, k), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, tmMoney(lim[k].price))), /*#__PURE__*/React.createElement("td", null, tmPct(lim[k].fill_prob)), /*#__PURE__*/React.createElement("td", null, lim[k].expected_minutes_to_fill != null ? Math.round(lim[k].expected_minutes_to_fill) : "—"), /*#__PURE__*/React.createElement("td", null, tmMoney(lim[k].spot_required)), /*#__PURE__*/React.createElement("td", null, lim[k].delta_if_filled != null ? lim[k].delta_if_filled.toFixed(2) : "—"), /*#__PURE__*/React.createElement("td", null, tmPct(lim[k].p_itm_if_filled))) : null))))), st.tranche && /*#__PURE__*/React.createElement("div", {
    className: "tm-tranche"
  }, "Tranche: ", /*#__PURE__*/React.createElement("b", null, st.tranche.suggest_frac != null ? `sell ${Math.round(st.tranche.suggest_frac * 100)}% now` : "—"), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", st.tranche.basis))), /*#__PURE__*/React.createElement(TmSection, {
    label: "Underlying timing",
    open: !!open.und,
    onToggle: () => t("und")
  }, /*#__PURE__*/React.createElement("div", {
    className: "metric-grid"
  }, /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Projected extreme",
    val: tmMoney(st.projected_extreme),
    title: "printed extreme + remaining 1\u03C3 (SIMULATION PRIOR \u2014 Phase A2 trained model replaces this)"
  }), (st.events || []).length > 0 && /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Events today",
    val: st.events.map(e => `${e.kind.toUpperCase()} ${e.at_et}`).join(", "),
    tone: "warn",
    title: "scheduled events widen simulated ranges near their timestamps (\xA715)"
  }), st.heuristic && /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "v2 heuristic cross-check",
    val: `${st.heuristic.state} (${st.heuristic.score})`,
    tone: st.heuristic.disagree_hard ? "warn" : null,
    title: `momentum gate M=${st.heuristic.momentum_gate_m}; ${st.heuristic.disagree_hard ? "HARD disagreement with the simulation — confidence reduced" : "agrees with the simulation"}`
  })), st.heuristic && st.heuristic.disagree_hard && /*#__PURE__*/React.createElement("div", {
    className: "tm-inadm"
  }, "Simulation and heuristic disagree hard \u2014 confidence lowered; trust the probabilities, not the score.")), /*#__PURE__*/React.createElement(TmSection, {
    label: "Option risk",
    open: !!open.risk,
    onToggle: () => t("risk")
  }, /*#__PURE__*/React.createElement("div", {
    className: "metric-grid"
  }, /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Delta",
    val: r.delta != null ? r.delta.toFixed(2) : "—"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Gamma",
    val: r.gamma != null ? r.gamma.toFixed(4) : "—"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Theta / min",
    val: r.theta_per_min != null ? r.theta_per_min.toFixed(4) : "—"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Vega",
    val: r.vega != null ? r.vega.toFixed(3) : "—"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "IV",
    val: r.iv != null ? `${(r.iv * 100).toFixed(0)}%` : "—",
    title: `source: ${r.iv_source || "—"} (${r.iv_usability || ""})`
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Risk headroom",
    val: r.headroom_dollars != null ? `$${tmMoney(r.headroom_dollars)}` : "—",
    title: "adverse spot move that fits before your P(ITM) limit breaks"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Quote age",
    val: c.quote_age_s != null ? `${c.quote_age_s}s` : "—"
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "MC std err",
    val: st.probabilities && st.probabilities.mc_se != null ? st.probabilities.mc_se.toFixed(3) : "—",
    title: "Monte Carlo noise floor \u2014 hysteresis margins sit above this (Amendment B)"
  }))), /*#__PURE__*/React.createElement(TmSection, {
    label: "Wait decomposition",
    open: !!open.dec,
    onToggle: () => t("dec")
  }, dec && /*#__PURE__*/React.createElement("div", {
    className: "metric-grid"
  }, /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Spot (\u0394\xB7dS)",
    val: tmMoney(dec.spot),
    title: `for a 1σ 15-min move of $${tmMoney(dec.dS_used)}`
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Gamma (\xBD\u0393\xB7dS\xB2)",
    val: tmMoney(dec.gamma)
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Theta (15 min)",
    val: tmMoney(dec.theta)
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "IV (\u22121 pt)",
    val: tmMoney(dec.iv)
  }), /*#__PURE__*/React.createElement(TmMetric, {
    lbl: "Execution (\xBD spread)",
    val: tmMoney(dec.execution)
  })), (w.scenarios || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "tm-tblwrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tm-tbl"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "scenario"), /*#__PURE__*/React.createElement("th", null, "spot"), /*#__PURE__*/React.createElement("th", null, "modeled bid"), /*#__PURE__*/React.createElement("th", null, "\u0394"), /*#__PURE__*/React.createElement("th", null, "P(ITM)"))), /*#__PURE__*/React.createElement("tbody", null, w.scenarios.map((s, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, s.label), /*#__PURE__*/React.createElement("td", null, tmMoney(s.target_spot)), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, tmMoney(s.exec_bid))), /*#__PURE__*/React.createElement("td", null, s.delta != null ? s.delta.toFixed(2) : "—"), /*#__PURE__*/React.createElement("td", null, tmPct(s.p_itm))))))), /*#__PURE__*/React.createElement("div", {
    className: "tm-note muted"
  }, "Display only \u2014 the decision comes from the simulated path distributions (\xA710).")));
}

/* ── Final hour strip (§33) ──────────────────────────────────────────── */

function TmFinalHour({
  fh
}) {
  if (!fh || !fh.active) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: `tm-fh${fh.recommend && fh.recommend.includes("BUY TO CLOSE") ? " tm-fh-hot" : ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "tm-fh-title"
  }, "FINAL WINDOW \u2014 close vs carry"), /*#__PURE__*/React.createElement("div", {
    className: "tm-fh-row"
  }, /*#__PURE__*/React.createElement("span", null, "cost to close ", /*#__PURE__*/React.createElement("b", null, tmMoney(fh.cost_to_close))), /*#__PURE__*/React.createElement("span", null, "risk / remaining penny ", /*#__PURE__*/React.createElement("b", null, fh.risk_per_penny != null ? fh.risk_per_penny : "∞")), /*#__PURE__*/React.createElement("span", null, "P(touch before bell) ", /*#__PURE__*/React.createElement("b", null, tmPct(fh.p_touch_before_bell)))), fh.recommend && /*#__PURE__*/React.createElement("div", {
    className: "tm-fh-rec"
  }, fh.recommend), fh.exercise_watch && fh.exercise_watch.active && /*#__PURE__*/React.createElement("div", {
    className: "tm-fh-watch"
  }, "AFTER-HOURS EXERCISE WATCH until ", fh.exercise_watch.until_et, " ET \u2014 ", fh.exercise_watch.note));
}

/* ── Portfolio strip (§30) ───────────────────────────────────────────── */

function TmPortfolio({
  apiFetch,
  positions
}) {
  const [roll, setRoll] = React.useState(null);
  const legs = React.useMemo(() => (positions || []).filter(p => (p.qty < 0 || p.contracts > 0) && (p.type === "call" || p.type === "put") && p.status !== "closed").map(p => ({
    symbol: p.ticker || p.symbol,
    strike: p.strike,
    kind: p.type,
    expiry: p.expDate || p.expiration,
    credit: p.entryPrice ?? p.entryPremium ?? 0,
    contracts: Math.abs(p.contracts || p.qty || 1)
  })), [positions]);
  React.useEffect(() => {
    if (!legs.length) {
      setRoll(null);
      return;
    }
    let stop = false;
    const load = async () => {
      try {
        const r = await apiFetch("/api/timing/portfolio", {
          method: "POST",
          noCache: true,
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            legs
          })
        });
        const j = await r.json();
        if (!stop && j && !j.error) setRoll(j);
      } catch (e) {/* keep last */}
    };
    load();
    const t = setInterval(skipWhenHidden(load), 90 * 1000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [JSON.stringify(legs)]);
  if (!legs.length || !roll) return null;
  const used = roll.risk_budget_used_pct;
  return /*#__PURE__*/React.createElement("div", {
    className: "tm-port"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tm-port-head"
  }, /*#__PURE__*/React.createElement("span", {
    className: "tm-port-title"
  }, "Portfolio \xB7 shared-shock view"), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, roll.legs.length, " legs \xB7 credit today $", Math.round(roll.total_credit_today)), used != null && /*#__PURE__*/React.createElement("span", {
    className: `tm-budget${used >= 80 ? " tm-warn" : ""}`,
    title: `worst all-in outcome under a shared ±2% ${roll.sector_proxy} move vs your $${roll.risk_budget_usd} weekly budget`
  }, "risk budget ", used, "%")), /*#__PURE__*/React.createElement("div", {
    className: "tm-shocks"
  }, Object.entries(roll.shock_table || {}).map(([s, v]) => /*#__PURE__*/React.createElement("span", {
    key: s,
    className: `tm-shock${v.all_in < 0 ? " tm-neg" : ""}`,
    title: `option-only ${Math.round(v.option_only)}`
  }, s, "%: ", /*#__PURE__*/React.createElement("b", null, v.all_in >= 0 ? "+" : "", Math.round(v.all_in))))), /*#__PURE__*/React.createElement("div", {
    className: "tm-note muted"
  }, roll.strikes_within_1pct > 1 ? `${roll.strikes_within_1pct} strikes within 1% — one tape, one trade. ` : "", "Every leg repriced under the SAME sector move; wheel legs valued all-in (Amendment D)."));
}

/* ── Fill logger (§4/§32) ────────────────────────────────────────────── */

function TmFill({
  apiFetch,
  contract,
  bid,
  onLogged
}) {
  const [openf, setOpenf] = React.useState(false);
  const [credit, setCredit] = React.useState("");
  const [mode, setMode] = React.useState("resting");
  const [n, setN] = React.useState(String(contract.contracts || 2));
  const [busy, setBusy] = React.useState(false);
  const [done, setDone] = React.useState(null);
  const log = async () => {
    setBusy(true);
    try {
      const r = await apiFetch("/api/timing/fill", {
        method: "POST",
        noCache: true,
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          symbol: contract.symbol,
          strike: contract.strike,
          kind: contract.kind,
          expiry: contract.expiry,
          credit: parseFloat(credit || bid || 0),
          contracts: parseInt(n, 10) || 1,
          mode
        })
      });
      const j = await r.json();
      setDone(j && j.ok ? "logged with full decision state ✓" : j.error || "failed");
      if (j && j.ok && onLogged) onLogged();
    } catch (e) {
      setDone(String(e));
    }
    setBusy(false);
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "tm-fill"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "tm-btn",
    onClick: () => {
      setOpenf(o => !o);
      setCredit(String(bid ?? ""));
      setDone(null);
    },
    "aria-expanded": openf
  }, openf ? "▾" : "▸", " Log a fill"), openf && /*#__PURE__*/React.createElement("div", {
    className: "tm-fill-row"
  }, /*#__PURE__*/React.createElement("input", {
    className: "tm-inp",
    type: "number",
    step: "0.01",
    inputMode: "decimal",
    value: credit,
    onChange: e => setCredit(e.target.value),
    placeholder: "credit"
  }), /*#__PURE__*/React.createElement("input", {
    className: "tm-inp tm-qty",
    type: "number",
    inputMode: "numeric",
    value: n,
    onChange: e => setN(e.target.value)
  }), /*#__PURE__*/React.createElement("div", {
    className: "tm-seg"
  }, ["resting", "chase"].map(m => /*#__PURE__*/React.createElement("button", {
    key: m,
    type: "button",
    className: `tm-seg-btn${mode === m ? " on" : ""}`,
    title: m === "resting" ? "standing limit lifted (scored vs Executable High)" : "you saw it and acted (scored vs Durable High)",
    onClick: () => setMode(m)
  }, m))), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "tm-btn tm-go",
    disabled: busy,
    onClick: log
  }, "Save"), done && /*#__PURE__*/React.createElement("span", {
    className: "tm-fill-done muted"
  }, done)));
}

/* ── The card ────────────────────────────────────────────────────────── */

function TimingCard({
  apiFetch,
  ticker,
  currentPrice,
  positions
}) {
  const [cands, setCands] = React.useState([]);
  const [active, setActive] = React.useState(null); // {symbol,strike,kind,expiry,contracts,key}
  const [st, setSt] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [diag, setDiag] = React.useState(null);
  const [showDiag, setShowDiag] = React.useState(false);
  const loadCands = React.useCallback(() => {
    sharedJson(apiFetch, "/api/timing/contracts", 30 * 1000).then(d => {
      const rows = d && d.candidates || [];
      setCands(rows);
      setActive(a => a || rows.find(r => r.symbol === ticker) || null);
    }).catch(() => {});
  }, [ticker]);
  React.useEffect(() => {
    loadCands();
  }, [loadCands]);
  React.useEffect(() => {
    setActive((cands || []).find(r => r.symbol === ticker) || null);
    setSt(null);
    setErr(null);
  }, [ticker]);
  React.useEffect(() => {
    if (!active) return;
    let stop = false;
    const load = () => {
      const q = `symbol=${encodeURIComponent(active.symbol)}&strike=${active.strike}` + `&kind=${active.kind}&expiry=${active.expiry}` + (active.contracts ? `&contracts=${active.contracts}` : "");
      apiFetch(`/api/timing/state?${q}`, {
        noCache: true
      }).then(r => r.json()).then(d => {
        if (stop) return;
        if (d && d.state) {
          setSt(d);
          setErr(null);
        } else setErr(d && d.error || "no state");
      }).catch(e => {
        if (!stop) setErr(String(e && e.message || e));
      });
    };
    load();
    const t = setInterval(skipWhenHidden(load), 30 * 1000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [active && active.key, active && active.symbol, active && active.strike]);
  const watch = async c => {
    setBusy(true);
    try {
      await apiFetch("/api/timing/candidates", {
        method: "POST",
        noCache: true,
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(c)
      });
      const added = {
        ...c,
        key: `${c.symbol}|${c.expiry}|${c.kind}|${c.strike}`
      };
      setCands(prev => [...(prev || []).filter(x => x.key !== added.key), added]);
      setActive(added);
    } catch (e) {/* noop */}
    setBusy(false);
  };
  const unwatch = async key => {
    setBusy(true);
    try {
      await apiFetch("/api/timing/candidates", {
        method: "POST",
        noCache: true,
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          op: "remove",
          key
        })
      });
      // Local truth — a sharedJson re-fetch within its TTL would revive
      // the removed candidate from cache and re-activate it.
      setCands(prev => (prev || []).filter(c => c.key !== key));
      setActive(null);
      setSt(null);
    } catch (e) {/* noop */}
    setBusy(false);
  };
  const setIntent = async intent => {
    if (!active) return;
    try {
      await apiFetch("/api/timing/intent", {
        method: "POST",
        noCache: true,
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          symbol: active.symbol,
          kind: active.kind,
          intent
        })
      });
      setSt(s => s ? {
        ...s,
        intent
      } : s);
    } catch (e) {/* noop */}
  };
  const runReplay = async () => {
    setDiag({
      running: true
    });
    try {
      const r = await apiFetch("/api/timing/replay_day", {
        method: "POST",
        noCache: true,
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          day: "2026-08-14"
        })
      });
      setDiag(await r.json());
    } catch (e) {
      setDiag({
        error: String(e)
      });
    }
  };
  const mine = (cands || []).filter(c => c.symbol === ticker);
  const others = (cands || []).filter(c => c.symbol !== ticker);
  return /*#__PURE__*/React.createElement("div", {
    className: "card tm-card",
    style: {
      marginBottom: "var(--row-gap)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Friday 0DTE \xB7 optimal stopping"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Premium timing \u2014 sell now or wait?")), st && st.model && /*#__PURE__*/React.createElement("div", {
    className: "tm-modeltag muted",
    title: `config ${st.model.config_hash} · seed ${st.model.seed} · ${st.model.paths} paths — every decision replays byte-identical`
  }, st.model.version)), mine.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "tm-cands"
  }, mine.map(c => /*#__PURE__*/React.createElement("button", {
    key: c.key,
    type: "button",
    className: `tm-cand${active && active.key === c.key ? " on" : ""}`,
    onClick: () => {
      setActive(c);
      setSt(null);
    }
  }, c.strike, c.kind === "call" ? "C" : "P", " ", c.expiry.slice(5))), active && /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "tm-cand tm-cand-x",
    disabled: busy,
    onClick: () => unwatch(active.key),
    title: "stop watching"
  }, "\u2715")), !active && /*#__PURE__*/React.createElement(TmPicker, {
    ticker: ticker,
    currentPrice: currentPrice,
    onPick: watch,
    saving: busy
  }), active && !st && !err && /*#__PURE__*/React.createElement("div", {
    className: "tm-loading muted"
  }, "evaluating ", active.symbol, " ", active.strike, active.kind === "call" ? "C" : "P", "\u2026 (seeded Monte Carlo)"), active && err && /*#__PURE__*/React.createElement("div", {
    className: "tm-err"
  }, "engine error: ", err), active && st && st.blocked && /*#__PURE__*/React.createElement("div", {
    className: "tm-blocked"
  }, /*#__PURE__*/React.createElement("span", {
    className: "tm-state tm-state-blocked"
  }, "BLOCKED"), /*#__PURE__*/React.createElement("b", null, " ", st.blocked.code), " \u2014 ", st.blocked.detail), active && st && !st.blocked && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(TmFinalHour, {
    fh: st.final_hour
  }), /*#__PURE__*/React.createElement(TmHero, {
    st: st
  }), /*#__PURE__*/React.createElement("div", {
    className: "tm-intent-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "Position intent:"), ["income_only", "wheel_acceptable"].map(i => /*#__PURE__*/React.createElement("button", {
    key: i,
    type: "button",
    className: `tm-seg-btn${st.intent === i ? " on" : ""}`,
    title: i === "income_only" ? "assignment is a failure — strict limits, high breach cost" : "you own/want the shares — looser limits, waiting is genuinely cheaper (§29)",
    onClick: () => setIntent(i)
  }, i.replace("_", " "))), /*#__PURE__*/React.createElement(TmFill, {
    apiFetch: apiFetch,
    contract: active,
    bid: st.credit && st.credit.bid
  })), /*#__PURE__*/React.createElement(TmDetails, {
    st: st
  })), /*#__PURE__*/React.createElement(TmPortfolio, {
    apiFetch: apiFetch,
    positions: positions
  }), others.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "tm-others muted"
  }, "also watching: ", others.map(c => `${c.symbol} ${c.strike}${c.kind === "call" ? "C" : "P"}`).join(" · ")), /*#__PURE__*/React.createElement("div", {
    className: "tm-foot"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "tm-diag-btn",
    onClick: () => setShowDiag(s => !s),
    "aria-expanded": showDiag
  }, showDiag ? "▾" : "▸", " diagnostics"), showDiag && /*#__PURE__*/React.createElement("div", {
    className: "tm-diag"
  }, /*#__PURE__*/React.createElement(TmStatus, {
    apiFetch: apiFetch
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "tm-btn",
    onClick: runReplay,
    disabled: diag && diag.running
  }, "Run Aug 14 diagnostic replay (\xA726)"), diag && !diag.running && /*#__PURE__*/React.createElement(TmReplay, {
    diag: diag
  }))));
}
function TmStatus({
  apiFetch
}) {
  const [s, setS] = React.useState(null);
  React.useEffect(() => {
    sharedJson(apiFetch, "/api/timing/status", 60 * 1000).then(setS).catch(() => {});
  }, []);
  if (!s) return null;
  const tape = s.tape || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "tm-status muted"
  }, "engine ", s.model_version, " \xB7 config ", s.config_hash, " \xB7 clock drift ", s.clock && s.clock.drift_s != null ? `${s.clock.drift_s}s` : "unmeasured", s.clock_blocked ? /*#__PURE__*/React.createElement("b", {
    className: "tm-warn"
  }, " \xB7 ", s.clock_blocked) : "", /*#__PURE__*/React.createElement("br", null), "tape: ", tape.running ? "recording" : "idle", " \xB7 ", tape.snapshots_today || 0, " snapshots today \xB7 req/min ", tape.req_last_min != null ? tape.req_last_min : "—", " (budget ", tape.budget_req_per_min, ")", tape.degraded ? ` · degraded: ${tape.degraded}` : "", " \xB7 ", tape.storage_kb != null ? `${tape.storage_kb} KB stored` : "");
}
function TmReplay({
  diag
}) {
  if (diag.error) return /*#__PURE__*/React.createElement("div", {
    className: "tm-err"
  }, diag.error);
  return /*#__PURE__*/React.createElement("div", {
    className: "tm-replay"
  }, (diag.trades || []).map((t, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "tm-replay-row"
  }, /*#__PURE__*/React.createElement("b", null, t.symbol, " ", t.strike, (t.kind || "")[0] === "c" ? "C" : "P"), t.error ? /*#__PURE__*/React.createElement("span", {
    className: "tm-err"
  }, " ", t.error) : /*#__PURE__*/React.createElement("span", null, t.measured && ` touched: ${t.measured.touched_strike ? "yes" : "no"} · ${t.measured.minutes_beyond_strike}min beyond · close dist ${t.measured.closing_distance}`, t.modeled && t.modeled.modeled_forward_max != null && ` · modeled fwd max ${t.modeled.modeled_forward_max} (adm ${t.modeled.modeled_admissible_max != null ? t.modeled.modeled_admissible_max : "none — all hazardous"})`, t.modeled && t.modeled.note ? ` · ${t.modeled.note}` : "", t.final_hour_330pm && t.final_hour_330pm.within_1pct ? ` · 3:30pm within 1% of strike` : ""))), diag.portfolio_1pm && /*#__PURE__*/React.createElement("div", {
    className: "tm-replay-row muted"
  }, "1pm portfolio: ", diag.portfolio_1pm.moves.map(m => `${m.symbol} ${m.pct_from_open_at_1pm > 0 ? "+" : ""}${m.pct_from_open_at_1pm}%`).join(" · "), " \u2014 ", diag.portfolio_1pm.note), diag.honesty && /*#__PURE__*/React.createElement("div", {
    className: "tm-note muted"
  }, diag.honesty));
}

/* ── Tweaks panel read-only thresholds (§35) ─────────────────────────── */

function TimingThresholds({
  apiFetch
}) {
  const [cfg, setCfg] = React.useState(null);
  React.useEffect(() => {
    sharedJson(apiFetch, "/api/timing/config", 5 * 60 * 1000).then(setCfg).catch(() => {});
  }, []);
  const TweakSection = window.TweakSection;
  const TweakRow = window.TweakRow;
  if (!cfg || !cfg.config || !TweakSection) return null;
  const c = cfg.config;
  const risk = c.risk || {};
  const inc = risk.income_only || {};
  const whl = risk.wheel_acceptable || {};
  return /*#__PURE__*/React.createElement(TweakSection, {
    label: `0DTE thresholds (read-only · ${cfg.hash})`
  }, /*#__PURE__*/React.createElement(TweakRow, {
    label: "Income-only limits",
    value: `ITM ${tmPct(inc.max_p_itm)} · touch ${tmPct(inc.max_p_touch)} · Δ ${inc.max_delta}`
  }), /*#__PURE__*/React.createElement(TweakRow, {
    label: "Wheel limits",
    value: `ITM ${tmPct(whl.max_p_itm)} · touch ${tmPct(whl.max_p_touch)} · Δ ${whl.max_delta}`
  }), /*#__PURE__*/React.createElement(TweakRow, {
    label: "Simulation",
    value: `${(c.simulation || {}).paths} paths / decision`
  }), /*#__PURE__*/React.createElement(TweakRow, {
    label: "Attention",
    value: `look every ${(c.attention || {}).look_interval_min}min · ${(c.attention || {}).alert_budget_per_day} alerts/day`
  }), /*#__PURE__*/React.createElement(TweakRow, {
    label: "Tape cadence",
    value: `P1 ${(c.tape || {}).p1_seconds}s · P2 ${(c.tape || {}).p2_seconds}s · durable ${(c.tape || {}).durable_seconds}s`
  }), /*#__PURE__*/React.createElement(TweakRow, {
    label: "Final hour",
    value: `window ${(c.final_hour || {}).window_min}min · pennies ≤ ${(c.final_hour || {}).pennies_max_cost}`
  }), /*#__PURE__*/React.createElement(TweakRow, {
    label: "Risk budget",
    value: `$${(c.portfolio || {}).weekly_max_loss_usd} vs ±2% ${(c.portfolio || {}).sector_proxy} shock`
  }), /*#__PURE__*/React.createElement(TweakRow, {
    label: "Edit",
    value: "thresholds.json in the data dir overrides these"
  }));
}
Object.assign(window, {
  TimingCard,
  TimingThresholds
});
})();
