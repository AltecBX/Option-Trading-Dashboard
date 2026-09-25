// tab-live.jsx — LAZY CHUNK (v5.29). THE LIVE SCANNER.
//
// Jerry saw an open-source day-trading scanner (a live feed of alerts —
// new high of day, gap holding, breakout on volume — beside ranked lists of
// gainers, losers and volume) and asked for it here, "100x better". The
// engine is live_scan.py; this is the screen. What it adds to a feed:
//
//   • every alert is a sentence, not a code: WHY it fired, in words;
//   • every setup carries its own graded record — how often its alerts were
//     still going the right way 30 minutes later, measured by the scanner
//     itself after each one;
//   • Setup Check answers "why didn't this stock alert?";
//   • the setups are Jerry's to edit, and any one can push to the phone.
//
// Endpoints: GET /api/live · /api/live/check?symbol= · /api/live/setups
//            POST /api/live/setups {setups:[...]} | {action:"reset"}

const LV_TIP = {
  card: "Every stock on your watchlist, checked every 30 seconds while the market is open (every minute before it). An alert fires when a setup's trigger happens and the stock passes that setup's conditions.",
  status: "When the scanner last checked prices, and how many of your watchlist names it got a price for.",
  side: "LONG setups are moves up (a new high, a breakout). SHORT setups are moves down. It is the direction of the move, not an instruction to trade.",
  record: "The setup's own track record: of its alerts over the last 20 sessions, how many were still moving the alert's way 30 minutes later, and by how much on average. Measured by the scanner after every alert — a setup that does not work shows it here.",
  rvol: "Relative volume: today's volume against this stock's 20-day average, adjusted for the time of day. 2.0x means twice as busy as usual for this hour.",
  check: "Type any symbol on your watchlist to see every setup: whether its trigger is live right now, and exactly which condition stopped an alert.",
  setups: "Rename, tune or switch off any setup, set the conditions a stock must pass, how long before the same stock can fire again, and whether it pushes to your phone.",
  sound: "Play a short beep in this browser when a new alert arrives.",
  lists: "Ranked lists from the same prices. Only stocks over $5 with at least 500K shares a day are listed, so the lists are names you can trade options on.",
};

const LV_LISTS = [
  ["gainers", "Gainers", "change_pct"], ["losers", "Losers", "change_pct"],
  ["active", "Most active", "volume"], ["rvol", "Volume surge", "rvol"],
  ["movers_5m", "5-min movers", "move_5m"], ["gap_up", "Gap up", "gap_pct"],
  ["gap_down", "Gap down", "gap_pct"],
];
const LV_PM_LISTS = [
  ["pm_gainers", "Pre-market gainers", "pm_change_pct"],
  ["pm_losers", "Pre-market losers", "pm_change_pct"],
  ["pm_volume", "Pre-market volume", "pm_volume"],
];

const lvNum = (v, d = 2) => (v == null || !isFinite(v) ? "—" : Number(v).toFixed(d));
const lvPct = (v, d = 1) => (v == null || !isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
const lvVol = (v) => {
  if (v == null || !isFinite(v)) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(Math.round(v));
};
const lvAgo = (iso) => {
  if (!iso) return null;
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
};
const LV_PHASE = {
  open: "Live", pre: "Pre-market", post: "After the close", closed: "Market closed",
};

function lvBeep() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = lvBeep.ctx || (lvBeep.ctx = new Ctx());
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = 880; g.gain.value = 0.06;
    o.connect(g); g.connect(ctx.destination);
    o.start(); o.stop(ctx.currentTime + 0.12);
  } catch (_) { /* no audio: nothing to do */ }
}

function LvRecord({ rec }) {
  if (!rec || !rec.n) {
    return <span className="lv-rec lv-rec-new" title={LV_TIP.record}>no record yet</span>;
  }
  const tone = rec.follow_rate >= 55 ? "up" : rec.follow_rate < 45 ? "down" : "";
  return (
    <span className={`lv-rec ${tone}`} title={LV_TIP.record}>
      {rec.follow_rate}% of {rec.n} · {lvPct(rec.avg_r30, 2)}
    </span>
  );
}

