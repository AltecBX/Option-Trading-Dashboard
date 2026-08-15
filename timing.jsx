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
  "WAIT": "wait", "GETTING CLOSE": "close", "SELL ZONE": "sell",
  "STRONG SELL ZONE": "strong", "TOO LATE": "late", "BLOCKED": "blocked",
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

function TmSection({ label, badge, open, onToggle, children }) {
  return (
    <div className="tm-sect">
      <button type="button" className="tm-sect-btn" onClick={onToggle} aria-expanded={open}>
        {open ? "▾" : "▸"} {label}
        {badge ? <span className="tm-sect-badge">{badge}</span> : null}
      </button>
      {open && <div className="tm-sect-body">{children}</div>}
    </div>
  );
}

function TmMetric({ lbl, val, tone, title }) {
  return (
    <div className="metric" title={title || ""}>
      <div className="lbl">{lbl}</div>
      <div className={`val${tone ? ` tm-${tone}` : ""}`}>{val}</div>
    </div>
  );
}

/* ── Contract picker ─────────────────────────────────────────────────── */

function TmPicker({ ticker, currentPrice, onPick, saving }) {
  const [strike, setStrike] = React.useState("");
  const [kind, setKind] = React.useState("call");
  const [expiry, setExpiry] = React.useState(tmNextFriday());
  const [contracts, setContracts] = React.useState("2");
  return (
    <div className="tm-picker">
      <div className="tm-picker-row">
        <select className="tm-inp tm-kind" value={kind} onChange={e => setKind(e.target.value)}>
          <option value="call">Call</option>
          <option value="put">Put</option>
        </select>
        <input className="tm-inp tm-strike" type="number" inputMode="decimal"
          placeholder={currentPrice ? `strike (spot ${Math.round(currentPrice)})` : "strike"}
          value={strike} onChange={e => setStrike(e.target.value)} />
        <input className="tm-inp tm-exp" type="date" value={expiry}
          onChange={e => setExpiry(e.target.value)} />
        <input className="tm-inp tm-qty" type="number" inputMode="numeric" min="1"
          title="contracts (for my-dollar amounts)" value={contracts}
          onChange={e => setContracts(e.target.value)} />
        <button type="button" className="tm-btn tm-go" disabled={!strike || saving}
          onClick={() => onPick({
            symbol: ticker, strike: parseFloat(strike), kind, expiry,
            contracts: parseInt(contracts, 10) || 1,
          })}>Watch</button>
      </div>
      <div className="tm-picker-hint muted">
        Pick the short {kind} you're considering — the engine watches it, records its tape,
        and answers sell-now-or-wait. Saved contracts alert your phone even with the app closed.
      </div>
    </div>
  );
}

/* ── Decision hero ───────────────────────────────────────────────────── */

