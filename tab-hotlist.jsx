// tab-hotlist.jsx — LAZY CHUNK (v5.36). THE OPTIONS HOTLIST.
//
// Two market-wide scans from Jerry's Unusual Whales plan, one tap apart:
//   • Hottest contracts: where the biggest money traded in options today,
//     and whether it printed at the ask (bought) or the bid (sold);
//   • Expensive options: names whose volatility is unusually rich, a
//     seller's shortlist; Cheap options: unusually cheap, a poor place to
//     sell.
//
// Endpoint: GET /api/uw/hotlist  (hotlist.py builds it)

const HL_TIP = {
  card: "Market-wide, from Unusual Whales. Hottest contracts are ordered by the dollars traded today. Expensive and cheap options come from UW's volatility-anomaly screen: options priced unusually high or low against the stock's own normal.",
  hottest: "The option contracts with the most money traded today. BOUGHT means most of it traded at the ask (buyers paying up); SOLD means at the bid.",
  rich: "Options unusually expensive against their own normal: premium sellers are being paid more than usual. Check the stock on Worth Selling Today or the Big Money Map before selling.",
  cheap: "Options unusually cheap against their own normal: selling premium here pays less than usual.",
};
const HL_VIEWS = [["hottest", "Hottest contracts"], ["rich", "Expensive options"], ["cheap", "Cheap options"]];
const hlMoney = (v) => {
  const a = Math.abs(v || 0);
  return a >= 1e9 ? `$${(a / 1e9).toFixed(1)}B` : a >= 1e6 ? `$${(a / 1e6).toFixed(1)}M`
    : a >= 1e3 ? `$${Math.round(a / 1e3)}K` : `$${Math.round(a)}`;
};

function HotlistTab({ apiFetch, onOpenTicker, visible }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState("hottest");
  const [mine, setMine] = useState(false);

  const load = React.useCallback(async () => {
    setBusy(true);
    try {
      const r = await apiFetch("/api/uw/hotlist");
      const j = await r.json();
      if (j.error) { setErr(j.error); return; }
      if (j.configured === false) { setErr("Unusual Whales is not connected, so there is no hotlist."); return; }
      setErr(null);
      if (j.data) setData(j.data);
    } catch (e) {
      setErr(`Could not reach the server (${e.message || e}).`);
    } finally { setBusy(false); }
  }, [apiFetch]);

  useEffect(() => {
    if (!visible) return undefined;
    load();
    const id = setInterval(load, 2 * 60 * 1000);
    return () => clearInterval(id);
  }, [visible, load]);

  const pick = (rows) => (rows || []).filter(r => !mine || r.watchlist);
  const rows = pick(data && data[view]);
  const missing = (data && data.missing) || [];
  const counts = { hottest: pick(data && data.hottest).length, rich: pick(data && data.rich).length,
                   cheap: pick(data && data.cheap).length };

  return (
    <div className="card sb-card hl-card">
      <div className="card-head">
        <div>
          <span className="kicker" title={HL_TIP.card}>Unusual Whales · market-wide options</span>
          <h3 className="card-title">Where the options action is</h3>
        </div>
        <div className="toolbar">
          <button className="research-run-btn" onClick={load} disabled={busy}>{busy ? "Loading…" : "Refresh"}</button>
        </div>
      </div>

      <div className="sb-bar">
        <div className="lv-seg hl-view" role="tablist" aria-label="Show">
          {HL_VIEWS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={view === k} title={HL_TIP[k]}
                    className={`lv-seg-btn ${view === k ? "on" : ""}`}
                    onClick={() => setView(k)}>{label}{data ? ` (${counts[k]})` : ""}</button>
          ))}
        </div>
        <div className="lv-seg" role="tablist" aria-label="Which stocks">
          <button role="tab" aria-selected={!mine} className={`lv-seg-btn ${!mine ? "on" : ""}`}
                  onClick={() => setMine(false)}>All</button>
          <button role="tab" aria-selected={mine} className={`lv-seg-btn ${mine ? "on" : ""}`}
                  onClick={() => setMine(true)}>★ My watchlist</button>
        </div>
      </div>

      {err ? <p className="lv-err">{err}</p> : null}
      {!data && !err ? <p className="lv-empty">Loading the hotlist…</p> : null}

      {data ? (
        rows.length ? (
          <ul className={`sb-list hl-list hl-${view}`}>
            {rows.map((r, i) => (
              <li key={`${r.option_symbol || r.symbol}-${i}`} className="sb-row">
                <div className="sb-top">
                  <button className="lv-sym sb-sym" onClick={() => onOpenTicker && onOpenTicker(r.symbol)}
                          title={`Open ${r.symbol} on the Trade screen`}>{r.symbol}</button>
                  {view === "hottest" ? (
                    <>
                      <span className="sb-amt">{hlMoney(r.premium)}</span>
                      <span className={`sb-tag hl-how hl-${r.how}`}>
                        {r.how === "bought" ? "BOUGHT" : r.how === "sold" ? "SOLD" : "MIXED"} {r.right}s
                      </span>
                      {r.lean ? <span className={`mm-lean ${r.lean}`}>{r.lean === "bullish" ? "▲ Bullish" : "▼ Bearish"}</span> : null}
                    </>
                  ) : (
                    <span className={`sb-tag hl-${view}`}>{view === "rich" ? "EXPENSIVE" : "CHEAP"}</span>
                  )}
                  {r.watchlist ? <span className="sb-tag sb-watch">★ Watchlist</span> : null}
                </div>
                <div className="sb-text">{r.text}</div>
              </li>
            ))}
          </ul>
        ) : <p className="lv-empty">Nothing on this list right now{mine ? " from your watchlist" : ""}.</p>
      ) : null}

      {data ? (
        <p className="sb-foot">
          Hottest contracts: ordered by dollars traded today; only contracts with at least 200 traded are listed.
          Expensive and cheap: Unusual Whales' volatility-anomaly screen.
          {missing.length ? ` Not available right now: ${missing.map(m => m === "hottest_chains" ? "hottest contracts" : m === "short_vol" ? "expensive options" : "cheap options").join(", ")}.` : ""}
        </p>
      ) : null}
    </div>
  );
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, { HotlistTab: React.memo(HotlistTab) });
