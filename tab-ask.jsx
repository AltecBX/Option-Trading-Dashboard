// tab-ask.jsx — LAZY CHUNK (v4.00). "Ask AI": type what you want in plain
// English → the server translates it into the app's rule language (OpenAI
// key when set; the strict grammar otherwise) → review the EXACT
// interpretation → scan the latest bar, hand off to the Backtest Lab, or
// save it as a push alert. The AI only translates — every number is
// validated server-side and shown here before anything runs, and nothing
// in this tab ever places a trade.

const ASK_EXAMPLES = [
  "My AI names down 15% from highs, RSI under 40, back above the 20 day average",
  "Stocks on my list making a new 50 day low on double volume",
  "Backtest selling 30 delta puts 45 dte on my starred names, take profit 50%, exit 21 dte, skip earnings — last 3 years",
  "Alert me when anything starred drops 4% in a day with volume 2x average",
  "Gapped down 2% but reclaimed the open, SPY in an uptrend",
  "Show me DELL",
];

const ASK_HIST_KEY = "jt_ask_history_v1";

function askHistLoad() {
  try { return JSON.parse(localStorage.getItem(ASK_HIST_KEY) || "[]"); }
  catch (e) { return []; }
}
function askHistPush(t) {
  try {
    const h = askHistLoad().filter(x => x !== t);
    h.unshift(t);
    localStorage.setItem(ASK_HIST_KEY, JSON.stringify(h.slice(0, 12)));
  } catch (e) { /* no-op */ }
}

// Per-condition ✓/✗/– chips with the measured value ("RSI 27", "-18.2%") so
// every match can be sanity-checked at a glance.
function AskChecks({ checks }) {
  return (
    <span className="ask-checks">
      {(checks || []).map((c, i) => (
        <span key={i}
              className={`ask-chk ${c.ok === true ? "ok" : c.ok === false ? "no" : "na"}`}
              title={`${c.label} — ${c.ok === true ? "passes" : c.ok === false ? "fails" : "not enough history to evaluate"}${c.value ? ` (measured: ${c.value})` : ""}`}>
          {c.ok === true ? "✓" : c.ok === false ? "✗" : "–"} {c.value || c.label}
        </span>
      ))}
    </span>
  );
}

