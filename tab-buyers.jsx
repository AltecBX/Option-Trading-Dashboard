// tab-buyers.jsx — LAZY CHUNK (v5.34). INSIDER & CONGRESS BUYING.
//
// Jerry upgraded Unusual Whales and asked for a market-wide feed of who is
// buying with their own money. Two lists and where they meet:
//
//   • company insiders buying on the open market over the last 30 days,
//     grouped by stock, ranked by how many DIFFERENT insiders bought (a
//     cluster is the strongest version) and then by dollars;
//   • members of Congress buying over the last 60 days;
//   • stocks on both lists, shown first.
//
// Endpoint: GET /api/uw/buyers  (smart_buyers.py builds it)

const SB_TIP = {
  card: "Who is putting their own money in. Insiders are officers, directors and big owners buying their company's stock on the open market, reported on Form 4 within two business days. Members of Congress report up to 45 days late and in dollar ranges, so that list is slower and rougher.",
  cluster: "Three or more different insiders bought: several people with inside knowledge agreeing is a stronger sign than one.",
  chief: "The CEO, CFO, president or chair was one of the buyers.",
  both: "On both lists: insiders and members of Congress bought the same stock.",
  watch: "On your watchlist.",
};

const sbMoney = (v) => {
  const a = Math.abs(v || 0);
  return a >= 1e9 ? `$${(a / 1e9).toFixed(1)}B` : a >= 1e6 ? `$${(a / 1e6).toFixed(1)}M`
    : a >= 1e3 ? `$${Math.round(a / 1e3)}K` : `$${Math.round(a)}`;
};
const sbDay = (iso) => {
  if (!iso) return "";
  const d = new Date(`${iso}T12:00:00`);
  return isNaN(d) ? iso : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};
const sbEarningsSoon = (iso) => {
  if (!iso) return null;
  const days = Math.round((new Date(`${iso}T12:00:00`) - new Date()) / 86400000);
  return days >= 0 && days <= 30 ? days : null;
};

function SbSym({ sym, onOpen }) {
  return <button className="lv-sym sb-sym" onClick={() => onOpen && onOpen(sym)}
                 title={`Open ${sym} on the Trade screen`}>{sym}</button>;
}

function SbInsiderRow({ r, onOpen }) {
  const earn = sbEarningsSoon(r.next_earnings);
  return (
    <li className="sb-row">
      <div className="sb-top">
        <SbSym sym={r.symbol} onOpen={onOpen} />
        <span className="sb-amt">{sbMoney(r.value)}</span>
        {r.cluster ? <span className="sb-tag sb-cluster" title={SB_TIP.cluster}>{r.buyers} insiders</span> : null}
        {r.chief ? <span className="sb-tag sb-chief" title={SB_TIP.chief}>Boss bought</span> : null}
        {r.watchlist ? <span className="sb-tag sb-watch" title={SB_TIP.watch}>★ Watchlist</span> : null}
      </div>
      <div className="sb-text">{r.text}</div>
      <div className="sb-meta">
        {r.sector ? <span>{r.sector}</span> : null}
        {earn != null ? <span className="sb-earn">Earnings in {earn} day{earn === 1 ? "" : "s"}</span> : null}
        {(r.people || []).length > 1
          ? <span>{r.people.map(p => `${p.title ? p.title + " " : ""}${p.name} ${sbMoney(p.value)}`).join(" · ")}</span>
          : null}
      </div>
    </li>
  );
}

function SbCongressRow({ r, onOpen }) {
  return (
    <li className="sb-row">
      <div className="sb-top">
        <SbSym sym={r.symbol} onOpen={onOpen} />
        <span className="sb-amt">{r.members} member{r.members === 1 ? "" : "s"}</span>
        {r.watchlist ? <span className="sb-tag sb-watch" title={SB_TIP.watch}>★ Watchlist</span> : null}
      </div>
      <div className="sb-text">{r.text}</div>
      {(r.trades || []).length > 1 ? (
        <div className="sb-meta">
          <span>{r.trades.slice(1).map(t => `${t.name} ${t.amount} ${sbDay(t.date)}`).join(" · ")}</span>
        </div>
      ) : null}
    </li>
  );
}