function TmHero({ st }) {
  const tone = TM_STATE_TONE[st.state] || "wait";
  const c = st.credit || {};
  const p = st.probabilities || {};
  const w = st.wait || {};
  const r = st.risk || {};
  return (
    <div className={`tm-hero tm-hero-${tone}`}>
      <div className="tm-hero-top">
        <span className={`tm-state tm-state-${tone}`}>{st.state}</span>
        {st.score != null && <span className="tm-score" title="0-100 stopping-summary score (§17) — the probabilities and dollars below are the decision">{st.score}</span>}
        {st.confidence != null && (
          <span className="tm-conf" title="model confidence (Phase A simulation prior, uncalibrated)">
            conf {Math.round(st.confidence * 100)}%
          </span>
        )}
        <span className="tm-layer" title="which layer produced this state (§21)">{st.layer}</span>
        {st.hysteresis && st.hysteresis.held && (
          <span className="tm-held" title={`raw state ${st.state_raw}; displayed state held by hysteresis (§34)`}>held</span>
        )}
      </div>
      <div className="tm-reason">{st.reason}</div>
      {(st.what_changed || []).length > 0 && (
        <div className="tm-changed">
          {st.what_changed.map((x, i) => <span key={i} className="tm-chg">{x}</span>)}
        </div>
      )}
      <div className="metric-grid tm-heromet">
        <TmMetric lbl="Executable credit" val={tmMoney(c.bid)} title="current BID — the price that matters (§2), never last" />
        <TmMetric lbl="Extra if waiting" val={w.expected_extra_dollars != null
          ? `$${Math.round(w.expected_extra_dollars)}` : tmMoney(w.expected_extra_credit)}
          title="expected additional ADMISSIBLE credit ahead (per your size when contracts set)" />
        <TmMetric lbl="P better @ next look" val={tmPct(p.p_better_next_look)}
          title="probability a better admissible premium is capturable at your next look (§31)" />
        <TmMetric lbl="P(touch)" val={tmPct(p.p_touch)} tone={p.p_touch > 0.4 ? "warn" : null}
          title={`simulation P(strike touch before close); analytic prior ${tmPct(p.p_touch_analytic)}`} />
        <TmMetric lbl="P(ITM)" val={tmPct(p.p_itm)} tone={p.p_itm > 0.25 ? "warn" : null}
          title={`simulation P(expiration ITM); analytic prior ${tmPct(p.p_itm_analytic)}`} />
        <TmMetric lbl="Minutes left" val={r.minutes_remaining != null ? Math.round(r.minutes_remaining) : "—"} />
        <TmMetric lbl="To strike" val={r.distance ? `${tmMoney(r.distance.dollars)} (${r.distance.sigma}σ)` : "—"} />
        <TmMetric lbl="Intent" val={(st.intent || "").replace("_", " ")}
          title="§29 — wheel-acceptable loosens limits; income-only tightens them" />
      </div>
      {!((st.risk || {}).admissible) && !st.blocked && (
        <div className="tm-inadm">⚠ Selling now sits OUTSIDE your {(st.intent || "").replace("_", " ")} limits — hazardous premium, not opportunity.</div>
      )}
    </div>
  );
}

/* ── Expandable detail sections ──────────────────────────────────────── */

