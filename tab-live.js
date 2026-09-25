(function () {
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
  lists: "Ranked lists from the same prices. Only stocks over $5 with at least 500K shares a day are listed, so the lists are names you can trade options on."
};
const LV_LISTS = [["gainers", "Gainers", "change_pct"], ["losers", "Losers", "change_pct"], ["active", "Most active", "volume"], ["rvol", "Volume surge", "rvol"], ["movers_5m", "5-min movers", "move_5m"], ["gap_up", "Gap up", "gap_pct"], ["gap_down", "Gap down", "gap_pct"]];
const LV_PM_LISTS = [["pm_gainers", "Pre-market gainers", "pm_change_pct"], ["pm_losers", "Pre-market losers", "pm_change_pct"], ["pm_volume", "Pre-market volume", "pm_volume"]];
const lvNum = (v, d = 2) => v == null || !isFinite(v) ? "—" : Number(v).toFixed(d);
const lvPct = (v, d = 1) => v == null || !isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
const lvVol = v => {
  if (v == null || !isFinite(v)) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(Math.round(v));
};
const lvAgo = iso => {
  if (!iso) return null;
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
};
const LV_PHASE = {
  open: "Live",
  pre: "Pre-market",
  post: "After the close",
  closed: "Market closed"
};
function lvBeep() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = lvBeep.ctx || (lvBeep.ctx = new Ctx());
    const o = ctx.createOscillator(),
      g = ctx.createGain();
    o.frequency.value = 880;
    g.gain.value = 0.06;
    o.connect(g);
    g.connect(ctx.destination);
    o.start();
    o.stop(ctx.currentTime + 0.12);
  } catch (_) {/* no audio: nothing to do */}
}
function LvRecord({
  rec
}) {
  if (!rec || !rec.n) {
    return /*#__PURE__*/React.createElement("span", {
      className: "lv-rec lv-rec-new",
      title: LV_TIP.record
    }, "no record yet");
  }
  const tone = rec.follow_rate >= 55 ? "up" : rec.follow_rate < 45 ? "down" : "";
  return /*#__PURE__*/React.createElement("span", {
    className: `lv-rec ${tone}`,
    title: LV_TIP.record
  }, rec.follow_rate, "% of ", rec.n, " \xB7 ", lvPct(rec.avg_r30, 2));
}
function LvAlertRow({
  a,
  rec,
  onOpen
}) {
  const long = a.side === "long";
  return /*#__PURE__*/React.createElement("li", {
    className: `lv-alert ${long ? "lv-long" : "lv-short"}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "lv-a-top"
  }, /*#__PURE__*/React.createElement("span", {
    className: "lv-time"
  }, a.time), /*#__PURE__*/React.createElement("span", {
    className: `lv-side ${long ? "up" : "down"}`,
    title: LV_TIP.side
  }, long ? "▲" : "▼", /*#__PURE__*/React.createElement("span", {
    className: "lv-side-word"
  }, long ? " LONG" : " SHORT")), /*#__PURE__*/React.createElement("button", {
    className: "lv-sym",
    onClick: () => onOpen && onOpen(a.symbol),
    title: `Open ${a.symbol} on the Trade tab`
  }, a.symbol), /*#__PURE__*/React.createElement("span", {
    className: "lv-setup"
  }, a.setup), /*#__PURE__*/React.createElement("span", {
    className: "lv-a-nums"
  }, /*#__PURE__*/React.createElement("span", {
    className: "lv-price"
  }, a.price != null ? `$${lvNum(a.price)}` : "—"), /*#__PURE__*/React.createElement("span", {
    className: a.change_pct > 0 ? "up" : a.change_pct < 0 ? "down" : ""
  }, lvPct(a.change_pct)), a.rvol != null ? /*#__PURE__*/React.createElement("span", {
    className: "lv-rv",
    title: LV_TIP.rvol
  }, lvNum(a.rvol, 1), "x") : null)), /*#__PURE__*/React.createElement("div", {
    className: "lv-why"
  }, a.why), /*#__PURE__*/React.createElement("div", {
    className: "lv-a-foot"
  }, /*#__PURE__*/React.createElement("span", {
    className: "lv-foot-meta"
  }, a.time, a.rvol != null ? ` · ${lvNum(a.rvol, 1)}x vol` : "", " \xB7", " "), /*#__PURE__*/React.createElement(LvRecord, {
    rec: rec
  })));
}
function LvRankings({
  rankings,
  phase,
  onOpen
}) {
  const lists = phase === "pre" ? LV_PM_LISTS.concat(LV_LISTS) : LV_LISTS.concat(LV_PM_LISTS);
  // null = "the right list for the session": Pre-market gainers before the
  // bell, Gainers after. The screen mounts before the first answer says
  // which phase it is, so a remembered default would stay on the empty
  // regular list all morning (Codex, #416). A tap is kept.
  const [pick, setPick] = useState(null);
  const cur = pick && lists.find(l => l[0] === pick) || lists[0];
  const rows = rankings && rankings[cur[0]] || [];
  const metric = cur[2];
  const fmt = r => metric === "volume" || metric === "pm_volume" ? lvVol(r[metric]) : metric === "rvol" ? `${lvNum(r.rvol, 1)}x` : lvPct(r[metric], 2);
  return /*#__PURE__*/React.createElement("div", {
    className: "lv-ranks",
    title: LV_TIP.lists
  }, /*#__PURE__*/React.createElement("div", {
    className: "lv-seg",
    role: "tablist"
  }, lists.map(([k, label]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    role: "tab",
    "aria-selected": cur[0] === k,
    className: `lv-seg-btn ${cur[0] === k ? "on" : ""}`,
    onClick: () => setPick(k)
  }, label))), rows.length ? /*#__PURE__*/React.createElement("table", {
    className: "scan-table lv-rtable"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "#"), /*#__PURE__*/React.createElement("th", null, "Symbol"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num"
  }, "Price"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num"
  }, cur[1]), /*#__PURE__*/React.createElement("th", {
    className: "scan-num"
  }, "Day"))), /*#__PURE__*/React.createElement("tbody", null, rows.map((r, i) => {
    const px = r.last != null ? r.last : r.pm_last;
    const day = cur[0].startsWith("pm_") ? r.pm_change_pct : r.change_pct;
    return /*#__PURE__*/React.createElement("tr", {
      key: r.symbol
    }, /*#__PURE__*/React.createElement("td", {
      className: "lv-rank"
    }, i + 1), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("button", {
      className: "lv-sym",
      onClick: () => onOpen && onOpen(r.symbol)
    }, r.symbol)), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, px != null ? `$${lvNum(px)}` : "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num lv-metric"
    }, fmt(r)), /*#__PURE__*/React.createElement("td", {
      className: `scan-num ${day > 0 ? "up" : day < 0 ? "down" : ""}`
    }, lvPct(day)));
  }))) : /*#__PURE__*/React.createElement("p", {
    className: "lv-empty"
  }, "Nothing on this list yet", phase === "pre" && !cur[0].startsWith("pm_") ? " — before the bell the regular-session lists are empty; the pre-market lists are live." : "."));
}
const lvCheckRank = r => r.fired_ts ? 0 : r.live ? 1 : r.blocked && r.blocked.length ? 2 : 3;
function LvCheck({
  apiFetch,
  initial
}) {
  const [sym, setSym] = useState(initial || "");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async e => {
    if (e) e.preventDefault();
    const s = sym.trim().toUpperCase();
    if (!s) return;
    setBusy(true);
    try {
      const r = await apiFetch(`/api/live/check?symbol=${encodeURIComponent(s)}`);
      setRes(await r.json());
    } catch (err) {
      setRes({
        symbol: s,
        known: false,
        why: `Could not reach the scanner (${err.message || err}).`
      });
    } finally {
      setBusy(false);
    }
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "lv-check",
    title: LV_TIP.check
  }, /*#__PURE__*/React.createElement("form", {
    className: "lv-check-form",
    onSubmit: run
  }, /*#__PURE__*/React.createElement("input", {
    className: "lv-input",
    value: sym,
    placeholder: "Symbol, e.g. NVDA",
    "aria-label": "Symbol to check",
    onChange: e => setSym(e.target.value)
  }), /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    type: "submit",
    disabled: busy
  }, busy ? "Checking…" : "Check")), res && !res.known ? /*#__PURE__*/React.createElement("p", {
    className: "lv-empty"
  }, res.why) : null, res && res.known ? /*#__PURE__*/React.createElement("ul", {
    className: "lv-check-list"
  }, res.setups.slice().sort((a, b) => lvCheckRank(a) - lvCheckRank(b)).map(r => /*#__PURE__*/React.createElement("li", {
    key: r.setup_id,
    className: `lv-check-row ${r.fired_ts ? "fired" : r.live ? "live" : r.blocked && r.blocked.length ? "" : "idle"}`
  }, /*#__PURE__*/React.createElement("span", {
    className: "lv-check-name"
  }, r.setup), /*#__PURE__*/React.createElement("span", {
    className: "lv-check-verdict"
  }, r.verdict), r.live || r.fired_ts ? /*#__PURE__*/React.createElement("span", {
    className: "lv-check-detail"
  }, r.detail) : null))) : null);
}
function LvSetupEditor({
  apiFetch,
  onClose,
  onSaved
}) {
  const [items, setItems] = useState(null);
  const [meta, setMeta] = useState(null);
  const [sel, setSel] = useState(0);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let stop = false;
    apiFetch("/api/live/setups").then(r => r.json()).then(d => {
      if (stop) return;
      setItems(d.setups || []);
      setMeta(d.meta || null);
    }).catch(e => !stop && setMsg(`Could not load the setups (${e.message || e}).`));
    return () => {
      stop = true;
    };
  }, [apiFetch]);
  const cur = items && items[sel];
  const spec = cur && meta && meta.triggers[cur.trigger];
  const patch = fn => setItems(list => list.map((s, i) => i === sel ? fn({
    ...s
  }) : s));
  const save = async body => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch("/api/live/setups", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(body)
      });
      const d = await r.json();
      if (!d.ok) {
        setMsg(d.error || "Not saved.");
        return;
      }
      setItems(d.setups);
      setSel(0);
      setMsg(d.refused && d.refused.length ? `Saved. Not saved: ${d.refused.join(", ")}.` : "Saved — applies on the next sweep.");
      onSaved && onSaved();
    } catch (e) {
      setMsg(`Not saved (${e.message || e}).`);
    } finally {
      setBusy(false);
    }
  };
  if (!items || !meta) {
    return /*#__PURE__*/React.createElement("div", {
      className: "lv-editor"
    }, /*#__PURE__*/React.createElement("p", {
      className: "lv-empty"
    }, msg || "Loading the setups…"));
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "lv-editor",
    role: "dialog",
    "aria-label": "Setups"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lv-ed-head"
  }, /*#__PURE__*/React.createElement("b", null, "Setups"), /*#__PURE__*/React.createElement("span", {
    className: "lv-ed-sub"
  }, "What can fire an alert. Changes apply on the next sweep."), /*#__PURE__*/React.createElement("button", {
    className: "lv-x",
    "aria-label": "Close",
    onClick: onClose
  }, "\u2715")), /*#__PURE__*/React.createElement("div", {
    className: "lv-ed-body"
  }, /*#__PURE__*/React.createElement("ul", {
    className: "lv-ed-list"
  }, items.map((s, i) => /*#__PURE__*/React.createElement("li", {
    key: s.id + i
  }, /*#__PURE__*/React.createElement("button", {
    className: `lv-ed-item ${i === sel ? "on" : ""}`,
    onClick: () => setSel(i)
  }, /*#__PURE__*/React.createElement("span", {
    className: `lv-dot ${meta.triggers[s.trigger].side}`
  }), /*#__PURE__*/React.createElement("span", {
    className: "lv-ed-name"
  }, s.name), /*#__PURE__*/React.createElement("span", {
    className: `lv-ed-state ${s.enabled ? "on" : ""}`
  }, s.enabled ? "On" : "Off"))))), cur ? /*#__PURE__*/React.createElement("div", {
    className: "lv-ed-form"
  }, /*#__PURE__*/React.createElement("label", {
    className: "lv-field"
  }, /*#__PURE__*/React.createElement("span", null, "Name"), /*#__PURE__*/React.createElement("input", {
    className: "lv-input",
    value: cur.name,
    onChange: e => patch(s => ({
      ...s,
      name: e.target.value
    }))
  })), /*#__PURE__*/React.createElement("label", {
    className: "lv-field"
  }, /*#__PURE__*/React.createElement("span", null, "Trigger"), /*#__PURE__*/React.createElement("select", {
    className: "lv-input",
    value: cur.trigger,
    onChange: e => patch(s => {
      const t = meta.triggers[e.target.value];
      const params = {};
      Object.entries(t.params).forEach(([k, p]) => {
        params[k] = p.default;
      });
      return {
        ...s,
        trigger: e.target.value,
        params
      };
    })
  }, Object.entries(meta.triggers).map(([k, t]) => /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, t.label)))), /*#__PURE__*/React.createElement("p", {
    className: "lv-help"
  }, spec.help), /*#__PURE__*/React.createElement("div", {
    className: "lv-grid"
  }, Object.entries(spec.params).map(([k, p]) => /*#__PURE__*/React.createElement("label", {
    key: k,
    className: "lv-field"
  }, /*#__PURE__*/React.createElement("span", null, p.label), /*#__PURE__*/React.createElement("input", {
    className: "lv-input",
    type: "number",
    step: "any",
    min: p.min,
    max: p.max,
    value: cur.params[k] ?? p.default,
    onChange: e => patch(s => ({
      ...s,
      params: {
        ...s.params,
        [k]: e.target.value
      }
    }))
  }))), Object.entries(meta.conditions).map(([k, c]) => /*#__PURE__*/React.createElement("label", {
    key: k,
    className: "lv-field"
  }, /*#__PURE__*/React.createElement("span", null, c.label), /*#__PURE__*/React.createElement("input", {
    className: "lv-input",
    type: "number",
    step: "any",
    min: c.min,
    max: c.max,
    value: cur.conditions[k] ?? c.default,
    onChange: e => patch(s => ({
      ...s,
      conditions: {
        ...s.conditions,
        [k]: e.target.value
      }
    }))
  }))), /*#__PURE__*/React.createElement("label", {
    className: "lv-field"
  }, /*#__PURE__*/React.createElement("span", null, "Same stock again after (minutes)", spec.once ? " — this one fires once a day" : ""), /*#__PURE__*/React.createElement("input", {
    className: "lv-input",
    type: "number",
    min: "0",
    max: "1440",
    disabled: spec.once,
    value: Math.round((cur.cooldown_s || 0) / 60),
    onChange: e => patch(s => ({
      ...s,
      cooldown_s: Math.max(0, Number(e.target.value) || 0) * 60
    }))
  }))), /*#__PURE__*/React.createElement("div", {
    className: "lv-ed-toggles"
  }, /*#__PURE__*/React.createElement("label", {
    className: "lv-check-box"
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!cur.enabled,
    onChange: e => patch(s => ({
      ...s,
      enabled: e.target.checked
    }))
  }), " Switched on"), /*#__PURE__*/React.createElement("label", {
    className: "lv-check-box"
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!cur.notify,
    onChange: e => patch(s => ({
      ...s,
      notify: e.target.checked
    }))
  }), " Push to my phone")), /*#__PURE__*/React.createElement("div", {
    className: "lv-ed-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    disabled: busy,
    onClick: () => save({
      setups: items
    })
  }, busy ? "Saving…" : "Save"), /*#__PURE__*/React.createElement("button", {
    className: "lv-btn",
    onClick: () => {
      setItems(list => list.concat([{
        ...cur,
        id: `${cur.id}-copy`,
        name: `${cur.name} (copy)`
      }]));
      setSel(items.length);
    }
  }, "Duplicate"), /*#__PURE__*/React.createElement("button", {
    className: "lv-btn",
    disabled: items.length <= 1,
    onClick: () => {
      setItems(list => list.filter((_, i) => i !== sel));
      setSel(0);
    }
  }, "Delete"), /*#__PURE__*/React.createElement("button", {
    className: "lv-btn",
    onClick: () => save({
      action: "reset"
    })
  }, "Reset to defaults")), msg ? /*#__PURE__*/React.createElement("p", {
    className: "lv-msg"
  }, msg) : null) : null));
}
function LiveScanTab({
  apiFetch,
  onOpenTicker,
  visible,
  ticker
}) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [side, setSide] = useState("all");
  const [setupFilter, setSetupFilter] = useState("");
  const [editing, setEditing] = useState(false);
  const [checking, setChecking] = useState(false);
  const [view, setView] = useState("alerts");
  const [sound, setSound] = useState(() => {
    try {
      return localStorage.getItem("jerry_live_sound") === "1";
    } catch (_) {
      return false;
    }
  });
  const [, tick] = useState(0);
  const seenRef = useRef(null);
  const load = React.useCallback(async () => {
    try {
      const r = await apiFetch("/api/live");
      const d = await r.json();
      if (d && d.error && !d.alerts) {
        setErr(d.error);
        return;
      }
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
    return () => {
      clearInterval(id);
      clearInterval(clock);
    };
  }, [visible, load]);
  const toggleSound = () => {
    const v = !sound;
    setSound(v);
    try {
      localStorage.setItem("jerry_live_sound", v ? "1" : "0");
    } catch (_) {/* private mode */}
    if (v) lvBeep();
  };
  const alerts = (data && data.alerts || []).filter(a => side === "all" || a.side === side).filter(a => !setupFilter || a.setup_id === setupFilter);
  const setupNames = {};
  (data && data.alerts || []).forEach(a => {
    setupNames[a.setup_id] = a.setup;
  });
  const records = data && data.records || {};
  const phase = data && data.phase || "closed";
  const ago = data && lvAgo(data.last_sweep);
  return /*#__PURE__*/React.createElement("div", {
    className: "card lv-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: LV_TIP.card
  }, "Live scanner"), /*#__PURE__*/React.createElement("h3", {
    className: "card-title"
  }, "What is moving right now, and why")), /*#__PURE__*/React.createElement("div", {
    className: "toolbar lv-toolbar"
  }, /*#__PURE__*/React.createElement("span", {
    className: `lv-mini lv-ph-${phase}`,
    title: LV_TIP.status
  }, /*#__PURE__*/React.createElement("span", {
    className: "lv-pulse"
  }), /*#__PURE__*/React.createElement("b", null, LV_PHASE[phase] || phase), ago ? /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 ", ago) : null), /*#__PURE__*/React.createElement("button", {
    className: `lv-btn lv-sound ${sound ? "on" : ""}`,
    onClick: toggleSound,
    title: LV_TIP.sound,
    "aria-pressed": sound,
    "aria-label": sound ? "Sound on" : "Sound off"
  }, sound ? "🔔" : "🔕", /*#__PURE__*/React.createElement("span", {
    className: "lv-wide"
  }, " ", sound ? "Sound on" : "Sound off")), /*#__PURE__*/React.createElement("button", {
    className: `lv-btn lv-check-btn ${checking ? "on" : ""}`,
    onClick: () => setChecking(v => !v),
    title: LV_TIP.check
  }, "Check", /*#__PURE__*/React.createElement("span", {
    className: "lv-wide"
  }, " a stock")), /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: () => setEditing(true),
    title: LV_TIP.setups
  }, "Setups"))), /*#__PURE__*/React.createElement("p", {
    className: `lv-status lv-ph-${phase}`,
    title: LV_TIP.status
  }, /*#__PURE__*/React.createElement("span", {
    className: "lv-pulse"
  }), /*#__PURE__*/React.createElement("b", null, LV_PHASE[phase] || phase), ago ? /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 checked ", ago) : null, data && data.universe_n ? /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 ", data.quoted_n, " of ", data.universe_n, " names") : null, data ? /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 ", data.alerts_today, " alert", data.alerts_today === 1 ? "" : "s", " today") : null, phase === "closed" || phase === "post" ? /*#__PURE__*/React.createElement("span", {
    className: "lv-status-note"
  }, " \u2014 runs 4:00 AM to 4:00 PM ET on trading days; today's alerts stay here.") : null), err || data && data.error ? /*#__PURE__*/React.createElement("p", {
    className: "lv-err"
  }, err || data.error) : null, editing ? /*#__PURE__*/React.createElement(LvSetupEditor, {
    apiFetch: apiFetch,
    onClose: () => setEditing(false),
    onSaved: load
  }) : null, checking ? /*#__PURE__*/React.createElement(LvCheck, {
    apiFetch: apiFetch,
    initial: ticker
  }) : null, /*#__PURE__*/React.createElement("div", {
    className: "lv-view lv-seg",
    role: "tablist",
    "aria-label": "Show"
  }, /*#__PURE__*/React.createElement("button", {
    role: "tab",
    "aria-selected": view === "alerts",
    className: `lv-seg-btn ${view === "alerts" ? "on" : ""}`,
    onClick: () => setView("alerts")
  }, "Alerts", data ? ` (${alerts.length})` : ""), /*#__PURE__*/React.createElement("button", {
    role: "tab",
    "aria-selected": view === "lists",
    className: `lv-seg-btn ${view === "lists" ? "on" : ""}`,
    onClick: () => setView("lists")
  }, "Lists")), /*#__PURE__*/React.createElement("div", {
    className: `lv-cols lv-show-${view}`
  }, /*#__PURE__*/React.createElement("section", {
    className: "lv-feed"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lv-feed-head"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lv-seg",
    role: "tablist",
    "aria-label": "Direction"
  }, [["all", "All"], ["long", "▲ Long"], ["short", "▼ Short"]].map(([k, l]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    role: "tab",
    "aria-selected": side === k,
    className: `lv-seg-btn ${side === k ? "on" : ""}`,
    onClick: () => setSide(k)
  }, l))), /*#__PURE__*/React.createElement("select", {
    className: "lv-input lv-setup-filter",
    value: setupFilter,
    "aria-label": "Setup",
    onChange: e => setSetupFilter(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "Every setup"), Object.entries(setupNames).map(([id, name]) => /*#__PURE__*/React.createElement("option", {
    key: id,
    value: id
  }, name, " (", (data.counts || {})[id] || 0, ")")))), alerts.length ? /*#__PURE__*/React.createElement("ul", {
    className: "lv-alerts"
  }, alerts.map(a => /*#__PURE__*/React.createElement(LvAlertRow, {
    key: a.id,
    a: a,
    rec: records[a.setup_id],
    onOpen: onOpenTicker
  }))) : /*#__PURE__*/React.createElement("p", {
    className: "lv-empty"
  }, !data ? "Loading…" : phase === "open" || phase === "pre" ? "No alerts yet today. They appear here as they fire — newest on top." : "No alerts today.")), /*#__PURE__*/React.createElement("section", {
    className: "lv-side"
  }, /*#__PURE__*/React.createElement(LvRankings, {
    rankings: data && data.rankings,
    phase: phase,
    onOpen: onOpenTicker
  }))));
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  LiveScanTab: React.memo(LiveScanTab)
});
})();