function BuyersTab({ apiFetch, onOpenTicker, visible }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState("insiders");
  const [mine, setMine] = useState(false);

  const load = React.useCallback(async () => {
    setBusy(true);
    try {
      const r = await apiFetch("/api/uw/buyers");
      const j = await r.json();
      if (j.error) { setErr(j.error); return; }
      if (j.configured === false) { setErr("Unusual Whales is not connected, so there is no buying data."); return; }
      setErr(null);
      if (j.data) setData(j.data);
    } catch (e) {
      setErr(`Could not reach the server (${e.message || e}).`);
    } finally { setBusy(false); }
  }, [apiFetch]);

  useEffect(() => {
    if (!visible) return undefined;
    load();
    // Filings arrive through the day; the server caches for 15 minutes.
    const id = setInterval(load, 15 * 60 * 1000);
    return () => clearInterval(id);
  }, [visible, load]);

  const pick = (rows) => (rows || []).filter(r => !mine || r.watchlist);
  const ins = pick(data && data.insiders);
  const con = pick(data && data.congress);
  const both = pick(data && data.both);
  const missing = (data && data.missing) || [];

  return (
    <div className="card sb-card">
      <div className="card-head">
        <div>
          <span className="kicker" title={SB_TIP.card}>Unusual Whales · insider &amp; Congress buying</span>
          <h3 className="card-title">Who is buying with their own money</h3>
        </div>
        <div className="toolbar">
          <button className="research-run-btn" onClick={load} disabled={busy}>{busy ? "Loading…" : "Refresh"}</button>
        </div>
      </div>

      <div className="sb-bar">
        <div className="lv-seg" role="tablist" aria-label="Which stocks">
          <button role="tab" aria-selected={!mine} className={`lv-seg-btn ${!mine ? "on" : ""}`}
                  onClick={() => setMine(false)}>All stocks</button>
          <button role="tab" aria-selected={mine} className={`lv-seg-btn ${mine ? "on" : ""}`}
                  onClick={() => setMine(true)}>★ My watchlist</button>
        </div>
      </div>

      {err ? <p className="lv-err">{err}</p> : null}
      {!data && !err ? <p className="lv-empty">Loading who is buying…</p> : null}

      {both.length ? (
        <section className="sb-both" title={SB_TIP.both}>
          <div className="sb-h">Insiders and Congress both buying</div>
          <ul className="sb-list">
            {both.map(b => (
              <li key={b.symbol} className="sb-row">
                <div className="sb-top">
                  <SbSym sym={b.symbol} onOpen={onOpenTicker} />
                  {b.watchlist ? <span className="sb-tag sb-watch">★ Watchlist</span> : null}
                </div>
                <div className="sb-text">{b.text} {b.insider.text}</div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {data ? (
        <>
          <div className="lv-seg sb-view" role="tablist" aria-label="Show">
            <button role="tab" aria-selected={view === "insiders"}
                    className={`lv-seg-btn ${view === "insiders" ? "on" : ""}`}
                    onClick={() => setView("insiders")}>Insiders ({ins.length})</button>
            <button role="tab" aria-selected={view === "congress"}
                    className={`lv-seg-btn ${view === "congress" ? "on" : ""}`}
                    onClick={() => setView("congress")}>Congress ({con.length})</button>
          </div>
          {view === "insiders" ? (
            ins.length ? (
              <ul className="sb-list sb-insiders">
                {ins.map(r => <SbInsiderRow key={r.symbol} r={r} onOpen={onOpenTicker} />)}
              </ul>
            ) : <p className="lv-empty">No open-market insider buys in the last {data.insider_days} days{mine ? " on your watchlist" : ""}.</p>
          ) : (
            con.length ? (
              <ul className="sb-list sb-congress">
                {con.map(r => <SbCongressRow key={r.symbol} r={r} onOpen={onOpenTicker} />)}
              </ul>
            ) : <p className="lv-empty">No congressional buys in the last {data.congress_days} days{mine ? " on your watchlist" : ""}.</p>
          )}
          <p className="sb-foot">
            Insiders: open-market purchases only (Form 4, code P), last {data.insider_days} days, ranked by how
            many different people bought. Congress: purchases disclosed in the last {data.congress_days} days;
            members can report up to 45 days late, in dollar ranges.
            {missing.length ? ` Not available right now: ${missing.map(m => m === "insider_buys" ? "insider trades" : "Congress trades").join(", ")}.` : ""}
            {(data.partial || []).length ? ` Only part of the window came back for ${(data.partial || []).map(m => m === "insider_buys" ? "insider trades" : "Congress trades").join(" and ")}, so some stocks may be missing.` : ""}
          </p>
        </>
      ) : null}
    </div>
  );
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, { BuyersTab: React.memo(BuyersTab) });