function TmDetails({ st }) {
  const [open, setOpen] = React.useState({});
  const t = k => setOpen(o => ({ ...o, [k]: !o[k] }));
  const c = st.credit || {}; const w = st.wait || {}; const r = st.risk || {};
  const lim = st.limits || {}; const sess = c.session || null;
  const dec = w.decomposition || null;
  return (
    <div className="tm-details">
      <TmSection label="Premium opportunity" open={!!open.prem} onToggle={() => t("prem")}
        badge={sess ? "tape live" : "no tape yet"}>
        <div className="metric-grid">
          <TmMetric lbl="Bid / Mid / Last" val={`${tmMoney(c.bid)} / ${tmMoney(c.mid)} / ${tmMoney(c.last)}`} />
          <TmMetric lbl="Spread" val={c.spread_pct != null ? `${c.spread_pct}%` : "—"} />
          {sess && <TmMetric lbl="Session exec high" val={tmMoney(sess.exec_high)} title="highest BID this session (resting-limit benchmark, §32)" />}
          {sess && <TmMetric lbl="Durable high (60s)" val={tmMoney(sess.durable_high)} title="highest bid sustained ≥60s — the honest chase benchmark (§2/Amendment E)" />}
          {sess && <TmMetric lbl="Admissible high" val={tmMoney(sess.admissible_high)} title="best bid while inside your risk limits (§5)" />}
          {w.hazardous_premium != null && <TmMetric lbl="Hazardous premium" val={tmMoney(w.hazardous_premium)} tone={w.hazardous_premium > 0.2 ? "warn" : null} title="expected extra that exists only OUTSIDE your limits — shown, never scored (§5)" />}
        </div>
        {lim.likely && (
          <div className="tm-limits">
            <div className="tm-limits-title">Three resting limits (§14 — the spike fills you; you don't chase it)</div>
            <div className="tm-tblwrap"><table className="tm-tbl"><thead><tr>
              <th></th><th>limit</th><th>fill prob</th><th>~mins</th><th>spot req.</th><th>Δ if filled</th><th>P(ITM) if filled</th>
            </tr></thead><tbody>
              {["likely", "balanced", "stretch"].map(k => lim[k] ? (
                <tr key={k}>
                  <td className="tm-limname">{k}</td>
                  <td><b>{tmMoney(lim[k].price)}</b></td>
                  <td>{tmPct(lim[k].fill_prob)}</td>
                  <td>{lim[k].expected_minutes_to_fill != null ? Math.round(lim[k].expected_minutes_to_fill) : "—"}</td>
                  <td>{tmMoney(lim[k].spot_required)}</td>
                  <td>{lim[k].delta_if_filled != null ? lim[k].delta_if_filled.toFixed(2) : "—"}</td>
                  <td>{tmPct(lim[k].p_itm_if_filled)}</td>
                </tr>) : null)}
            </tbody></table></div>
          </div>
        )}
        {st.tranche && (
          <div className="tm-tranche">
            Tranche: <b>{st.tranche.suggest_frac != null ? `sell ${Math.round(st.tranche.suggest_frac * 100)}% now` : "—"}</b>
            <span className="muted"> · {st.tranche.basis}</span>
          </div>
        )}
      </TmSection>

      <TmSection label="Underlying timing" open={!!open.und} onToggle={() => t("und")}>
        <div className="metric-grid">
          <TmMetric lbl="Projected extreme" val={tmMoney(st.projected_extreme)} title="printed extreme + remaining 1σ (SIMULATION PRIOR — Phase A2 trained model replaces this)" />
          {(st.events || []).length > 0 && (
            <TmMetric lbl="Events today" val={st.events.map(e => `${e.kind.toUpperCase()} ${e.at_et}`).join(", ")} tone="warn"
              title="scheduled events widen simulated ranges near their timestamps (§15)" />
          )}
          {st.heuristic && (
            <TmMetric lbl="v2 heuristic cross-check" val={`${st.heuristic.state} (${st.heuristic.score})`}
              tone={st.heuristic.disagree_hard ? "warn" : null}
              title={`momentum gate M=${st.heuristic.momentum_gate_m}; ${st.heuristic.disagree_hard ? "HARD disagreement with the simulation — confidence reduced" : "agrees with the simulation"}`} />
          )}
        </div>
        {st.heuristic && st.heuristic.disagree_hard && (
          <div className="tm-inadm">Simulation and heuristic disagree hard — confidence lowered; trust the probabilities, not the score.</div>
        )}
      </TmSection>

      <TmSection label="Option risk" open={!!open.risk} onToggle={() => t("risk")}>
        <div className="metric-grid">
          <TmMetric lbl="Delta" val={r.delta != null ? r.delta.toFixed(2) : "—"} />
          <TmMetric lbl="Gamma" val={r.gamma != null ? r.gamma.toFixed(4) : "—"} />
          <TmMetric lbl="Theta / min" val={r.theta_per_min != null ? r.theta_per_min.toFixed(4) : "—"} />
          <TmMetric lbl="Vega" val={r.vega != null ? r.vega.toFixed(3) : "—"} />
          <TmMetric lbl="IV" val={r.iv != null ? `${(r.iv * 100).toFixed(0)}%` : "—"}
            title={`source: ${r.iv_source || "—"} (${r.iv_usability || ""})`} />
          <TmMetric lbl="Risk headroom" val={r.headroom_dollars != null ? `$${tmMoney(r.headroom_dollars)}` : "—"}
            title="adverse spot move that fits before your P(ITM) limit breaks" />
          <TmMetric lbl="Quote age" val={c.quote_age_s != null ? `${c.quote_age_s}s` : "—"} />
          <TmMetric lbl="MC std err" val={st.probabilities && st.probabilities.mc_se != null ? st.probabilities.mc_se.toFixed(3) : "—"}
            title="Monte Carlo noise floor — hysteresis margins sit above this (Amendment B)" />
        </div>
      </TmSection>

      <TmSection label="Wait decomposition" open={!!open.dec} onToggle={() => t("dec")}>
        {dec && (
          <div className="metric-grid">
            <TmMetric lbl="Spot (Δ·dS)" val={tmMoney(dec.spot)} title={`for a 1σ 15-min move of $${tmMoney(dec.dS_used)}`} />
            <TmMetric lbl="Gamma (½Γ·dS²)" val={tmMoney(dec.gamma)} />
            <TmMetric lbl="Theta (15 min)" val={tmMoney(dec.theta)} />
            <TmMetric lbl="IV (−1 pt)" val={tmMoney(dec.iv)} />
            <TmMetric lbl="Execution (½ spread)" val={tmMoney(dec.execution)} />
          </div>
        )}
        {(w.scenarios || []).length > 0 && (
          <div className="tm-tblwrap"><table className="tm-tbl"><thead><tr>
            <th>scenario</th><th>spot</th><th>modeled bid</th><th>Δ</th><th>P(ITM)</th>
          </tr></thead><tbody>
            {w.scenarios.map((s, i) => (
              <tr key={i}><td>{s.label}</td><td>{tmMoney(s.target_spot)}</td>
                <td><b>{tmMoney(s.exec_bid)}</b></td><td>{s.delta != null ? s.delta.toFixed(2) : "—"}</td>
                <td>{tmPct(s.p_itm)}</td></tr>
            ))}
          </tbody></table></div>
        )}
        <div className="tm-note muted">Display only — the decision comes from the simulated path distributions (§10).</div>
      </TmSection>
    </div>
  );
}

/* ── Final hour strip (§33) ──────────────────────────────────────────── */

function TmFinalHour({ fh }) {
  if (!fh || !fh.active) return null;
  return (
    <div className={`tm-fh${fh.recommend && fh.recommend.includes("BUY TO CLOSE") ? " tm-fh-hot" : ""}`}>
      <div className="tm-fh-title">FINAL WINDOW — close vs carry</div>
      <div className="tm-fh-row">
        <span>cost to close <b>{tmMoney(fh.cost_to_close)}</b></span>
        <span>risk / remaining penny <b>{fh.risk_per_penny != null ? fh.risk_per_penny : "∞"}</b></span>
        <span>P(touch before bell) <b>{tmPct(fh.p_touch_before_bell)}</b></span>
      </div>
      {fh.recommend && <div className="tm-fh-rec">{fh.recommend}</div>}
      {fh.exercise_watch && fh.exercise_watch.active && (
        <div className="tm-fh-watch">AFTER-HOURS EXERCISE WATCH until {fh.exercise_watch.until_et} ET — {fh.exercise_watch.note}</div>
      )}
    </div>
  );
}

/* ── Portfolio strip (§30) ───────────────────────────────────────────── */

function TmPortfolio({ apiFetch, positions }) {
  const [roll, setRoll] = React.useState(null);
  const legs = React.useMemo(() => (positions || [])
    .filter(p => (p.qty < 0 || p.contracts > 0) && (p.type === "call" || p.type === "put")
      && p.status !== "closed")
    .map(p => ({
      symbol: p.ticker || p.symbol, strike: p.strike,
      kind: p.type, expiry: p.expDate || p.expiration,
      credit: p.entryPrice ?? p.entryPremium ?? 0,
      contracts: Math.abs(p.contracts || p.qty || 1),
    })), [positions]);
  React.useEffect(() => {
    if (!legs.length) { setRoll(null); return; }
    let stop = false;
    const load = async () => {
      try {
        const r = await apiFetch("/api/timing/portfolio", {
          method: "POST", noCache: true,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ legs }),
        });
        const j = await r.json();
        if (!stop && j && !j.error) setRoll(j);
      } catch (e) { /* keep last */ }
    };
    load();
    const t = setInterval(skipWhenHidden(load), 90 * 1000);
    return () => { stop = true; clearInterval(t); };
  }, [JSON.stringify(legs)]);
  if (!legs.length || !roll) return null;
  const used = roll.risk_budget_used_pct;
  return (
    <div className="tm-port">
      <div className="tm-port-head">
        <span className="tm-port-title">Portfolio · shared-shock view</span>
        <span className="muted">{roll.legs.length} legs · credit today ${Math.round(roll.total_credit_today)}</span>
        {used != null && (
          <span className={`tm-budget${used >= 80 ? " tm-warn" : ""}`}
            title={`worst all-in outcome under a shared ±2% ${roll.sector_proxy} move vs your $${roll.risk_budget_usd} weekly budget`}>
            risk budget {used}%
          </span>
        )}
      </div>
      <div className="tm-shocks">
        {Object.entries(roll.shock_table || {}).map(([s, v]) => (
          <span key={s} className={`tm-shock${v.all_in < 0 ? " tm-neg" : ""}`}
            title={`option-only ${Math.round(v.option_only)}`}>
            {s}%: <b>{v.all_in >= 0 ? "+" : ""}{Math.round(v.all_in)}</b>
          </span>
        ))}
      </div>
      <div className="tm-note muted">{roll.strikes_within_1pct > 1 ? `${roll.strikes_within_1pct} strikes within 1% — one tape, one trade. ` : ""}Every leg repriced under the SAME sector move; wheel legs valued all-in (Amendment D).</div>
    </div>
  );
}