function LvAlertRow({ a, rec, onOpen }) {
  const long = a.side === "long";
  return (
    <li className={`lv-alert ${long ? "lv-long" : "lv-short"}`}>
      <div className="lv-a-top">
        <span className="lv-time">{a.time}</span>
        <span className={`lv-side ${long ? "up" : "down"}`} title={LV_TIP.side}>
          {long ? "▲ LONG" : "▼ SHORT"}
        </span>
        <button className="lv-sym" onClick={() => onOpen && onOpen(a.symbol)}
                title={`Open ${a.symbol} on the Trade tab`}>{a.symbol}</button>
        <span className="lv-setup">{a.setup}</span>
        <span className="lv-a-nums">
          <span className="lv-price">{a.price != null ? `$${lvNum(a.price)}` : "—"}</span>
          <span className={a.change_pct > 0 ? "up" : a.change_pct < 0 ? "down" : ""}>{lvPct(a.change_pct)}</span>
          {a.rvol != null ? <span className="lv-rv" title={LV_TIP.rvol}>{lvNum(a.rvol, 1)}x</span> : null}
        </span>
      </div>
      <div className="lv-why">{a.why}</div>
      <div className="lv-a-foot"><LvRecord rec={rec} /></div>
    </li>
  );
}

function LvRankings({ rankings, phase, onOpen }) {
  const lists = phase === "pre" ? LV_PM_LISTS.concat(LV_LISTS) : LV_LISTS.concat(LV_PM_LISTS);
  // null = "the right list for the session": Pre-market gainers before the
  // bell, Gainers after. The screen mounts before the first answer says
  // which phase it is, so a remembered default would stay on the empty
  // regular list all morning (Codex, #416). A tap is kept.
  const [pick, setPick] = useState(null);
  const cur = (pick && lists.find(l => l[0] === pick)) || lists[0];
  const rows = (rankings && rankings[cur[0]]) || [];
  const metric = cur[2];
  const fmt = (r) => metric === "volume" || metric === "pm_volume" ? lvVol(r[metric])
    : metric === "rvol" ? `${lvNum(r.rvol, 1)}x` : lvPct(r[metric], 2);
  return (
    <div className="lv-ranks" title={LV_TIP.lists}>
      <div className="lv-seg" role="tablist">
        {lists.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={cur[0] === k}
                  className={`lv-seg-btn ${cur[0] === k ? "on" : ""}`}
                  onClick={() => setPick(k)}>{label}</button>
        ))}
      </div>
      {rows.length ? (
        <table className="scan-table lv-rtable">
          <thead>
            <tr>
              <th>#</th><th>Symbol</th>
              <th className="scan-num">Price</th>
              <th className="scan-num">{cur[1]}</th>
              <th className="scan-num">Day</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const px = r.last != null ? r.last : r.pm_last;
              const day = cur[0].startsWith("pm_") ? r.pm_change_pct : r.change_pct;
              return (
                <tr key={r.symbol}>
                  <td className="lv-rank">{i + 1}</td>
                  <td><button className="lv-sym" onClick={() => onOpen && onOpen(r.symbol)}>{r.symbol}</button></td>
                  <td className="scan-num">{px != null ? `$${lvNum(px)}` : "—"}</td>
                  <td className="scan-num lv-metric">{fmt(r)}</td>
                  <td className={`scan-num ${day > 0 ? "up" : day < 0 ? "down" : ""}`}>{lvPct(day)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <p className="lv-empty">Nothing on this list yet{phase === "pre" && !cur[0].startsWith("pm_")
          ? " — before the bell the regular-session lists are empty; the pre-market lists are live." : "."}</p>
      )}
    </div>
  );
}

