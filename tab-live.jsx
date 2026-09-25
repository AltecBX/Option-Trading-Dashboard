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

// A list row's group: Jerry's own watchlist tag when he gave the stock
// one, else a short sector name. Several rows sharing a group on one list
// is a sector moving together, and that is what the column is for (v5.32).
const LV_SECTOR_SHORT = {
  Technology: "Tech", "Information Technology": "Tech",
  "Financial Services": "Financials", Financials: "Financials",
  Healthcare: "Health", "Health Care": "Health", Energy: "Energy",
  "Consumer Cyclical": "Consumer", "Consumer Discretionary": "Consumer",
  "Consumer Defensive": "Staples", "Consumer Staples": "Staples",
  Industrials: "Industrials", "Basic Materials": "Materials", Materials: "Materials",
  Utilities: "Utilities", "Real Estate": "Real Estate",
  "Communication Services": "Comms",
};
const lvGroup = (r) => (r.tag ? String(r.tag)
  : r.sector ? (LV_SECTOR_SHORT[r.sector] || String(r.sector)) : null);
const LV_GROUP_COLOURS = 6;

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
          {long ? "▲" : "▼"}<span className="lv-side-word">{long ? " LONG" : " SHORT"}</span>
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
      <div className="lv-a-foot">
        {/* On a phone the time and the volume ride down here, so the first
            line is only what decides a glance: which way, what, how much. */}
        <span className="lv-foot-meta">
          {a.time}{a.rvol != null ? ` · ${lvNum(a.rvol, 1)}x vol` : ""} ·{" "}
        </span>
        <LvRecord rec={rec} />
      </div>
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
  const pm = cur[0].startsWith("pm_");
  // On the gainers and losers lists the ranked number IS the day's change;
  // a second column repeating it was the empty space the group now uses.
  const dayCol = metric !== (pm ? "pm_change_pct" : "change_pct");
  // Groups that show up more than once on this list, most first. Each gets
  // its own colour, so repeats stand out without reading a word.
  const counts = {};
  rows.forEach(r => { const g = lvGroup(r); if (g) counts[g] = (counts[g] || 0) + 1; });
  const shared = Object.keys(counts).filter(g => counts[g] > 1)
    .sort((a, b) => counts[b] - counts[a]);
  const colour = (g) => { const i = shared.indexOf(g); return i >= 0 && i < LV_GROUP_COLOURS ? i : -1; };
  const [only, setOnly] = useState(null);
  // The filter holds only while its chip is on screen: a later poll can
  // leave the group with one row, its chip gone, and no way back to the
  // full list (Codex, #419).
  const filtering = only != null && shared.includes(only);
  const shown = filtering ? rows.filter(r => lvGroup(r) === only) : rows;
  const pickList = (k) => { setPick(k); setOnly(null); };
  return (
    <div className="lv-ranks" title={LV_TIP.lists}>
      <div className="lv-seg" role="tablist">
        {lists.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={cur[0] === k}
                  className={`lv-seg-btn ${cur[0] === k ? "on" : ""}`}
                  onClick={() => pickList(k)}>{label}</button>
        ))}
      </div>
      {shared.length ? (
        <div className="lv-groups" title="Groups with more than one stock on this list. Tap one to show only those; tap it again to show everything.">
          {shared.slice(0, LV_GROUP_COLOURS).map(g => (
            <button key={g} type="button"
                    className={`lv-grp lv-grp-${colour(g)}${filtering && only === g ? " on" : ""}`}
                    aria-pressed={filtering && only === g}
                    onClick={() => setOnly(filtering && only === g ? null : g)}>
              {g} <b>{counts[g]}</b>
            </button>
          ))}
        </div>
      ) : null}
      {rows.length ? (
        <table className="scan-table lv-rtable">
          <thead>
            <tr>
              <th>#</th><th>Symbol</th>
              <th className="scan-num">Price</th>
              <th className="lv-grp-col">Group</th>
              <th className="scan-num">{cur[1]}</th>
              {dayCol ? <th className="scan-num">Day</th> : null}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const px = r.last != null ? r.last : r.pm_last;
              const day = pm ? r.pm_change_pct : r.change_pct;
              const g = lvGroup(r);
              const c = g ? colour(g) : -1;
              return (
                <tr key={r.symbol}>
                  <td className="lv-rank">{rows.indexOf(r) + 1}</td>
                  <td><button className="lv-sym" onClick={() => onOpen && onOpen(r.symbol)}>{r.symbol}</button></td>
                  <td className="scan-num">{px != null ? `$${lvNum(px)}` : "—"}</td>
                  <td className="lv-grp-col">
                    {g ? <span className={`lv-grp${c >= 0 ? ` lv-grp-${c}` : ""}`}
                               title={(r.tag ? `Your tag: ${r.tag}` : "Sector") +
                                      (r.sector ? ` · ${r.sector}` : "") +
                                      (counts[g] > 1 ? ` · ${counts[g]} on this list` : "")}>{g}</span>
                       : <span className="lv-grp-none">—</span>}
                  </td>
                  <td className="scan-num lv-metric">{fmt(r)}</td>
                  {dayCol ? <td className={`scan-num ${day > 0 ? "up" : day < 0 ? "down" : ""}`}>{lvPct(day)}</td> : null}
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

// Ranked on the server's one status per row, which decides eligibility
// first — never on the raw trigger, which reads "live" for a switched-off
// setup too (Codex, #417).
const LV_CHECK_RANK = { fired: 0, live_blocked: 1, live: 1, blocked: 2, idle: 3, session: 4, off: 5 };
const lvCheckRank = (r) => (r.status in LV_CHECK_RANK ? LV_CHECK_RANK[r.status] : 3);
const LV_CHECK_CLASS = { fired: "fired", live: "live", live_blocked: "live", blocked: "",
                         idle: "idle", session: "idle", off: "idle" };

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
          {/* What happened first: fired, then live, then stopped by a
              condition, then idle (dimmed). The answer to "why didn't it
              alert?" is near the top, not under eleven "not live" rows. */}
          {res.setups.slice().sort((a, b) => lvCheckRank(a) - lvCheckRank(b)).map(r => (
            <li key={r.setup_id} className={`lv-check-row ${LV_CHECK_CLASS[r.status] ?? "idle"}`}>
              <span className="lv-check-name">{r.setup}</span>
              <span className="lv-check-verdict">{r.verdict}</span>
              {r.status === "fired" || r.status === "live" || r.status === "live_blocked"
                ? <span className="lv-check-detail">{r.detail}</span> : null}
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
  const [view, setView] = useState("alerts");
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
          <span className={`lv-mini lv-ph-${phase}`} title={LV_TIP.status}>
            <span className="lv-pulse" /><b>{LV_PHASE[phase] || phase}</b>{ago ? <> · {ago}</> : null}
          </span>
          <button className={`lv-btn lv-sound ${sound ? "on" : ""}`} onClick={toggleSound} title={LV_TIP.sound}
                  aria-pressed={sound} aria-label={sound ? "Sound on" : "Sound off"}>
            {sound ? "🔔" : "🔕"}<span className="lv-wide"> {sound ? "Sound on" : "Sound off"}</span>
          </button>
          <button className={`lv-btn lv-check-btn ${checking ? "on" : ""}`} onClick={() => setChecking(v => !v)}
                  title={LV_TIP.check}>Check<span className="lv-wide"> a stock</span></button>
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

      {/* Phone only: the feed and the lists side by side do not fit, and
          stacked the lists sat 2,000px down, past every alert. One tap
          between them instead. Hidden on a wide screen, where both show. */}
      <div className="lv-view lv-seg" role="tablist" aria-label="Show">
        <button role="tab" aria-selected={view === "alerts"}
                className={`lv-seg-btn ${view === "alerts" ? "on" : ""}`}
                onClick={() => setView("alerts")}>Alerts{data ? ` (${alerts.length})` : ""}</button>
        <button role="tab" aria-selected={view === "lists"}
                className={`lv-seg-btn ${view === "lists" ? "on" : ""}`}
                onClick={() => setView("lists")}>Lists</button>
      </div>

      <div className={`lv-cols lv-show-${view}`}>
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