/* ── Fill logger (§4/§32) ────────────────────────────────────────────── */

function TmFill({ apiFetch, contract, bid, onLogged }) {
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
        method: "POST", noCache: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: contract.symbol, strike: contract.strike, kind: contract.kind,
          expiry: contract.expiry, credit: parseFloat(credit || bid || 0),
          contracts: parseInt(n, 10) || 1, mode,
        }),
      });
      const j = await r.json();
      setDone(j && j.ok ? "logged with full decision state ✓" : (j.error || "failed"));
      if (j && j.ok && onLogged) onLogged();
    } catch (e) { setDone(String(e)); }
    setBusy(false);
  };
  return (
    <div className="tm-fill">
      <button type="button" className="tm-btn" onClick={() => { setOpenf(o => !o); setCredit(String(bid ?? "")); setDone(null); }}
        aria-expanded={openf}>{openf ? "▾" : "▸"} Log a fill</button>
      {openf && (
        <div className="tm-fill-row">
          <input className="tm-inp" type="number" step="0.01" inputMode="decimal" value={credit}
            onChange={e => setCredit(e.target.value)} placeholder="credit" />
          <input className="tm-inp tm-qty" type="number" inputMode="numeric" value={n}
            onChange={e => setN(e.target.value)} />
          <div className="tm-seg">
            {["resting", "chase"].map(m => (
              <button key={m} type="button" className={`tm-seg-btn${mode === m ? " on" : ""}`}
                title={m === "resting" ? "standing limit lifted (scored vs Executable High)" : "you saw it and acted (scored vs Durable High)"}
                onClick={() => setMode(m)}>{m}</button>
            ))}
          </div>
          <button type="button" className="tm-btn tm-go" disabled={busy} onClick={log}>Save</button>
          {done && <span className="tm-fill-done muted">{done}</span>}
        </div>
      )}
    </div>
  );
}

