(function () {
// tab-spike.jsx — LAZY CHUNK (v4.82). SOLD INTO STRENGTH.
//
// The manual workflow this replaces: sort the day's movers, judge how
// extreme each move is, open the chain, find today's expiry, read the
// premium, decide. By the time that is done the volatility premium has
// usually collapsed.
//
// The board ranks by the one number that decides the trade: the credit on
// the screen MINUS what the measured record says that call settles for. Not
// a probability, which cannot price a sale, and not a score, which cannot
// be checked against a fill.
//
// Every move and every strike is shown in the stock's OWN daily sigma as
// well as in percent, because percent is the wrong ruler — a 3.6% day on a
// stock running at 81% volatility is an ordinary session and a 16.4% day on
// a 51%-vol name is a four-sigma event.
//
// Endpoints: GET /api/spike?top=  ·  /api/spike/detail?symbol=  ·  /status

const SK_TIP = {
  card: "Stocks that have already run hard today, and what their same-day calls pay for selling above the level they reached. Ranked by dollars of measured edge per contract — the credit minus what this kind of move has historically settled for.",
  edge: "THE RANKING COLUMN. The credit you would take, minus what the measured record says this call settles for at the bell, per contract. No volatility model is involved: the settlement is what actually happened after comparable runs, scaled by how much of the session is still ahead. Positive means the market is paying more than the history says the risk is worth.",
  settles: "What this call has historically been worth at the close, in dollars a share, after a run of this size with a strike this far beyond it. Measured, then scaled down by how much of the session remains — the same strike is a different trade at 10am and at 3pm.",
  credit: "The bid. It is the only price a resting sell order is actually promised, so it is what the edge is computed on — never the mid.",
  move: "How far the stock has run today, in percent AND in its own daily sigma. Sigma is the honest ruler: the same percentage is an ordinary day on a wild stock and an extreme event on a quiet one.",
  move_sigma: "Today's run divided by the stock's own 20-day daily sigma. Sorting the board on percent instead would rank a 3.6% day on an 81%-volatility name above a 16% day on a 51%-volatility name, which is backwards.",
  vol: "The stock's own volatility right now, annualised from its last 20 sessions. It is what makes today's move large or ordinary.",
  strike: "The call being sold: its strike, how far above yesterday's close it sits in percent, and the same distance in the stock's own sigma.",
  beyond: "How far the strike sits BEYOND the level the stock has already reached, in sigma. This is the distance that has to be covered for the sale to lose, and it is what the measured probability is keyed on.",
  p_close: "How often a stock that had run this far CLOSED at or above a strike this much further out. Measured on 830,059 sessions across 371 names, in each stock's own sigma, then shrunk toward this ticker's own record where it has one. This is the real-world frequency, not delta.",
  p_touch: "How often it ever TRADED at the strike, whether or not it closed there. Always at least as high as the close-above rate — you cannot finish above a level you never reached. A touch is what you feel; the close is what settles.",
  at_high: "How often a stock that ran this far FINISHED within a whisker of its high. It is rarely more than one time in fifteen for a big move, and it gets rarer the bigger the move — that is the behaviour this trade is built on.",
  grade: "MEASURED when this ticker has 20 or more comparable runs of its own behind the number. MOSTLY POOLED or POOLED when it does not and the universe is answering. A pooled answer is not a worse answer, but it is a less specific one.",
  own: "How many runs of this size this particular stock has on file, and what share of the probability came from its own record rather than the universe. A name with three spikes on file is answered almost entirely by the pool, and says so.",
  session: "How much of the trading session has already gone. This is the single biggest factor in the trade: the same strike that loses money at 10am can be clearly profitable at 3pm, because the stock has that much less time to reach it.",
  session_basis: "MEASURED means the share of the day's risk still ahead was computed from real minute bars — risk does not arrive evenly through a session, it clusters at the open and the close. MODELED means it fell back to the clock, which is the largest approximation on this card.",
  expiration: "The expiration being sold. This board only lists same-day expiries, which is the whole point — the position resolves at the bell.",
  spread: "The bid-ask spread as a percentage of the mid. Same-day options widen out fast; a wide market is expensive to exit if the trade goes wrong.",
  oi: "Open interest and today's volume on this contract. Thin interest on a same-day option is a hard exit.",
  iv: "The implied volatility being charged on this contract, and the option's delta. Delta is the risk-neutral probability the market is charging — the close-above column is what actually happened.",
  refused: "Names that ran, and strikes that were priced, but did not make the board — with the reason. A short list is only trustworthy if you can see what did not make it.",
  candidates: "Every name that has run at least the minimum sigma today, whether or not any of its calls qualified. This is the funnel the board was drawn from.",
  no_trade: "Nothing qualifies. Most sessions are like this — a board that always has something on it is not measuring anything.",
  takeover: "A stock spiking on a takeover or merger headline is never listed here. That is the one move that does not come back, and it is also the move that quietly leaves a hole in any history measured on names that still exist.",
  prior: "The measured record behind every probability on this card: sessions and names, from daily prints, with the move and the strike in each stock's own sigma.",
  scanning: "A scan is running now. The board refreshes itself every couple of minutes while the market is open, and stops when nobody is looking at it.",
  stale: "The market is closed, or the scan has not run recently. Same-day premium decays by the minute, so an old board here is not a board."
};
const skNum = (v, d = 2) => v == null || !isFinite(v) ? "—" : Number(v).toFixed(d);
const skPct = (v, d = 1) => v == null || !isFinite(v) ? "—" : `${Number(v).toFixed(d)}%`;
const skPct0 = (v, d = 0) => v == null || !isFinite(v) ? "—" : `${(Number(v) * 100).toFixed(d)}%`;
const skMoney = (v, d = 2) => v == null || !isFinite(v) ? "—" : `$${Number(v).toFixed(d)}`;
const skSigned = (v, d = 0) => v == null || !isFinite(v) ? "—" : `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(d)}`;
const skSig = v => v == null || !isFinite(v) ? "—" : `${Number(v).toFixed(2)}σ`;
// House rule: dates read "September 18, 2026".
const skDate = s => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric"
  });
};
const skTime = s => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit"
  });
};
const skRowKey = r => `${r.symbol}|${r.expiration}|${r.strike}`;
async function skReadJson(r) {
  const text = await r.text();
  try {
    return {
      d: JSON.parse(text)
    };
  } catch (_e) {
    return r.ok ? {
      d: null,
      err: "The app's sign-in page answered instead of data. Reload to sign back in."
    } : {
      d: null,
      err: "The server answered with an error page instead of data."
    };
  }
}