function LvCheck({ apiFetch, initial }) {
  const [sym, setSym] = useState(initial || "");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async (e) => {
    if (e) e.preventDefault();
    const s = sym.trim().toUpperCase();
    if (!s) return;
    setBusy(true);
    try {
      const r = await apiFetch(`/api/live/check?symbol=${encodeURIComponent(s)}`);
      setRes(await r.json());
    } catch (err) {
      setRes({ symbol: s, known: false, why: `Could not reach the scanner (${err.message || err}).` });
    } finally { setBusy(false); }
  };
  return (
    <div className="lv-check" title={LV_TIP.check}>
      <form className="lv-check-form" onSubmit={run}>
        <input className="lv-input" value={sym} placeholder="Symbol, e.g. NVDA"
               aria-label="Symbol to check" onChange={e => setSym(e.target.value)} />
        <button className="research-run-btn" type="submit" disabled={busy}>{busy ? "Checking…" : "Check"}</button>
      </form>
      {res && !res.known ? <p className="lv-empty">{res.why}</p> : null}
      {res && res.known ? (
        <ul className="lv-check-list">
          {res.setups.map(r => (
            <li key={r.setup_id} className={`lv-check-row ${r.fired_ts ? "fired" : r.live ? "live" : ""}`}>
              <span className="lv-check-name">{r.setup}</span>
              <span className="lv-check-verdict">{r.verdict}</span>
              {r.live || r.fired_ts ? <span className="lv-check-detail">{r.detail}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function LvSetupEditor({ apiFetch, onClose, onSaved }) {
  const [items, setItems] = useState(null);
  const [meta, setMeta] = useState(null);
  const [sel, setSel] = useState(0);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let stop = false;
    apiFetch("/api/live/setups").then(r => r.json()).then(d => {
      if (stop) return;
      setItems(d.setups || []); setMeta(d.meta || null);
    }).catch(e => !stop && setMsg(`Could not load the setups (${e.message || e}).`));
    return () => { stop = true; };
  }, [apiFetch]);
  const cur = items && items[sel];
  const spec = cur && meta && meta.triggers[cur.trigger];
  const patch = (fn) => setItems(list => list.map((s, i) => (i === sel ? fn({ ...s }) : s)));
  const save = async (body) => {
    setBusy(true); setMsg(null);
    try {
      const r = await apiFetch("/api/live/setups", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!d.ok) { setMsg(d.error || "Not saved."); return; }
      setItems(d.setups); setSel(0);
      setMsg(d.refused && d.refused.length ? `Saved. Not saved: ${d.refused.join(", ")}.` : "Saved — applies on the next sweep.");
      onSaved && onSaved();
    } catch (e) {
      setMsg(`Not saved (${e.message || e}).`);
    } finally { setBusy(false); }
  };
  if (!items || !meta) {
    return <div className="lv-editor"><p className="lv-empty">{msg || "Loading the setups…"}</p></div>;
  }
  return (
    <div className="lv-editor" role="dialog" aria-label="Setups">
      <div className="lv-ed-head">
        <b>Setups</b>
        <span className="lv-ed-sub">What can fire an alert. Changes apply on the next sweep.</span>
        <button className="lv-x" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      <div className="lv-ed-body">
        <ul className="lv-ed-list">
          {items.map((s, i) => (
            <li key={s.id + i}>
              <button className={`lv-ed-item ${i === sel ? "on" : ""}`} onClick={() => setSel(i)}>
                <span className={`lv-dot ${meta.triggers[s.trigger].side}`} />
                <span className="lv-ed-name">{s.name}</span>
                <span className={`lv-ed-state ${s.enabled ? "on" : ""}`}>{s.enabled ? "On" : "Off"}</span>
              </button>
            </li>
          ))}
        </ul>
        {cur ? (
          <div className="lv-ed-form">
            <label className="lv-field">
              <span>Name</span>
              <input className="lv-input" value={cur.name}
                     onChange={e => patch(s => ({ ...s, name: e.target.value }))} />
            </label>
            <label className="lv-field">
              <span>Trigger</span>
              <select className="lv-input" value={cur.trigger}
                      onChange={e => patch(s => {
                        const t = meta.triggers[e.target.value];
                        const params = {};
                        Object.entries(t.params).forEach(([k, p]) => { params[k] = p.default; });
                        return { ...s, trigger: e.target.value, params };
                      })}>
                {Object.entries(meta.triggers).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
              </select>
            </label>
            <p className="lv-help">{spec.help}</p>
            <div className="lv-grid">
              {Object.entries(spec.params).map(([k, p]) => (
                <label key={k} className="lv-field">
                  <span>{p.label}</span>
                  <input className="lv-input" type="number" step="any" min={p.min} max={p.max}
                         value={cur.params[k] ?? p.default}
                         onChange={e => patch(s => ({ ...s, params: { ...s.params, [k]: e.target.value } }))} />
                </label>
              ))}
              {Object.entries(meta.conditions).map(([k, c]) => (
                <label key={k} className="lv-field">
                  <span>{c.label}</span>
                  <input className="lv-input" type="number" step="any" min={c.min} max={c.max}
                         value={cur.conditions[k] ?? c.default}
                         onChange={e => patch(s => ({ ...s, conditions: { ...s.conditions, [k]: e.target.value } }))} />
                </label>
              ))}
              <label className="lv-field">
                <span>Same stock again after (minutes){spec.once ? " — this one fires once a day" : ""}</span>
                <input className="lv-input" type="number" min="0" max="1440" disabled={spec.once}
                       value={Math.round((cur.cooldown_s || 0) / 60)}
                       onChange={e => patch(s => ({ ...s, cooldown_s: Math.max(0, Number(e.target.value) || 0) * 60 }))} />
              </label>
            </div>
            <div className="lv-ed-toggles">
              <label className="lv-check-box">
                <input type="checkbox" checked={!!cur.enabled}
                       onChange={e => patch(s => ({ ...s, enabled: e.target.checked }))} /> Switched on
              </label>
              <label className="lv-check-box">
                <input type="checkbox" checked={!!cur.notify}
                       onChange={e => patch(s => ({ ...s, notify: e.target.checked }))} /> Push to my phone
              </label>
            </div>
            <div className="lv-ed-actions">
              <button className="research-run-btn" disabled={busy} onClick={() => save({ setups: items })}>
                {busy ? "Saving…" : "Save"}
              </button>
              <button className="lv-btn" onClick={() => {
                setItems(list => list.concat([{ ...cur, id: `${cur.id}-copy`, name: `${cur.name} (copy)` }]));
                setSel(items.length);
              }}>Duplicate</button>
              <button className="lv-btn" disabled={items.length <= 1} onClick={() => {
                setItems(list => list.filter((_, i) => i !== sel)); setSel(0);
              }}>Delete</button>
              <button className="lv-btn" onClick={() => save({ action: "reset" })}>Reset to defaults</button>
            </div>
            {msg ? <p className="lv-msg">{msg}</p> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function LiveScanTab({ apiFetch, onOpenTicker, visible, ticker }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [side, setSide] = useState("all");
  const [setupFilter, setSetupFilter] = useState("");
  const [editing, setEditing] = useState(false);
  const [checking, setChecking] = useState(false);
  const [sound, setSound] = useState(() => {
    try { return localStorage.getItem("jerry_live_sound") === "1"; } catch (_) { return false; }
  });
  const [, tick] = useState(0);
  const seenRef = useRef(null);

  const load = React.useCallback(async () => {
    try {
      const r = await apiFetch("/api/live");
      const d = await r.json();
      if (d && d.error && !d.alerts) { setErr(d.error); return; }
      setErr(null);
      // A beep for alerts that are new since the last look — never for the
      // backlog on first load.
      const newest = d.alerts && d.alerts[0] ? d.alerts[0].ts : 0;
      if (seenRef.current != null && newest > seenRef.current && sound) lvBeep();
      seenRef.current = newest;
      setData(d);
    } catch (e) {
      setErr(`Could not reach the scanner (${e.message || e}).`);
    }
  }, [apiFetch, sound]);

  useEffect(() => {
    if (!visible) return undefined;
    load();
    const id = setInterval(load, 10000);
    const clock = setInterval(() => tick(x => x + 1), 5000);
    return () => { clearInterval(id); clearInterval(clock); };
  }, [visible, load]);

  const toggleSound = () => {
    const v = !sound;
    setSound(v);
    try { localStorage.setItem("jerry_live_sound", v ? "1" : "0"); } catch (_) { /* private mode */ }
    if (v) lvBeep();
  };

  const alerts = ((data && data.alerts) || [])
    .filter(a => side === "all" || a.side === side)
    .filter(a => !setupFilter || a.setup_id === setupFilter);
  const setupNames = {};
  ((data && data.alerts) || []).forEach(a => { setupNames[a.setup_id] = a.setup; });
  const records = (data && data.records) || {};
  const phase = (data && data.phase) || "closed";
  const ago = data && lvAgo(data.last_sweep);

  return (
    <div className="card lv-card">
      <div className="card-head">
        <div>
          <span className="kicker" title={LV_TIP.card}>Live scanner</span>
          <h3 className="card-title">What is moving right now, and why</h3>
        </div>
        <div className="toolbar lv-toolbar">
          <button className={`lv-btn ${sound ? "on" : ""}`} onClick={toggleSound} title={LV_TIP.sound}
                  aria-pressed={sound}>{sound ? "🔔 Sound on" : "🔕 Sound off"}</button>
          <button className={`lv-btn ${checking ? "on" : ""}`} onClick={() => setChecking(v => !v)}
                  title={LV_TIP.check}>Check a stock</button>
          <button className="research-run-btn" onClick={() => setEditing(true)} title={LV_TIP.setups}>Setups</button>
        </div>
      </div>

      <p className={`lv-status lv-ph-${phase}`} title={LV_TIP.status}>
        <span className="lv-pulse" />
        <b>{LV_PHASE[phase] || phase}</b>
        {ago ? <> · checked {ago}</> : null}
        {data && data.universe_n ? <> · {data.quoted_n} of {data.universe_n} names</> : null}
        {data ? <> · {data.alerts_today} alert{data.alerts_today === 1 ? "" : "s"} today</> : null}
        {phase === "closed" || phase === "post"
          ? <span className="lv-status-note"> — runs 4:00 AM to 4:00 PM ET on trading days; today's alerts stay here.</span>
          : null}
      </p>
      {err || (data && data.error) ? <p className="lv-err">{err || data.error}</p> : null}

      {editing ? <LvSetupEditor apiFetch={apiFetch} onClose={() => setEditing(false)} onSaved={load} /> : null}
      {checking ? <LvCheck apiFetch={apiFetch} initial={ticker} /> : null}

      <div className="lv-cols">
        <section className="lv-feed">
          <div className="lv-feed-head">
            <div className="lv-seg" role="tablist" aria-label="Direction">
              {[["all", "All"], ["long", "▲ Long"], ["short", "▼ Short"]].map(([k, l]) => (
                <button key={k} role="tab" aria-selected={side === k}
                        className={`lv-seg-btn ${side === k ? "on" : ""}`} onClick={() => setSide(k)}>{l}</button>
              ))}
            </div>
            <select className="lv-input lv-setup-filter" value={setupFilter} aria-label="Setup"
                    onChange={e => setSetupFilter(e.target.value)}>
              <option value="">Every setup</option>
              {Object.entries(setupNames).map(([id, name]) => (
                <option key={id} value={id}>{name} ({(data.counts || {})[id] || 0})</option>
              ))}
            </select>
          </div>
          {alerts.length ? (
            <ul className="lv-alerts">
              {alerts.map(a => <LvAlertRow key={a.id} a={a} rec={records[a.setup_id]} onOpen={onOpenTicker} />)}
            </ul>
          ) : (
            <p className="lv-empty">
              {!data ? "Loading…"
                : phase === "open" || phase === "pre"
                  ? "No alerts yet today. They appear here as they fire — newest on top."
                  : "No alerts today."}
            </p>
          )}
        </section>
        <section className="lv-side">
          <LvRankings rankings={data && data.rankings} phase={phase} onOpen={onOpenTicker} />
        </section>
      </div>
    </div>
  );
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, { LiveScanTab: React.memo(LiveScanTab) });