/* ── The card ────────────────────────────────────────────────────────── */

function TimingCard({ apiFetch, ticker, currentPrice, positions }) {
  const [cands, setCands] = React.useState([]);
  const [active, setActive] = React.useState(null);   // {symbol,strike,kind,expiry,contracts,key}
  const [st, setSt] = React.useState(null);
  const [err, setErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [diag, setDiag] = React.useState(null);
  const [showDiag, setShowDiag] = React.useState(false);

  const loadCands = React.useCallback(() => {
    sharedJson(apiFetch, "/api/timing/contracts", 30 * 1000)
      .then(d => {
        const rows = (d && d.candidates) || [];
        setCands(rows);
        setActive(a => a || rows.find(r => r.symbol === ticker) || null);
      }).catch(() => {});
  }, [ticker]);
  React.useEffect(() => { loadCands(); }, [loadCands]);
  React.useEffect(() => {
    setActive((cands || []).find(r => r.symbol === ticker) || null);
    setSt(null); setErr(null);
  }, [ticker]);

  React.useEffect(() => {
    if (!active) return;
    let stop = false;
    const load = () => {
      const q = `symbol=${encodeURIComponent(active.symbol)}&strike=${active.strike}` +
        `&kind=${active.kind}&expiry=${active.expiry}` +
        (active.contracts ? `&contracts=${active.contracts}` : "");
      apiFetch(`/api/timing/state?${q}`, { noCache: true })
        .then(r => r.json())
        .then(d => {
          if (stop) return;
          if (d && d.state) { setSt(d); setErr(null); }
          else setErr((d && d.error) || "no state");
        })
        .catch(e => { if (!stop) setErr(String(e && e.message || e)); });
    };
    load();
    const t = setInterval(skipWhenHidden(load), 30 * 1000);
    return () => { stop = true; clearInterval(t); };
  }, [active && active.key, active && active.symbol, active && active.strike]);

  const watch = async (c) => {
    setBusy(true);
    try {
      await apiFetch("/api/timing/candidates", {
        method: "POST", noCache: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(c),
      });
      const added = { ...c, key: `${c.symbol}|${c.expiry}|${c.kind}|${c.strike}` };
      setCands(prev => [...(prev || []).filter(x => x.key !== added.key), added]);
      setActive(added);
    } catch (e) { /* noop */ }
    setBusy(false);
  };
  const unwatch = async (key) => {
    setBusy(true);
    try {
      await apiFetch("/api/timing/candidates", {
        method: "POST", noCache: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ op: "remove", key }),
      });
      // Local truth — a sharedJson re-fetch within its TTL would revive
      // the removed candidate from cache and re-activate it.
      setCands(prev => (prev || []).filter(c => c.key !== key));
      setActive(null); setSt(null);
    } catch (e) { /* noop */ }
    setBusy(false);
  };
  const setIntent = async (intent) => {
    if (!active) return;
    try {
      await apiFetch("/api/timing/intent", {
        method: "POST", noCache: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: active.symbol, kind: active.kind, intent }),
      });
      setSt(s => s ? { ...s, intent } : s);
    } catch (e) { /* noop */ }
  };
  const runReplay = async () => {
    setDiag({ running: true });
    try {
      const r = await apiFetch("/api/timing/replay_day", {
        method: "POST", noCache: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ day: "2026-08-14" }),
      });
      setDiag(await r.json());
    } catch (e) { setDiag({ error: String(e) }); }
  };

  const mine = (cands || []).filter(c => c.symbol === ticker);
  const others = (cands || []).filter(c => c.symbol !== ticker);
  return (
    <div className="card tm-card" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker">Friday 0DTE · optimal stopping</div>
          <div className="card-title">Premium timing — sell now or wait?</div>
        </div>
        {st && st.model && (
          <div className="tm-modeltag muted" title={`config ${st.model.config_hash} · seed ${st.model.seed} · ${st.model.paths} paths — every decision replays byte-identical`}>
            {st.model.version}
          </div>
        )}
      </div>

      {mine.length > 0 && (
        <div className="tm-cands">
          {mine.map(c => (
            <button key={c.key} type="button"
              className={`tm-cand${active && active.key === c.key ? " on" : ""}`}
              onClick={() => { setActive(c); setSt(null); }}>
              {c.strike}{c.kind === "call" ? "C" : "P"} {c.expiry.slice(5)}
            </button>
          ))}
          {active && (
            <button type="button" className="tm-cand tm-cand-x" disabled={busy}
              onClick={() => unwatch(active.key)} title="stop watching">✕</button>
          )}
        </div>
      )}

      {!active && <TmPicker ticker={ticker} currentPrice={currentPrice} onPick={watch} saving={busy} />}

      {active && !st && !err && <div className="tm-loading muted">evaluating {active.symbol} {active.strike}{active.kind === "call" ? "C" : "P"}… (seeded Monte Carlo)</div>}
      {active && err && <div className="tm-err">engine error: {err}</div>}

      {active && st && st.blocked && (
        <div className="tm-blocked">
          <span className="tm-state tm-state-blocked">BLOCKED</span>
          <b> {st.blocked.code}</b> — {st.blocked.detail}
        </div>
      )}

      {active && st && !st.blocked && (
        <React.Fragment>
          <TmFinalHour fh={st.final_hour} />
          <TmHero st={st} />
          <div className="tm-intent-row">
            <span className="muted">Position intent:</span>
            {["income_only", "wheel_acceptable"].map(i => (
              <button key={i} type="button" className={`tm-seg-btn${st.intent === i ? " on" : ""}`}
                title={i === "income_only" ? "assignment is a failure — strict limits, high breach cost" : "you own/want the shares — looser limits, waiting is genuinely cheaper (§29)"}
                onClick={() => setIntent(i)}>{i.replace("_", " ")}</button>
            ))}
            <TmFill apiFetch={apiFetch} contract={active} bid={st.credit && st.credit.bid} />
          </div>
          <TmDetails st={st} />
        </React.Fragment>
      )}

      <TmPortfolio apiFetch={apiFetch} positions={positions} />

      {others.length > 0 && (
        <div className="tm-others muted">
          also watching: {others.map(c => `${c.symbol} ${c.strike}${c.kind === "call" ? "C" : "P"}`).join(" · ")}
        </div>
      )}

      <div className="tm-foot">
        <button type="button" className="tm-diag-btn" onClick={() => setShowDiag(s => !s)} aria-expanded={showDiag}>
          {showDiag ? "▾" : "▸"} diagnostics
        </button>
        {showDiag && (
          <div className="tm-diag">
            <TmStatus apiFetch={apiFetch} />
            <button type="button" className="tm-btn" onClick={runReplay} disabled={diag && diag.running}>
              Run Aug 14 diagnostic replay (§26)
            </button>
            {diag && !diag.running && <TmReplay diag={diag} />}
          </div>
        )}
      </div>
    </div>
  );
}

