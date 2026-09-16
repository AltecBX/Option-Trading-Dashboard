// tab-stretch.jsx — LAZY CHUNK (v5.16). AT THE LINE.
//
// The workflow this replaces, done by eye one ticker at a time: open
// Analyze, see where the stock's weekly highs usually land above Friday's
// close, wait for the price to get there, then sell a call above it — and
// the mirror image with puts after a drop. This board does the waiting for
// the whole watchlist, both sides, on two horizons (the week from last
// week's close; the day from yesterday's close), and prices every strike
// beyond the line against what happened AFTER comparable crossings on the
// stock's own record. READY means a real contract cleared the gates.
// CROSSED means the stock is there and nothing pays — and says why.
//
// The alert goes to the phone once per symbol, side and expiry whether or
// not this tab is open; the board is where the reasoning lives.
//
// Endpoints: GET /api/stretch · /api/stretch/detail?symbol= · /status

const ST_TIP = {
  card: "Stocks that have reached their usual high or low — the dashed line the Analyze chart draws — and what the calls above or the puts below pay. The line is this stock's own median weekly high/low from last week's close (or median daily high/low from yesterday's close), not a fixed percentage.",
  state: "READY: the stock is past its line AND a listed contract cleared the risk limit and the fill gates. CROSSED: past its line, but nothing on the chain pays for the measured risk — the row says why. A crossed line is a candidate, never a recommendation by itself.",
  side: "Call after a move UP through the usual high; put after a move DOWN through the usual low. Each side is measured on its own crossings — the put numbers are not the call numbers with the sign flipped.",
  horizon: "WEEK: the move is measured from the prior week's last close and the trade is this week's last listed expiry. DAY: the move is from the prior session's close and the trade is a same-day expiry — only the Monday/Wednesday/Friday names have one.",
  expiration: "The expiry being sold. For the week it is the last listed expiry of this week (usually Friday); for the day it is today.",
  move: "How far the stock has moved from its anchor, in percent and in its own sigma. The anchor is named on the row: last week's close for the week, yesterday's close for the day.",
  line: "Its usual high or low from that anchor — the median of the last N weeks (or days), the same dashed line the Analyze chart draws. Half of the past windows reached it and half did not. It is the trigger, not a ceiling: the record beside it says how far past it the stock has gone.",
  strike: "The strike picked, and how far it sits from the anchor in percent — the number to compare with the line and with the record.",
  delta: "The option's delta: the market's own price-based odds of finishing in the money. Shown so you can see which delta bucket the pick landed in — the measured column beside it is what actually happened after crossings like this one.",
  credit: "The mid of the quote. The bid is in the detail; a resting sell order is only promised the bid.",
  itm: "THE RISK NUMBER. Of the comparable crossings on record, the share that FINISHED through a strike this far beyond the level — measured from the crossing bar on, never from the start of the week. This is what assigns you. The gate refuses anything above the limit in the footer.",
  touch: "The share of comparable crossings that TRADED at the strike at some point before expiry, whether or not they finished there. Always at least the finished-through rate. A touch is what you feel mid-week; the close is what settles.",
  grade: "MEASURED: 20 or more comparable crossings on this stock's own record. MOSTLY POOLED / POOLED: its own record is thin and other watchlist names' crossings, in each stock's own sigma, are answering. The count is the total behind the number.",
  since: "When this setup first became READY today. NEW marks rows that turned READY on the latest scan.",
  basis: "Which past crossings count as comparable: the same number of sessions left when the sample is deep enough; otherwise every crossing with at least that many sessions left — those had MORE room to run, so the risk shown is an upper bound.",
  closed_back: "After crossing the line, how often the window CLOSED back inside it. This is the belief the workflow rests on — the week usually closes below its high — measured rather than assumed. It is near a coin flip on most names.",
  beyond: "How much further past the line the stock typically travelled after crossing (the median), and the level nine in ten stayed within. The strike wants to sit beyond the second number, not the first.",
  near: "Names within reach of a line — at least three quarters of the way there — but not across it. Nothing is priced until it crosses.",
  refused: "Names that crossed a line and did not make the board, each with its reason. “No option expires” is the calendar, not the scanner: most stocks list options only on Fridays. “Options too thin to trade” means fewer than five strikes on that side had a real bid, a fillable spread and open interest — nobody is there to fill the order — and the name is skipped for five days. Earnings inside the trade, a takeover headline, thin evidence and a failed gate each say so.",
  no_trade: "Nothing is READY, and the sentence says which kind of nothing: the market is closed, nothing has crossed a line yet, or names crossed and nothing paid. Most sessions are quiet; a board that always has something on it is not measuring anything.",
  alerts: "Pushes sent today. One per symbol, side and expiry, remembered across restarts, sent only when the credit clears the floor — and only if a push channel (ntfy or Pushover) is configured on the server.",
  limits: "The gates in force: the finished-through limit and the delta range scanned. Both live in thresholds.json under “stretch” and are research choices, not proven constants.",
  pool: "How many crossings from other watchlist names are on file to answer for a thin record, in sigma so a quiet name and a wild one are comparable.",
  vs_monday: "The like-for-like check of the workflow itself: a 20-delta-equivalent strike sold Monday morning from last week's close, versus the same rule applied after the stock reaches its line. Measured on the bars alone — premium is not on the bars and is not invented.",
  scanning: "The watchman is running. It re-reads the board every few minutes while the market is open, whether or not this tab is open.",
  stale: "The market is closed, or the scan has not run recently.",
  day_upper: "For a same-day expiry the whole remaining day is charged to the seller — daily bars cannot say what time the line was crossed. Sold into strength, above, scales that by the session clock; this board does not.",
};