// label, key, tooltip, formatter, numeric
const SK_COLS = [["Rank", "rank", "edge", r => r.rank, true], ["Symbol", "symbol", "card", null, false], ["Run today", "move_pct", "move", r => `${skPct(r.move_pct)} · ${skSig(r.move_sigma)}`, true], ["Strike", "strike", "strike", r => `${skNum(r.strike, 2)} · ${skPct(r.strike_pct)}`, true], ["Beyond the run", "beyond_sigma", "beyond", r => skSig(r.beyond_sigma), true], ["Credit (bid)", "credit", "credit", r => skMoney(r.credit), true], ["Settles for", "settles", "settles", r => skMoney(r.settles), true], ["Edge per contract", "edge_per_contract", "edge", r => skSigned(r.edge_per_contract, 0), true], ["Closes above", "p_close_above", "p_close", r => skPct0(r.p_close_above), true], ["Ever touches", "p_touch", "p_touch", r => skPct0(r.p_touch), true], ["Finishes at its high", "p_finishes_at_high", "at_high", r => skPct0(r.p_finishes_at_high, 1), true], ["Evidence", "grade", "grade", r => r.grade, false], ["Its own runs", "n_own", "own", r => `${r.n_own == null ? "—" : r.n_own} · ${skPct0(r.weight_own, 0)}`, true], ["Its own volatility", "sigma_annual", "vol", r => skPct0(r.sigma_annual, 0), true], ["Spread", "spread_pct", "spread", r => skPct(r.spread_pct, 0), true], ["Open interest", "oi", "oi", r => `${r.oi == null ? "—" : Number(r.oi).toLocaleString()} · ${r.volume == null ? "—" : Number(r.volume).toLocaleString()}`, true], ["Implied volatility", "iv", "iv", r => `${skPct0(r.iv, 0)} · ${skNum(r.delta, 2)}Δ`, true]];
const SK_ASC = new Set(["rank", "symbol", "strike", "spread_pct", "p_close_above", "p_touch", "settles"]);
// On a phone the table stacks; only the deciding fields show there.
const SK_MOBILE = new Set(["rank", "symbol", "move_pct", "strike", "beyond_sigma", "credit", "settles", "edge_per_contract", "p_close_above", "grade"]);
function SkDetail({
  r,
  detail,
  onClose
}) {
  const ref = detail && detail.refused || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "sk-detail-wrap"
  }, /*#__PURE__*/React.createElement("div", {
    className: "sk-detail-head"
  }, /*#__PURE__*/React.createElement("b", null, "#", r.rank, " ", r.symbol), " \xB7 ", skDate(r.expiration), " \xB7 ", skNum(r.strike, 2), " call", /*#__PURE__*/React.createElement("button", {
    className: "su-more-btn sk-close",
    onClick: onClose,
    title: "Collapse"
  }, "Close")), /*#__PURE__*/React.createElement("div", {
    className: "sk-detail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "sl-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "sl-block-title"
  }, "Why this one"), /*#__PURE__*/React.createElement("p", null, r.symbol, " has run ", /*#__PURE__*/React.createElement("b", null, skPct(r.move_pct)), " today, which is", " ", /*#__PURE__*/React.createElement("b", null, skSig(r.move_sigma)), " for a stock whose own volatility is", " ", skPct0(r.sigma_annual, 0), " annualised. The ", skNum(r.strike, 2), " call sits", " ", /*#__PURE__*/React.createElement("b", null, skSig(r.beyond_sigma)), " beyond the level it has already reached."), /*#__PURE__*/React.createElement("p", null, "After comparable runs, a stock closed above a strike that far out", " ", /*#__PURE__*/React.createElement("b", null, skPct0(r.p_close_above)), " of the time and traded there at some point", " ", skPct0(r.p_touch), " of the time. It finished within a whisker of its high", " ", /*#__PURE__*/React.createElement("b", null, skPct0(r.p_finishes_at_high, 1)), " of the time \u2014 that rarity is what this trade is built on."), /*#__PURE__*/React.createElement("p", {
    title: SK_TIP.edge
  }, "The market pays ", /*#__PURE__*/React.createElement("b", null, skMoney(r.credit)), " at the bid. The measured record settles this call at ", /*#__PURE__*/React.createElement("b", null, skMoney(r.settles)), " with", " ", skPct0(1 - (r.elapsed || 0), 0), " of the session still ahead (full session: ", skMoney(r.settles_full_session), "). That is", " ", /*#__PURE__*/React.createElement("b", null, skSigned(r.edge_per_contract, 0)), " a contract."), /*#__PURE__*/React.createElement("p", {
    className: "sl-muted",
    title: SK_TIP.session_basis
  }, "Session left: ", r.session_basis), /*#__PURE__*/React.createElement("p", {
    className: "sl-muted",
    title: SK_TIP.grade
  }, "Evidence: ", /*#__PURE__*/React.createElement("b", null, r.grade), " \u2014 ", r.n_own, " runs of this size on ", r.symbol, "'s own record, carrying ", skPct0(r.weight_own, 0), " of the probability; the rest is the pooled universe.", r.clamped ? " This move is past the edge of the measured grid, so the " + "nearest measured row was used rather than extrapolating." : "")), /*#__PURE__*/React.createElement("div", {
    className: "sl-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "sl-block-title"
  }, "What would make this wrong"), /*#__PURE__*/React.createElement("ul", null, /*#__PURE__*/React.createElement("li", null, "The high is not in yet. A strike this far out is touched", " ", skPct0(r.p_touch), " of the time \u2014 the seller is paid for the run being over, not for the stock falling back."), /*#__PURE__*/React.createElement("li", null, "The session estimate. ", r.session_basis, ". Risk clusters at the open and the close, so a clock-based figure understates what is left mid-morning."), /*#__PURE__*/React.createElement("li", null, "A headline that re-rates the name after you are short. Takeover and merger spikes are refused outright, but nothing catches an unscheduled one."), /*#__PURE__*/React.createElement("li", null, "The measured record is drawn from names that still exist today, which quietly omits the spikes that were acquired and never came back."))), ref.length ? /*#__PURE__*/React.createElement("div", {
    className: "sl-block",
    title: SK_TIP.refused
  }, /*#__PURE__*/React.createElement("div", {
    className: "sl-block-title"
  }, "Refused on ", r.symbol), /*#__PURE__*/React.createElement("ul", null, ref.slice(0, 8).map((x, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, x.strike ? `${skNum(x.strike, 2)} call — ` : "", (x.why || []).join("; "))))) : null));
}
function SpikeCard({
  apiFetch,
  onPickTicker
}) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sortK, setSortK] = useState("rank");
  const [sortD, setSortD] = useState(1);
  const [open, setOpen] = useState(null);
  const [details, setDetails] = useState({});
  const [showRefused, setShowRefused] = useState(false);
  const [showFunnel, setShowFunnel] = useState(false);
  const seq = useRef(0);
  const load = React.useCallback(async () => {
    const mine = ++seq.current;
    setBusy(true);
    try {
      const r = await apiFetch("/api/spike");
      const {
        d,
        err: pageErr
      } = await skReadJson(r);
      if (mine !== seq.current) return;
      if (d == null) {
        setData(null);
        setErr(pageErr);
        return;
      }
      setData(d);
      setErr(d.error || null);
    } catch (e) {
      if (mine === seq.current) setErr(String(e && e.message ? e.message : e));
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }, [apiFetch]);
  useEffect(() => {
    load();
  }, [load]);
  // Same-day premium decays by the minute, so this board refreshes itself
  // while the market is open — and not at all when it is closed.
  useEffect(() => {
    if (!(data && data.market_open)) return;
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [data && data.market_open, load]);
  const rows = data && data.rows || [];
  const sorted = useMemo(() => {
    const key = r => {
      const v = r[sortK];
      if (v == null) return sortD > 0 ? Infinity : -Infinity;
      return typeof v === "string" ? v.toLowerCase() : v;
    };
    return rows.slice().sort((a, b) => {
      const ka = key(a),
        kb = key(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * sortD;
    });
  }, [rows, sortK, sortD]);
  const openRow = open ? sorted.find(r => skRowKey(r) === open) || null : null;
  const th = (label, k, tipKey, numeric) => /*#__PURE__*/React.createElement("th", {
    key: k,
    className: numeric ? "scan-th-num" : "",
    title: SK_TIP[tipKey],
    style: {
      cursor: "pointer"
    },
    onClick: () => {
      if (sortK === k) setSortD(x => -x);else {
        setSortK(k);
        setSortD(SK_ASC.has(k) ? 1 : -1);
      }
    }
  }, label, sortK === k ? sortD < 0 ? " ↓" : " ↑" : "");
  const toggle = async r => {
    const k = skRowKey(r);
    if (open === k) {
      setOpen(null);
      return;
    }
    setOpen(k);
    if (!details[r.symbol]) {
      try {
        const resp = await apiFetch(`/api/spike/detail?symbol=${encodeURIComponent(r.symbol)}`);
        const {
          d
        } = await skReadJson(resp);
        if (d) setDetails(p => ({
          ...p,
          [r.symbol]: d
        }));
      } catch (_e) {/* the row still opens on what the board carries */}
    }
  };
  const refused = data && data.refused || [];
  const cands = data && data.candidates || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "card sk-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: SK_TIP.card
  }, "Sold into strength"), /*#__PURE__*/React.createElement("h3", {
    className: "card-title"
  }, "What today\u2019s runs pay for selling above them"), /*#__PURE__*/React.createElement("p", {
    className: "card-sub"
  }, "Stocks that have moved hard in their OWN volatility, and every same-day call above the level they reached, all expiring TODAY \u2014 ranked by the credit minus what that call has historically settled for. A big mover finishes at its high about one time in fifteen; the seller is paid for the run being over, not for a reversal.")), /*#__PURE__*/React.createElement("div", {
    className: "toolbar"
  }, /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: load,
    disabled: busy,
    title: "Re-read the board"
  }, busy ? "Reading…" : "Refresh"))), data ? /*#__PURE__*/React.createElement("p", {
    className: "sl-status"
  }, /*#__PURE__*/React.createElement("span", {
    title: SK_TIP.stale
  }, data.as_of ? `Scanned ${skDate(data.as_of)} at ${skTime(data.as_of)}` : "No scan yet"), /*#__PURE__*/React.createElement("span", {
    title: SK_TIP.session
  }, " \xB7 ", skPct0(data.elapsed, 0), " of the session gone"), /*#__PURE__*/React.createElement("span", {
    title: SK_TIP.session_basis
  }, " \xB7 session left ", data.session_profile), data.scanning ? /*#__PURE__*/React.createElement("span", {
    className: "sl-live",
    title: SK_TIP.scanning
  }, " \xB7 scanning") : null, /*#__PURE__*/React.createElement("span", {
    title: SK_TIP.candidates
  }, " \xB7 ", data.scanned, " of ", data.universe, " names have run"), data.prior ? /*#__PURE__*/React.createElement("span", {
    title: SK_TIP.prior
  }, " \xB7 measured on ", (data.prior.n_sessions || 0).toLocaleString(), " sessions across ", data.prior.n_names, " names") : null, /*#__PURE__*/React.createElement("span", null, " \xB7 ", data.version)) : null, busy && !data ? /*#__PURE__*/React.createElement("div", {
    className: "st-loading",
    "aria-busy": "true"
  }, /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "40%"
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "88%"
    }
  })) : null, err ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "research-error"
  }, err), /*#__PURE__*/React.createElement("button", {
    className: "card-error-btn st-retry",
    onClick: load
  }, "Try again")) : null, data && !err && data.no_trade ? /*#__PURE__*/React.createElement("div", {
    className: "su-refused sl-notrade",
    title: SK_TIP.no_trade
  }, /*#__PURE__*/React.createElement("b", null, "Nothing to sell into."), " ", data.no_trade_reason) : null, rows.length ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap sk-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable sk-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, SK_COLS.map(([l, k, t, _f, n]) => th(l, k, t, n)))), /*#__PURE__*/React.createElement("tbody", null, sorted.map(r => {
    const k = skRowKey(r);
    return /*#__PURE__*/React.createElement("tr", {
      key: k,
      className: `scan-row sk-row ${open === k ? "scan-row-active" : ""} ${r.rank === 1 ? "sk-row-top" : ""}`,
      onClick: () => toggle(r),
      title: "Click for the full reasoning and what was refused on this name"
    }, SK_COLS.map(([label, ck, tipKey, f, numeric]) => /*#__PURE__*/React.createElement("td", {
      key: ck,
      "data-label": label,
      title: SK_TIP[tipKey],
      className: `${numeric ? "scan-num" : ""} ${SK_MOBILE.has(ck) ? "" : "sk-m-hide"}`
    }, ck === "symbol" ? /*#__PURE__*/React.createElement("button", {
      className: "su-blink",
      onClick: e => {
        e.stopPropagation();
        onPickTicker && onPickTicker(r.symbol);
      },
      title: `Load ${r.symbol}`
    }, r.symbol) : ck === "edge_per_contract" ? /*#__PURE__*/React.createElement("b", {
      className: r.edge_per_contract >= 0 ? "sk-edge-up" : "sk-edge-dn"
    }, f(r)) : f(r))));
  })))) : null, openRow ? /*#__PURE__*/React.createElement(SkDetail, {
    r: openRow,
    detail: details[openRow.symbol],
    onClose: () => setOpen(null)
  }) : null, data ? /*#__PURE__*/React.createElement("div", {
    className: "su-more"
  }, /*#__PURE__*/React.createElement("button", {
    className: "su-more-btn",
    "aria-expanded": showFunnel,
    title: SK_TIP.candidates,
    onClick: () => setShowFunnel(v => !v)
  }, showFunnel ? "Hide" : "Show", " what has run today (", cands.length, ")"), showFunnel ? cands.length ? /*#__PURE__*/React.createElement("table", {
    className: "sl-mini"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Symbol"
  }, "Symbol"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SK_TIP.move
  }, "Run today"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SK_TIP.move_sigma
  }, "In its own sigma"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: SK_TIP.vol
  }, "Its own volatility"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Last price"
  }, "Last"))), /*#__PURE__*/React.createElement("tbody", null, cands.map(c => /*#__PURE__*/React.createElement("tr", {
    key: c.symbol
  }, /*#__PURE__*/React.createElement("td", {
    title: "Symbol"
  }, c.symbol), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: SK_TIP.move
  }, skPct(c.change_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: SK_TIP.move_sigma
  }, skSig(c.move_sigma)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: SK_TIP.vol
  }, skPct0(c.sigma_annual, 0)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: "Last price"
  }, skMoney(c.last)))))) : /*#__PURE__*/React.createElement("p", {
    className: "sl-muted"
  }, "Nothing has run far enough today.") : null, /*#__PURE__*/React.createElement("button", {
    className: "su-more-btn",
    "aria-expanded": showRefused,
    title: SK_TIP.refused,
    onClick: () => setShowRefused(v => !v)
  }, showRefused ? "Hide" : "Show", " what was refused (", refused.length, ")"), showRefused ? refused.length ? /*#__PURE__*/React.createElement("table", {
    className: "sl-mini sl-failed"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Symbol"
  }, "Symbol"), /*#__PURE__*/React.createElement("th", {
    title: SK_TIP.refused
  }, "Why"))), /*#__PURE__*/React.createElement("tbody", null, refused.map((x, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", {
    title: "Symbol"
  }, x.symbol, x.strike ? ` ${skNum(x.strike, 2)}` : ""), /*#__PURE__*/React.createElement("td", {
    title: SK_TIP.refused
  }, (x.why || []).join(" · ")))))) : /*#__PURE__*/React.createElement("p", {
    className: "sl-muted"
  }, "Nothing was refused.") : null) : null, /*#__PURE__*/React.createElement("p", {
    className: "sl-muted",
    title: SK_TIP.takeover
  }, "Takeover and merger spikes are never listed here \u2014 that is the one move that does not come back."));
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  SpikeCard: React.memo(SpikeCard)
});
})();