function TmStatus({ apiFetch }) {
  const [s, setS] = React.useState(null);
  React.useEffect(() => {
    sharedJson(apiFetch, "/api/timing/status", 60 * 1000).then(setS).catch(() => {});
  }, []);
  if (!s) return null;
  const tape = s.tape || {};
  return (
    <div className="tm-status muted">
      engine {s.model_version} · config {s.config_hash} · clock drift {s.clock && s.clock.drift_s != null ? `${s.clock.drift_s}s` : "unmeasured"}
      {s.clock_blocked ? <b className="tm-warn"> · {s.clock_blocked}</b> : ""}
      <br />
      tape: {tape.running ? "recording" : "idle"} · {tape.snapshots_today || 0} snapshots today ·
      req/min {tape.req_last_min != null ? tape.req_last_min : "—"} (budget {tape.budget_req_per_min})
      {tape.degraded ? ` · degraded: ${tape.degraded}` : ""} · {tape.storage_kb != null ? `${tape.storage_kb} KB stored` : ""}
    </div>
  );
}

function TmReplay({ diag }) {
  if (diag.error) return <div className="tm-err">{diag.error}</div>;
  return (
    <div className="tm-replay">
      {(diag.trades || []).map((t, i) => (
        <div key={i} className="tm-replay-row">
          <b>{t.symbol} {t.strike}{(t.kind || "")[0] === "c" ? "C" : "P"}</b>
          {t.error ? <span className="tm-err"> {t.error}</span> : (
            <span>
              {t.measured && ` touched: ${t.measured.touched_strike ? "yes" : "no"} · ${t.measured.minutes_beyond_strike}min beyond · close dist ${t.measured.closing_distance}`}
              {t.modeled && t.modeled.modeled_forward_max != null &&
                ` · modeled fwd max ${t.modeled.modeled_forward_max} (adm ${t.modeled.modeled_admissible_max != null ? t.modeled.modeled_admissible_max : "none — all hazardous"})`}
              {t.modeled && t.modeled.note ? ` · ${t.modeled.note}` : ""}
              {t.final_hour_330pm && t.final_hour_330pm.within_1pct ? ` · 3:30pm within 1% of strike` : ""}
            </span>
          )}
        </div>
      ))}
      {diag.portfolio_1pm && (
        <div className="tm-replay-row muted">
          1pm portfolio: {diag.portfolio_1pm.moves.map(m => `${m.symbol} ${m.pct_from_open_at_1pm > 0 ? "+" : ""}${m.pct_from_open_at_1pm}%`).join(" · ")} — {diag.portfolio_1pm.note}
        </div>
      )}
      {diag.honesty && <div className="tm-note muted">{diag.honesty}</div>}
    </div>
  );
}