const stNum = (v, d = 2) => (v == null || !isFinite(v) ? "—" : Number(v).toFixed(d));
const stPct = (v, d = 1) => (v == null || !isFinite(v) ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
const stPct0 = (v, d = 0) => (v == null || !isFinite(v) ? "—" : `${Number(v).toFixed(d)}%`);
const stMoney = (v, d = 2) => (v == null || !isFinite(v) ? "—" : `$${Number(v).toFixed(d)}`);
const stSig = (v) => (v == null || !isFinite(v) ? "—" : `${Number(v).toFixed(2)}σ`);
const stDate = (s) => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s)
    : d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
};
const stTime = (s) => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s)
    : d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
};
const stRowKey = (r) => r.key || `${r.symbol}|${r.horizon}|${r.side}|${r.expiration}`;
const ST_FILTER_KEY = "jt.stretch.filters.v1";

const ST_PHASE_VERDICT = { pre: "Pre-market", post: "Market closed", holiday: "Market closed", weekend: "Market closed" };

async function stReadJson(r) {
  const text = await r.text();
  try { return { d: JSON.parse(text) }; }
  catch (_e) {
    return r.ok
      ? { d: null, err: "The app's sign-in page answered instead of data. Reload to sign back in." }
      : { d: null, err: "The server answered with an error page instead of data." };
  }
}

// label, key, tooltip, formatter, numeric
const ST_COLS = [
  ["State", "state", "state", (r) => r.state.toUpperCase(), false],
  ["Symbol", "symbol", "card", null, false],
  ["Side", "side", "side", (r) => r.side, false],
  ["Horizon", "horizon", "horizon", (r) => r.horizon, false],
  ["Expiry", "expiration", "expiration", (r) => stDate(r.expiration), false],
  ["Move", "move_pct", "move", (r) => `${stPct(r.move_pct)} · ${stSig(r.move_sigma)}`, true],
  ["Its line", "line_pct", "line", (r) => `${stPct(r.line_pct)} · rec ${stPct(r.record_pct * 100, 0)}`, true],
  ["Strike", "strike", "strike", (r) => (r.strike == null ? "—" : `${stNum(r.strike, 2)} · ${stPct(r.strike_pct)}`), true],
  ["Delta", "delta", "delta", (r) => (r.delta == null ? "—" : `${Math.abs(r.delta).toFixed(2)}Δ`), true],
  ["Credit", "credit", "credit", (r) => stMoney(r.credit), true],
  ["Finished through", "itm_pct", "itm", (r) => stPct0(r.itm_pct), true],
  ["Touched", "touch_pct", "touch", (r) => stPct0(r.touch_pct), true],
  ["Evidence", "grade", "grade", (r) => (r.grade ? `${r.grade} · ${r.n}` : "—"), false],
  ["Since", "first_seen", "since", (r) => stTime(r.first_seen), false],
];
const ST_ASC = new Set(["state", "symbol", "side", "horizon", "expiration", "itm_pct", "touch_pct", "first_seen"]);
const ST_MOBILE = new Set(["state", "symbol", "side", "expiration", "move_pct", "strike", "credit", "itm_pct", "grade"]);