function AskResultsTable({ rows, onOpenTicker, emptyNote }) {
  if (!rows || !rows.length) return emptyNote ? <div className="ask-empty">{emptyNote}</div> : null;
  return (
    <div className="ask-tbl-wrap">
      <table className="ask-tbl">
        <thead><tr>
          <th title="Tap a symbol to open it on the Trade tab.">Ticker</th>
          <th title="Last close used by the scan.">Price</th>
          <th title="Change on the scanned day.">Day</th>
          <th title="Each condition with its measured value: ✓ passes, ✗ fails, – not enough history.">Conditions</th>
        </tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.symbol}>
              <td><button className="ask-sym" onClick={() => onOpenTicker && onOpenTicker(r.symbol)}
                          title={`Open ${r.symbol} on the Trade tab (as of ${r.date}).`}>{r.symbol}</button></td>
              <td>${r.price}</td>
              <td className={r.chg_pct >= 0 ? "up" : "down"}>{r.chg_pct != null ? `${r.chg_pct >= 0 ? "+" : ""}${r.chg_pct}%` : "—"}</td>
              <td>{r.missed
                ? <span className="ask-missed" title="This name passes every condition except one.">only missing: {r.missed}</span>
                : <AskChecks checks={r.checks} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// The interpretation card — restate + assumptions + anything unsupported.
// This is the trust surface: what runs is exactly what this card says.
function AskInterpretation({ res }) {
  if (!res) return null;
  const srcChip = res.source === "ai"
    ? { txt: `AI · ${res.model || "OpenAI"}`, cls: "ai", tip: "Translated by your OpenAI key, then validated and clamped by the app. Only your words and your tag names were sent." }
    : res.source === "cache"
      ? { txt: "AI (cached)", cls: "ai", tip: "Same request seen before — served from the server's parse cache, no API call." }
      : { txt: "strict parser", cls: "gr", tip: "Translated by the app's built-in deterministic grammar (no AI key involved)." };
  return (
    <div className="ask-interp">
      <div className="ask-restate">
        <span className={`ask-src ${srcChip.cls}`} title={srcChip.tip}>{srcChip.txt}</span>
        <span className={`ask-intent ask-intent-${res.intent}`}
              title="What kind of action this was understood as.">{res.intent}</span>
        <b title="The app's own read-back of the validated rules — built from what will actually run, not from the AI's prose.">{res.restate || "—"}</b>
      </div>
      {(res.assumptions || []).length > 0 && (
        <div className="ask-assume" title="Defaults the translator chose for fuzzy wording. Refine below if any is wrong.">
          {res.assumptions.map((a, i) => <div key={i}>• assumed: {a}</div>)}
        </div>
      )}
      {(res.warnings || []).length > 0 && (
        <div className="ask-warns">
          {res.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
        </div>
      )}
      {(res.unsupported || []).length > 0 && (
        <div className="ask-unsup" title="Clauses that cannot run on this app's data — listed instead of silently guessed.">
          {res.unsupported.map((u, i) => <div key={i}>✎ can’t run: “{u.text}” — {u.reason}</div>)}
        </div>
      )}
    </div>
  );
}

function AskSavedList({ apiFetch, nonce, onRunSaved, onBumped }) {
  const [items, setItems] = useState([]);
  const [busyId, setBusyId] = useState(null);
  useEffect(() => {
    apiFetch("/api/nl/strategies").then(r => r.json())
      .then(d => setItems(d.items || [])).catch(() => {});
  }, [nonce]);
  if (!items.length) return null;
  const post = (body) =>
    apiFetch("/api/nl/strategies", { method: "POST", body: JSON.stringify(body) })
      .then(r => r.json()).then(() => onBumped && onBumped());
  return (
    <div className="card ask-saved">
      <div className="card-head"><div>
        <div className="kicker" title="Every idea you save — rerun it in one tap, or turn on its bell to get a push when a NEW symbol starts matching (checked once after each close; needs Pushover or ntfy configured on the server).">Saved scans & alerts</div>
      </div></div>
      {items.map(it => (
        <div key={it.id} className="ask-saved-row">
          <button className={`ask-bell ${it.alert && it.alert.enabled ? "on" : ""}`}
                  title={it.alert && it.alert.enabled
                    ? "Alert ON — you get a push when a new symbol matches (once per symbol per 5 days). Tap to turn off."
                    : "Alert OFF — tap to get a push when a new symbol starts matching this scan."}
                  onClick={() => post({ op: "update", id: it.id, alert_enabled: !(it.alert && it.alert.enabled) })}>
            {it.alert && it.alert.enabled ? "🔔" : "🔕"}
          </button>
          <div className="ask-saved-main">
            <div className="ask-saved-name">{it.name}</div>
            <div className="ask-saved-sub">
              {it.restate || it.text}
              {it.last_run && it.last_run.at
                ? ` · last run ${String(it.last_run.at).slice(0, 16).replace("T", " ")}: ${it.last_run.n_matches} match${it.last_run.n_matches === 1 ? "" : "es"}${(it.last_run.matched || []).length ? ` (${it.last_run.matched.slice(0, 6).join(", ")}${it.last_run.matched.length > 6 ? "…" : ""})` : ""}`
                : ""}
            </div>
          </div>
          <button className="rr-btn" disabled={busyId === it.id}
                  title="Run this saved scan now against the latest data."
                  onClick={() => { setBusyId(it.id); Promise.resolve(onRunSaved(it)).finally(() => setBusyId(null)); }}>
            {busyId === it.id ? "…" : "Run"}</button>
          <button className="rr-btn ask-del" title="Delete this saved scan."
                  onClick={() => { if (confirm(`Delete “${it.name}”?`)) post({ op: "delete", id: it.id }); }}>✕</button>
        </div>
      ))}
    </div>
  );
}

function AskTab({ apiFetch, onOpenTicker, onOpenBacktest }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);          // last translate result
  const [askedText, setAskedText] = useState(""); // text that produced res
  const [refine, setRefine] = useState("");
  const [err, setErr] = useState(null);
  const [status, setStatus] = useState(null);    // /api/nl/status
  const [scanning, setScanning] = useState(false);
  const [scanProg, setScanProg] = useState(null);
  const [board, setBoard] = useState(null);
  const [saveMsg, setSaveMsg] = useState(null);
  const [nonce, setNonce] = useState(0);
  const [hist, setHist] = useState(askHistLoad);
  const pollRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    apiFetch("/api/nl/status").then(r => r.json()).then(setStatus).catch(() => {});
    // Resume: if a scan is mid-flight from a previous visit, re-attach.
    apiFetch("/api/nl/board").then(r => r.json()).then(d => {
      if (d && d.status && d.status.scanning) startPolling(null);
      else if (d && d.board && d.board.as_of) setBoard(d.board);
    }).catch(() => {});
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const startPolling = (sid) => {
    setScanning(true);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => {
      apiFetch(`/api/nl/board${sid ? `?strategy_id=${encodeURIComponent(sid)}` : ""}`)
        .then(r => r.json())
        .then(d => {
          const st = (d && d.status) || {};
          if (st.scanning) { setScanProg(st); return; }
          clearInterval(pollRef.current); pollRef.current = null;
          setScanning(false); setScanProg(null);
          if (st.error) setErr(`Scan failed: ${st.error}`);
          else if (d.board && d.board.as_of) { setBoard(d.board); setErr(null); }
        })
        .catch(() => {});
    }, 2500);
  };

  const translate = (t, baseRules) => {
    const q = (t || "").trim();
    if (!q || busy) return;
    setBusy(true); setErr(null); setSaveMsg(null);
    if (!baseRules) setBoard(null);
    apiFetch("/api/nl/translate", {
      method: "POST",
      body: JSON.stringify(baseRules ? { text: q, base_rules: baseRules } : { text: q }),
    })
      .then(r => r.json())
      .then(d => {
        setBusy(false);
        if (d.error) { setErr(d.error); return; }
        setRes(d); setAskedText(baseRules ? askedText : q); setRefine("");
        askHistPush(baseRules ? `${askedText} → ${q}` : q);
        setHist(askHistLoad());
      })
      .catch(e => { setBusy(false); setErr(String(e)); });
  };

  const runScan = (rules, sid) => {
    setErr(null); setBoard(null); setScanProg(null);
    apiFetch("/api/nl/scan", {
      method: "POST",
      body: JSON.stringify(sid ? { strategy_id: sid } : { rules }),
    })
      .then(r => r.json())
      .then(d => {
        if (d.started) startPolling(sid || null);
        else setErr(d.reason || d.error || "could not start the scan");
      })
      .catch(e => setErr(String(e)));
  };

  const saveStrategy = (withAlert) => {
    if (!res) return;
    const name = (res.restate || askedText).slice(0, 60);
    apiFetch("/api/nl/strategies", {
      method: "POST",
      body: JSON.stringify({ op: "save", name, text: askedText, rules: res.rules,
                             intent: res.intent, restate: res.restate,
                             alert: !!withAlert }),
    })
      .then(r => r.json())
      .then(d => {
        if (d.error) { setErr(d.error); return; }
        setNonce(n => n + 1);
        setSaveMsg(withAlert
          ? "Saved with the alert ON — you'll get a push when a NEW symbol matches (checked after each close)."
          : "Saved — find it under Saved scans & alerts below.");
      })
      .catch(e => setErr(String(e)));
  };

  const openBacktest = () => {
    if (res && res.rules && onOpenBacktest) onOpenBacktest(res.rules, askedText);
  };

  const aiOn = status && status.ai;
  const canScan = res && (res.intent === "scan" || res.intent === "alert")
    && (res.rules.entry || []).length > 0;
  const tagChips = ((status && status.tags) || []).slice(0, 8);

  return (
    <div className="ask-tab">
      <div className="card ask-card">
        <div className="card-head"><div>
          <div className="kicker" title="Describe a scan, a backtest, or an alert in your own words. The app translates it into explicit rules, shows you EXACTLY how it understood you, and only then runs it. The AI translates only — all evaluation happens in this app, and nothing here ever places a trade.">Ask AI</div>
          <h2 title="Examples: find stocks matching conditions now · backtest an idea over years of history · get a push alert when something starts matching.">Describe it. I’ll build it.</h2>
        </div></div>

        <div className="ask-inputrow">
          <textarea ref={inputRef} className="ask-input" rows={2} value={text} spellCheck={false}
                    placeholder='e.g. "my AI names down 15% from highs turning back up" — scans, backtests, or alerts'
                    onChange={e => setText(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); translate(text); } }}
                    title="Plain English. Vocabulary: gaps, day moves, drawdowns from highs, RSI, moving averages & crosses, new highs/lows, volume vs average, consecutive days, price filters, SPY regime, option structures (CSPs, covered calls, strangles, condors, spreads, wheel) with delta/DTE/management, your watchlist tags, symbol lists, alerts. Enter = go." />
          <button className="rr-btn ask-go" disabled={busy || !text.trim()} onClick={() => translate(text)}
                  title="Translate the text into explicit rules. Nothing runs until you review them.">
            {busy ? "…" : "Go →"}
          </button>
        </div>

        <div className="ask-exrow">
          {(hist.length ? hist.slice(0, 3) : []).map((h, i) => (
            <button key={"h" + i} className="ask-ex hist" onClick={() => setText(h.split(" → ")[0])}
                    title={`Recent: ${h}`}>↺ {h.length > 42 ? h.slice(0, 42) + "…" : h}</button>
          ))}
          {ASK_EXAMPLES.map((ex, i) => (
            <button key={i} className="ask-ex" onClick={() => setText(ex)} title={ex}>
              {ex.length > 46 ? ex.slice(0, 46) + "…" : ex}
            </button>
          ))}
        </div>

        {tagChips.length > 0 && (
          <div className="ask-tags" title="Your watchlist tags (from the Manage tab's CSV import) — say “my <tag> names” to scan just that group.">
            your groups: {tagChips.map(t => (
              <button key={t} className="ask-tag" onClick={() => setText(`my ${t} names `)}
                      title={`Start a scan of your “${t}” group.`}>{t}</button>
            ))}{(status.tags || []).length > 8 ? ` +${status.tags.length - 8} more` : ""}
          </div>
        )}

        {err && <div className="bt-warn bt-err">{err}</div>}

        {res && (
          <div className="ask-result">
            <AskInterpretation res={res} />

            <div className="ask-actions">
              {canScan && (
                <button className="rr-btn ask-primary" disabled={scanning} onClick={() => runScan(res.rules)}
                        title="Evaluate every condition on the latest daily bar across the chosen universe. Uses the SAME condition code as the Backtest Lab.">
                  {scanning ? "Scanning…" : "Scan now"}
                </button>
              )}
              {res.intent !== "chart" && res.intent !== "help" && (
                <button className="rr-btn" onClick={openBacktest}
                        title="Send these exact rules to the Backtest Lab — years of history, walk-forward validation, Monte Carlo, honest warnings.">
                  Backtest in the Lab →
                </button>
              )}
              {canScan && (
                <>
                  <button className="rr-btn" onClick={() => saveStrategy(false)}
                          title="Keep this scan in your library to rerun any time.">Save</button>
                  <button className="rr-btn" onClick={() => saveStrategy(true)}
                          title="Save AND enable a push alert: after each market close, if a NEW symbol matches, you get a notification (Pushover/ntfy).">
                    Save + alert 🔔
                  </button>
                </>
              )}
              {res.intent === "chart" && (res.symbols || []).map(s => (
                <button key={s} className="rr-btn ask-primary" onClick={() => onOpenTicker && onOpenTicker(s)}
                        title={`Open ${s} on the Trade tab.`}>Open {s} →</button>
              ))}
            </div>
            {saveMsg && <div className="ask-savemsg">{saveMsg}</div>}

            {res.intent === "help" && (
              <div className="ask-help">
                <b>Things you can ask for:</b>
                <div>• <b>Scans</b> — “stocks on my list down 20% from highs with RSI under 35” → a live table, with near-misses.</div>
                <div>• <b>Backtests</b> — “backtest buying 3 straight down days, exit +5% or 10 days, last 5 years” → the full Lab with validation.</div>
                <div>• <b>Options structures</b> — “sell 20 delta strangles 45 dte, take profit 50%, exit 21 dte, skip earnings”.</div>
                <div>• <b>Alerts</b> — “alert me when anything starred makes a new 50-day low” → a push after the close when a new name matches.</div>
                <div>• <b>Groups</b> — use your Manage-tab tags: “my Gold-Metals names…”.</div>
                <div>• <b>Charts</b> — “show me DELL”.</div>
                <div className="ask-help-note">Not supported (and never silently faked): news, IV rank history, analyst ratings, fundamentals, option flow as scan conditions.</div>
              </div>
            )}

            <div className="ask-refinerow">
              <input className="ask-refine" value={refine} spellCheck={false}
                     placeholder='adjust it: e.g. "RSI 25 instead, starred names only"'
                     onChange={e => setRefine(e.target.value)}
                     onKeyDown={e => { if (e.key === "Enter") translate(refine, res.rules); }}
                     title="Modify the current rules in plain English — everything you don't mention stays the same." />
              <button className="rr-btn" disabled={busy || !refine.trim()} onClick={() => translate(refine, res.rules)}>refine</button>
            </div>
          </div>
        )}

        {scanning && (
          <div className="ask-scanprog" title="Scanning symbol by symbol — one data request at a time, no rate-limit bursts.">
            Scanning… {scanProg && scanProg.total ? `${scanProg.done}/${scanProg.total}` : ""} {scanProg && scanProg.symbol ? `· ${scanProg.symbol}` : ""}
          </div>
        )}

        {board && !scanning && (
          <div className="ask-board">
            <div className="ask-board-head"
                 title={`Scanned ${board.n_scanned} of ${board.n_universe} symbols (${board.n_no_data || 0} had no data) in ${board.universe} as of ${board.as_of}. Conditions: ${(board.conditions || []).join("; ")}`}>
              <b>{(board.matches || []).length}</b> match{(board.matches || []).length === 1 ? "" : "es"} in {board.universe}
              <span className="ask-asof"> · {String(board.as_of || "").slice(0, 16).replace("T", " ")}</span>
            </div>
            {board.note && <div className="ask-warns">⚠ {board.note}</div>}
            <AskResultsTable rows={board.matches} onOpenTicker={onOpenTicker}
                             emptyNote="No symbol passes every condition right now — the near-misses below are the closest." />
            {(board.near_misses || []).length > 0 && (
              <>
                <div className="ask-nm-title" title="Names passing every condition except ONE — often tomorrow's matches.">Near misses (one condition away)</div>
                <AskResultsTable rows={board.near_misses} onOpenTicker={onOpenTicker} />
              </>
            )}
          </div>
        )}

        <div className="ask-foot">
          {status === null ? "" : aiOn
            ? <span title="Plain-English translation is live through your OpenAI key. Only your words and your tag names are sent — never positions, journal or account data. Each translation costs a fraction of a cent and identical requests are cached.">🟢 AI translation on · {status.model}{status.last_ai_error ? ` · last error: ${status.last_ai_error}` : ""}</span>
            : status.key_set
              ? <span title="A key is set but the server reports AI unavailable (offline mode?).">🟡 AI key set but unavailable{status.last_ai_error ? ` — ${status.last_ai_error}` : ""}</span>
              : <span title="Without a key the strict pattern-matching parser still works — it just needs more exact wording. Add OPENAI_API_KEY as a Railway environment variable to unlock free-form English.">⚪ AI off — strict parser mode. Add OPENAI_API_KEY on Railway for full plain-English.</span>}
        </div>
      </div>

      <AskSavedList apiFetch={apiFetch} nonce={nonce}
                    onBumped={() => setNonce(n => n + 1)}
                    onRunSaved={(it) => { setRes(null); setBoard(null); runScan(null, it.id); }} />
    </div>
  );
}

// Chunk registration (house pattern — verify_frontend checks this).
window.AskTab = AskTab;