/* ── Tweaks panel read-only thresholds (§35) ─────────────────────────── */

function TimingThresholds({ apiFetch }) {
  const [cfg, setCfg] = React.useState(null);
  React.useEffect(() => {
    sharedJson(apiFetch, "/api/timing/config", 5 * 60 * 1000).then(setCfg).catch(() => {});
  }, []);
  const TweakSection = window.TweakSection;
  const TweakRow = window.TweakRow;
  if (!cfg || !cfg.config || !TweakSection) return null;
  const c = cfg.config;
  const risk = c.risk || {}; const inc = risk.income_only || {}; const whl = risk.wheel_acceptable || {};
  return (
    <TweakSection label={`0DTE thresholds (read-only · ${cfg.hash})`}>
      <TweakRow label="Income-only limits" value={`ITM ${tmPct(inc.max_p_itm)} · touch ${tmPct(inc.max_p_touch)} · Δ ${inc.max_delta}`} />
      <TweakRow label="Wheel limits" value={`ITM ${tmPct(whl.max_p_itm)} · touch ${tmPct(whl.max_p_touch)} · Δ ${whl.max_delta}`} />
      <TweakRow label="Simulation" value={`${(c.simulation || {}).paths} paths / decision`} />
      <TweakRow label="Attention" value={`look every ${(c.attention || {}).look_interval_min}min · ${(c.attention || {}).alert_budget_per_day} alerts/day`} />
      <TweakRow label="Tape cadence" value={`P1 ${(c.tape || {}).p1_seconds}s · P2 ${(c.tape || {}).p2_seconds}s · durable ${(c.tape || {}).durable_seconds}s`} />
      <TweakRow label="Final hour" value={`window ${(c.final_hour || {}).window_min}min · pennies ≤ ${(c.final_hour || {}).pennies_max_cost}`} />
      <TweakRow label="Risk budget" value={`$${(c.portfolio || {}).weekly_max_loss_usd} vs ±2% ${(c.portfolio || {}).sector_proxy} shock`} />
      <TweakRow label="Edit" value="thresholds.json in the data dir overrides these" />
    </TweakSection>
  );
}

Object.assign(window, { TimingCard, TimingThresholds });