function StLadder({ rows, side, limit }) {
  if (!rows || !rows.length) return null;
  return (
    <table className="sl-mini st-ladder">
      <thead>
        <tr>
          <th title={ST_TIP.strike}>Strike</th>
          <th className="scan-num" title={ST_TIP.delta}>Δ</th>
          <th className="scan-num" title={ST_TIP.credit}>Credit</th>
          <th className="scan-num" title={ST_TIP.itm}>Finished through</th>
          <th className="scan-num" title={ST_TIP.touch}>Touched</th>
          <th className="scan-num" title="Average result of selling this strike over the comparable crossings, on the collateral it ties up, annualised">EV / yr</th>
          <th title="Fill quality: spread and open interest">Fill</th>
          <th title="Why this strike was refused, if it was">Gate</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((x) => (
          <tr key={x.strike} className={x.gate ? "st-gated" : ""}>
            <td>{stNum(x.strike, 2)} {side}</td>
            <td className="scan-num">{x.delta == null ? "—" : Math.abs(x.delta).toFixed(2)}</td>
            <td className="scan-num">{stMoney(x.credit)}</td>
            <td className="scan-num">{stPct0(x.itm_pct)}{x.itm_pct != null && limit != null && x.itm_pct > limit * 100 ? " ▲" : ""}</td>
            <td className="scan-num">{stPct0(x.touch_pct)}</td>
            <td className="scan-num">{x.ev_ann_pct == null ? "—" : stPct(x.ev_ann_pct, 0)}</td>
            <td>{x.liq_grade || "—"}</td>
            <td className="sl-muted">{x.gate || "clears"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StDetail({ r, detail, limits, onClose }) {
  const ref = ((detail && detail.refused) || []).filter((x) => x.horizon === r.horizon && x.side === r.side);
  const vs = detail && detail.profile && detail.profile.vs_monday && detail.profile.vs_monday[r.side];
  const ready = r.state === "ready";
  return (
    <div className="sk-detail-wrap st-detail-wrap">
      <div className="sk-detail-head">
        <b>{r.symbol} {r.side}</b> · {r.horizon} · {stDate(r.expiration)}
        {ready ? <span> · {stNum(r.strike, 2)} {r.side}</span> : null}
        <button className="su-more-btn sk-close" onClick={onClose} title="Collapse">Close</button>
      </div>
      <div className="sk-detail">
        <div className="sl-block">
          <div className="sl-block-title">Where it is</div>
          <p>
            {r.symbol} is <b>{stPct(r.move_pct)}</b> from {r.anchor_label} ({stMoney(r.anchor)}),
            which is <b>{stSig(r.move_sigma)}</b> for a stock running at {stPct0(r.sigma_annual * 100, 0)} annualised.
            Its usual {r.side === "call" ? "high" : "low"} on this horizon is <b>{stPct(r.line_pct)}</b>{" "}
            ({stMoney(r.line_price)}) over {r.n_windows} {r.horizon === "week" ? "weeks" : "days"};
            the record is {stPct(r.record_pct * 100, 1)} — context, not a ceiling.
          </p>
          {r.n != null ? (
            <p title={ST_TIP.basis}>
              After <b>{r.n}</b> comparable crossings ({r.grade}{r.n_pool ? `: ${r.n_own} its own, ${r.n_pool} pooled` : ""};{" "}
              {r.basis}), the {r.horizon} closed back inside the line{" "}
              <b>{stPct0((r.p_closed_back || 0) * 100)}</b> of the time. The typical further travel was{" "}
              <b>{stPct(r.median_beyond_pct)}</b>; nine in ten stayed within <b>{stPct(r.p90_beyond_pct)}</b>.
              {r.clamped ? " This move is below the first row of the measured grid, so the nearest row was used." : ""}
            </p>
          ) : null}
          {ready ? (
            <p title={ST_TIP.itm}>
              The <b>{stNum(r.strike, 2)} {r.side}</b> ({Math.abs(r.delta || 0).toFixed(2)}Δ, {stPct(r.strike_pct)} from the anchor)
              pays <b>{stMoney(r.credit)}</b> at the mid ({stMoney(r.bid)} bid). Comparable crossings finished through a strike
              this far out <b>{stPct0(r.itm_pct)}</b> of the time and touched it {stPct0(r.touch_pct)} of the time.
              {r.horizon === "day" ? " The whole remaining day is charged here — see the note on same-day expiries." : ""}
            </p>
          ) : (
            <p><b>Not ready:</b> {(r.why || []).join("; ")}</p>
          )}
          {ready && r.why && r.why.length ? (
            <ul className="sl-muted">{r.why.map((w, i) => <li key={i}>{w}</li>)}</ul>
          ) : null}
        </div>
        {r.ladder && r.ladder.length ? (
          <div className="sl-block">
            <div className="sl-block-title" title={ST_TIP.itm}>
              Every strike in the delta range, against the same crossings
              {limits ? ` — the limit is ${Math.round(limits.max_itm * 100)}% finished through` : ""}
            </div>
            <StLadder rows={r.ladder} side={r.side} limit={limits && limits.max_itm} />
          </div>
        ) : null}
        {vs ? (
          <div className="sl-block" title={ST_TIP.vs_monday}>
            <div className="sl-block-title">Waiting for the line versus selling Monday morning</div>
            <p>
              Same 20-delta-equivalent rule, two entries, on {vs.weeks} weeks of this stock's bars.
              Sold Monday from last week's close: strike sat {stPct(vs.monday.strike_from_anchor_pct * 100, 1)} away,
              finished through {stPct0(vs.monday.finished_through * 100)}, touched {stPct0(vs.monday.touched * 100)}.
              Sold after the line: strike sat <b>{stPct(vs.line.strike_from_anchor_pct * 100, 1)}</b> away,
              finished through {stPct0(vs.line.finished_through * 100)}, touched {stPct0(vs.line.touched * 100)} —
              and the line was reached in {stPct0(vs.opportunity * 100)} of weeks, typically by {vs.typical_cross_day}.
              Premium is not on the bars: the further strike is what you are paid for.
            </p>
          </div>
        ) : null}
        <div className="sl-block">
          <div className="sl-block-title">What would make this wrong</div>
          <ul>
            <li>The line is not the high. After crossings like this the {r.horizon} closed back inside it only{" "}
              {stPct0((r.p_closed_back || 0) * 100)} of the time; the seller is paid for the strike being beyond the
              further travel, not for a reversal.</li>
            <li>The crossing bar is charged in full. Daily bars cannot see whether today's high came before or after
              the line was reached, so the measured risk is an upper bound — and for a same-day expiry the whole day is charged.</li>
            <li>A headline after you are short. Takeover spikes are refused and a scheduled earnings date inside the
              trade is refused; nothing catches an unscheduled one.</li>
            <li>Pooled evidence is other names' behaviour in this stock's sigma. It is a less specific answer, and it is labelled.</li>
          </ul>
        </div>
        {ref.length ? (
          <div className="sl-block" title={ST_TIP.refused}>
            <div className="sl-block-title">Refused on {r.symbol}</div>
            <ul>{ref.slice(0, 8).map((x, i) => <li key={i}>{(x.why || []).join("; ")}</li>)}</ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function stStatusFacts(data) {
  return (
    <React.Fragment>
      <span title={ST_TIP.stale}>{data.as_of ? `Scanned ${stTime(data.as_of)}` : "No scan yet"}</span>
      {data.scanning ? <span className="sl-live" title={ST_TIP.scanning}> · watching</span> : null}
      <span title={ST_TIP.near}> · {data.n_crossed} of {data.universe} names past a line, {(data.near || []).length} within reach</span>
      {data.warming ? <span> · {data.warming} still being measured</span> : null}
      <span title={ST_TIP.alerts}> · {data.alerts && data.alerts.configured
        ? `${data.alerts.pushed_today} alert${data.alerts.pushed_today === 1 ? "" : "s"} sent today`
        : "no push channel configured"}</span>
      {data.limits ? (
        <span title={ST_TIP.limits}> · limit {Math.round(data.limits.max_itm * 100)}% finished through,{" "}
          {data.limits.min_delta.toFixed(2)}–{data.limits.max_delta.toFixed(2)}Δ</span>
      ) : null}
      {data.pool ? (
        <span title={ST_TIP.pool}> · pool {Object.values(data.pool).reduce((a, s) => a + Object.values(s).reduce((b, n) => b + n, 0), 0).toLocaleString()} crossings</span>
      ) : null}
      <span> · {data.version}</span>
    </React.Fragment>
  );
}

function StretchCard({ apiFetch, onPickTicker }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sortK, setSortK] = useState("state");
  const [sortD, setSortD] = useState(1);
  const [open, setOpen] = useState(null);
  const [details, setDetails] = useState({});
  const [showNear, setShowNear] = useState(false);
  const [showRefused, setShowRefused] = useState(false);
  const [filters, setFilters] = useState(() => {
    try { return { side: "both", horizon: "both", ...(JSON.parse(localStorage.getItem(ST_FILTER_KEY) || "{}")) }; }
    catch (_e) { return { side: "both", horizon: "both" }; }
  });
  const seq = useRef(0);

  const setFilter = (k, v) => setFilters((f) => {
    const next = { ...f, [k]: v };
    try { localStorage.setItem(ST_FILTER_KEY, JSON.stringify(next)); } catch (_e) { /* per-viewer convenience only */ }
    return next;
  });

  const load = React.useCallback(async () => {
    const mine = ++seq.current;
    setBusy(true);
    try {
      const r = await apiFetch("/api/stretch");
      const { d, err: pageErr } = await stReadJson(r);
      if (mine !== seq.current) return;
      if (d == null) { setData(null); setErr(pageErr); return; }
      setData(d);
      setErr(d.error || null);
    } catch (e) {
      if (mine === seq.current) setErr(String(e && e.message ? e.message : e));
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }, [apiFetch]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!(data && data.market_open)) return;
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [data && data.market_open, load]);

  const rows = useMemo(() => ((data && data.rows) || []).filter((r) =>
    (filters.side === "both" || r.side === filters.side)
    && (filters.horizon === "both" || r.horizon === filters.horizon)), [data, filters]);
  // Order stays put while you read: state first, then the size of the move.
  const sorted = useMemo(() => {
    const key = (r) => {
      if (sortK === "state") return r.state === "ready" ? 0 : 1;
      const v = r[sortK];
      if (v == null) return sortD > 0 ? Infinity : -Infinity;
      return typeof v === "string" ? v.toLowerCase() : v;
    };
    return rows.slice().sort((a, b) => {
      const ka = key(a), kb = key(b);
      const c = (ka < kb ? -1 : ka > kb ? 1 : 0) * sortD;
      return c !== 0 ? c : (b.move_sigma || 0) - (a.move_sigma || 0);
    });
  }, [rows, sortK, sortD]);
  const openRow = open ? sorted.find((r) => stRowKey(r) === open) || null : null;
  const nNew = rows.filter((r) => r.is_new).length;

  const th = (label, k, tipKey, numeric) => (
    <th key={k} className={numeric ? "scan-th-num" : ""} title={ST_TIP[tipKey]}
        style={{ cursor: "pointer" }}
        onClick={() => {
          if (sortK === k) setSortD((x) => -x);
          else { setSortK(k); setSortD(ST_ASC.has(k) ? 1 : -1); }
        }}>
      {label}{sortK === k ? (sortD < 0 ? " ↓" : " ↑") : ""}
    </th>
  );

  const toggle = async (r) => {
    const k = stRowKey(r);
    if (open === k) { setOpen(null); return; }
    setOpen(k);
    if (!details[r.symbol]) {
      try {
        const resp = await apiFetch(`/api/stretch/detail?symbol=${encodeURIComponent(r.symbol)}`);
        const { d } = await stReadJson(resp);
        if (d) setDetails((p) => ({ ...p, [r.symbol]: d }));
      } catch (_e) { /* the row still opens on what the board carries */ }
    }
  };

  const near = (data && data.near) || [];
  const refused = (data && data.refused) || [];
  const seg = (k, opts) => (
    <div className="st-seg" role="group">
      {opts.map(([v, label]) => (
        <button key={v} className={`su-more-btn ${filters[k] === v ? "st-seg-on" : ""}`}
                aria-pressed={filters[k] === v} onClick={() => setFilter(k, v)}>{label}</button>
      ))}
    </div>
  );

  return (
    <div className="card sk-card st-card">
      <div className="card-head">
        <div>
          <span className="kicker" title={ST_TIP.card}>At the line</span>
          <h3 className="card-title">Reached its usual high or low — what the calls above and puts below pay</h3>
        </div>
        <div className="toolbar st-toolbar">
          {seg("side", [["both", "Both"], ["call", "Calls"], ["put", "Puts"]])}
          {seg("horizon", [["both", "Week + day"], ["week", "This week"], ["day", "Today"]])}
          <button className="research-run-btn" onClick={load} disabled={busy} title="Re-read the board">
            {busy ? "Reading…" : "Refresh"}
          </button>
        </div>
      </div>

      {data && !err && data.no_trade ? (
        <PanelVerdict tone={data.market_open ? "stop" : "neutral"}
                      verdict={ST_PHASE_VERDICT[data.phase] || "Nothing ready"}
                      reason={data.no_trade_reason}
                      at={data.as_of}
                      tip={ST_TIP.no_trade} />
      ) : null}

      {data ? (
        data.no_trade ? (
          <details className="panel-method sl-status-fold">
            <summary title={ST_TIP.stale}>
              <DataStatus kind={data.scanning ? "loading" : data.as_of ? "cached" : "pending"}
                          at={data.as_of}
                          note="The watchman re-reads the board every few minutes while the market is open, whether or not this tab is open." />
              {data.as_of ? `Scanned ${stTime(data.as_of)}` : "No scan yet"}
              {data.scanning ? " · watching" : ""}
            </summary>
            <div className="panel-method-body">{stStatusFacts(data)}</div>
          </details>
        ) : (
          <p className="sl-status">
            <DataStatus kind={data.scanning ? "loading" : data.as_of ? "cached" : "pending"}
                        at={data.as_of}
                        note="The watchman re-reads the board every few minutes while the market is open, whether or not this tab is open." />
            {stStatusFacts(data)}
            {nNew ? <span className="st-new-chip" title={ST_TIP.since}> · {nNew} new</span> : null}
          </p>
        )
      ) : null}

      <PanelMethod label="Method — what the line is, what the evidence is, and what is refused">
        <p>
          A stock's line is its own median weekly high (or low) from last week's close — the dashed
          line on the Analyze chart — and its median daily high (or low) from yesterday's close. When
          the price crosses one, every strike beyond it is priced against what happened AFTER
          comparable crossings on that stock's record: the first bar that reached the level, and only
          what followed. Finished-through is what assigns you; touched is what you feel. Delta is the
          market's price, shown beside the measured number, never in place of it.
        </p>
        <p title={ST_TIP.day_upper}>
          Daily bars cannot see the time of day a line was crossed, so the crossing bar is charged in
          full and a same-day expiry is charged the whole day — an upper bound on the risk. Takeover
          headlines and earnings inside the trade are refused, and so is a chain with nobody there:
          a strike without a real bid, a fillable spread and open interest is never priced, and a
          name with fewer than five such strikes is skipped for five days. Names with thin records
          borrow the pool, in sigma, and are labelled POOLED.
        </p>
      </PanelMethod>

      {busy && !data ? (
        <div className="st-loading" aria-busy="true">
          <div className="skel skel-line" style={{ width: "40%" }} />
          <div className="skel skel-line" style={{ width: "88%" }} />
        </div>
      ) : null}

      {err ? (
        <React.Fragment>
          <div className="research-error">{err}</div>
          <button className="card-error-btn st-retry" onClick={load}>Try again</button>
        </React.Fragment>
      ) : null}

      {sorted.length ? (
        <div className="scan-table-wrap sk-table-wrap">
          <table className="scan-table mtable sk-table st-table">
            <thead>
              <tr>{ST_COLS.map(([l, k, t, _f, n]) => th(l, k, t, n))}</tr>
            </thead>
            <tbody>
              {sorted.map((r) => {
                const k = stRowKey(r);
                return (
                  <tr key={k} className={`scan-row sk-row st-row st-${r.state} ${open === k ? "scan-row-active" : ""}`}
                      onClick={() => toggle(r)}
                      title="Click for the reasoning, every strike in range, and what was refused on this name">
                    {ST_COLS.map(([label, ck, tipKey, f, numeric]) => (
                      <td key={ck} data-label={label} title={ST_TIP[tipKey]}
                          className={`${numeric ? "scan-num" : ""} ${ST_MOBILE.has(ck) ? "" : "sk-m-hide"}`}>
                        {ck === "symbol" ? (
                          <button className="su-blink"
                                  onClick={(e) => { e.stopPropagation(); onPickTicker && onPickTicker(r.symbol); }}
                                  title={`Load ${r.symbol}`}>{r.symbol}</button>
                        ) : ck === "state" ? (
                          <span className={`st-state st-state-${r.state}`}>{f(r)}{r.is_new ? <span className="st-new-chip"> NEW</span> : null}</span>
                        ) : f(r)}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (data && !data.no_trade && rows.length === 0 ? (
        <p className="sl-muted">Nothing matches the filters; {data.n_ready} ready on the other side or horizon.</p>
      ) : null)}

      {openRow ? (
        <StDetail r={openRow} detail={details[openRow.symbol]} limits={data && data.limits}
                  onClose={() => setOpen(null)} />
      ) : null}

      {data ? (
        <div className="su-more">
          <button className="su-more-btn" aria-expanded={showNear} title={ST_TIP.near}
                  onClick={() => setShowNear((v) => !v)}>
            {showNear ? "Hide" : "Show"} within reach ({near.length})
          </button>
          {showNear ? (
            near.length ? (
              <table className="sl-mini">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th title={ST_TIP.horizon}>Horizon</th>
                    <th title={ST_TIP.side}>Side</th>
                    <th className="scan-num" title={ST_TIP.move}>Move</th>
                    <th className="scan-num" title={ST_TIP.line}>Its line</th>
                    <th className="scan-num" title="How far along the way to the line">Of the way</th>
                  </tr>
                </thead>
                <tbody>
                  {near.map((c, i) => (
                    <tr key={i}>
                      <td>{c.symbol}</td>
                      <td>{c.horizon}</td>
                      <td>{c.side}</td>
                      <td className="scan-num">{stPct(c.move_pct)}</td>
                      <td className="scan-num">{stPct(c.line_pct)}</td>
                      <td className="scan-num">{stPct0(c.fraction * 100)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="sl-muted">Nothing is within reach of a line.</p>
          ) : null}
          <button className="su-more-btn" aria-expanded={showRefused} title={ST_TIP.refused}
                  onClick={() => setShowRefused((v) => !v)}>
            {showRefused ? "Hide" : "Show"} what was refused ({refused.length})
          </button>
          {showRefused ? (
            refused.length ? (
              <table className="sl-mini sl-failed">
                <thead>
                  <tr><th>Symbol</th><th>Setup</th><th title={ST_TIP.refused}>Why</th></tr>
                </thead>
                <tbody>
                  {refused.map((x, i) => (
                    <tr key={i}>
                      <td>{x.symbol}</td>
                      <td>{x.horizon ? `${x.horizon} ${x.side}` : x.gate}</td>
                      <td>{(x.why || []).join(" · ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="sl-muted">Nothing was refused.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// ── Analyze: what happened after this stock reached its line ────────────────
// The chart already draws the dashed median high and low. This is the
// sentence the chart cannot say: how often the stock got there, and what
// it did next — on its own record, with the count — plus the like-for-like
// check against selling Monday morning.
function StretchLineCard({ apiFetch, ticker }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const seq = useRef(0);
  useEffect(() => {
    if (!ticker) return;
    const mine = ++seq.current;
    setData(null); setErr(null);
    (async () => {
      try {
        const r = await apiFetch(`/api/stretch/profile?symbol=${encodeURIComponent(ticker)}`);
        const { d, err: pageErr } = await stReadJson(r);
        if (mine !== seq.current) return;
        if (d == null) { setErr(pageErr); return; }
        if (!d.ok) { setErr(d.error || "no profile"); return; }
        setData(d);
      } catch (e) {
        if (mine === seq.current) setErr(String(e && e.message ? e.message : e));
      }
    })();
  }, [apiFetch, ticker]);

  const side = (s) => {
    const a = data && data.after_the_line && data.after_the_line[s];
    const vs = data && data.vs_monday && data.vs_monday[s];
    if (!a) return null;
    const up = s === "call";
    return (
      <div className="sl-block st-side-block" key={s}>
        <div className="sl-block-title" style={{ color: up ? "var(--up)" : "var(--down)" }}>
          {up ? "Usual high" : "Usual low"} · {stPct(a.line.pct * 100)} from last week's close ({stMoney(a.line.price)})
        </div>
        <p title={ST_TIP.closed_back}>
          Reached in <b>{vs ? stPct0(vs.opportunity * 100) : "—"}</b> of {a.line.n} weeks
          {vs && vs.typical_cross_day ? `, typically by ${vs.typical_cross_day}` : ""}.
          After crossing it, the week closed back {up ? "below" : "above"} the line{" "}
          <b>{stPct0((a.p_closed_back || 0) * 100)}</b> of the time
          {" "}({a.n} crossings, {a.grade.toLowerCase()}).
        </p>
        <p title={ST_TIP.beyond}>
          Typical further travel past the line <b>{stPct(a.median_beyond_pct * 100)}</b>; nine in ten stayed
          within <b>{stPct(a.p90_beyond_pct * 100)}</b>. The record is {stPct(a.line.record_pct * 100, 1)}.
        </p>
        {vs ? (
          <p className="sl-muted" title={ST_TIP.vs_monday}>
            Same 20-delta rule: sold Monday, the strike sat {stPct(vs.monday.strike_from_anchor_pct * 100, 1)} away and
            finished through {stPct0(vs.monday.finished_through * 100)}; sold after the line, it sat{" "}
            {stPct(vs.line.strike_from_anchor_pct * 100, 1)} away and finished through {stPct0(vs.line.finished_through * 100)}.
          </p>
        ) : null}
      </div>
    );
  };

  const live = (data && data.live) || [];
  return (
    <div className="card st-line-card" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker" title={ST_TIP.card}>After it reaches the line</div>
          <div className="card-title">What {ticker} did once it got to its usual high or low</div>
        </div>
        {live.length ? (
          <div className="toolbar">
            {live.map((r) => (
              <span key={r.key} className={`st-state st-state-${r.state}`} title={ST_TIP.state}>
                {r.state.toUpperCase()} · {r.horizon} {r.side}{r.strike ? ` · ${stNum(r.strike, 2)}` : ""}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      {err ? <p className="sl-muted">{err}</p> : null}
      {!data && !err ? (
        <div className="st-loading" aria-busy="true">
          <div className="skel skel-line" style={{ width: "60%" }} />
          <div className="skel skel-line" style={{ width: "85%" }} />
        </div>
      ) : null}
      {data ? (
        <div className="st-sides">{["call", "put"].map(side)}</div>
      ) : null}
      {data ? (
        <p className="sl-muted" style={{ marginTop: 8 }} title={ST_TIP.day_upper}>
          Measured on {data.n_weeks} complete weeks of daily bars, in {ticker}'s own sigma
          ({stPct0(data.sigma_annual * 100, 0)} annualised). Outcomes are counted from the crossing bar on, never
          from Monday. When the stock is past a line during the session, the At the line board on Trade prices the strikes.
        </p>
      ) : null}
    </div>
  );
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, { StretchCard: React.memo(StretchCard), StretchLineCard: React.memo(StretchLineCard) });
