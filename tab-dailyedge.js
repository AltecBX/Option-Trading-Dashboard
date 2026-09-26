(function () {
// tab-dailyedge.jsx — LAZY CHUNK (v5.35). DAILY EDGE.
//
// Jerry, 9/26: "I only want to trade when I have an edge." Six ETFs list an
// expiry every weekday (SMH, QQQ, SPY, IWM, XLF, GLD). This card says, for
// each one, whether right now is a day the two-year record says to sell the
// next-day call, the next-day put, or nothing — and when SMH sits in its
// no-edge band, which of the others has a setup. One row per ETF, plain
// words, the contract and its bid, and whether the bid pays for what the
// record lost past that strike. DAILY_EDGE.md has the numbers.
//
// Endpoints: GET /api/daily-edge · /api/daily-edge/forward

const DE_TIP = {
  card: "Next-day call or put signals on the six ETFs with an expiry every weekday. Measured on two years of hourly bars; only patterns that held in BOTH years are used. Most of the day most of these say SKIP, and that is the point.",
  symbol: "The ETF. Click to load it.",
  today: "Move from yesterday's close, and the same move in the ETF's own daily sigma (the stdev of its last 20 daily moves).",
  zone: "The sell zone in percent for TODAY: 0.15 to 0.90 of this ETF's own daily sigma. It widens when the ETF is volatile and narrows when it is quiet.",
  signal: "SELL: the record says sell now. WAIT: in the zone but before 10:30, when the record starts. NO CALL: down more than 0.9 sigma, the next day tends to bounce. SKIP: no edge either way in the record.",
  sell: "The next session's expiry and the listed strike nearest the one the evidence was measured on (0.84 sigma out over the time left, about 20 delta).",
  bid: "The live bid on that contract. A resting sell order is only promised the bid.",
  loss: "What the record lost past that strike on average, per share, on the signal days. Your bid has to beat this for the trade to have paid historically.",
  pays: "Bid minus the record's average loss. Positive means the premium on the screen covered the historical loss past the strike.",
  record: "On the signal days, how often that side finished past its strike, last year / prior year. Selling the same side at every close broke about 16-23% of the time.",
  forward: "Every first signal per ETF per day is logged with its strike and bid, then graded after its expiry closes. This is the paper-trade record building itself."
};
const deNum = (v, d = 2) => v == null || !isFinite(v) ? "—" : Number(v).toFixed(d);
const dePct = (v, d = 2) => v == null || !isFinite(v) ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
const deMoney = v => v == null || !isFinite(v) ? "—" : `${v < 0 ? "-" : ""}$${Math.abs(Number(v)).toFixed(2)}`;
const deTime = s => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit"
  });
};
const deDate = s => {
  if (!s) return "—";
  const d = new Date(`${s}T12:00:00`);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-US", {
    weekday: "short",
    month: "numeric",
    day: "numeric"
  });
};
const deStrike = v => v == null || !isFinite(v) ? "—" : Math.round(v * 100) % 100 === 0 ? String(Math.round(v)) : Number(v).toFixed(2);
async function deReadJson(r) {
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
function deSignal(r) {
  if (r.state === "SELL") return {
    cls: r.side === "call" ? "st-up" : "st-down",
    text: `SELL ${String(r.side).toUpperCase()}`
  };
  if (r.state === "WAIT") return {
    cls: "st-inside",
    text: "WAIT"
  };
  if (r.state === "NO_CALL") return {
    cls: "st-outside",
    text: "NO CALL"
  };
  if (r.state === "CLOSED") return {
    cls: "st-none",
    text: "CLOSED"
  };
  if (r.state === "NO_DATA") return {
    cls: "st-none",
    text: "NO DATA"
  };
  return {
    cls: "st-none",
    text: "SKIP"
  };
}
function deRecord(r) {
  const ev = r.rule_evidence || r.evidence;
  if (!ev || !ev.breach) return "—";
  const [a, b] = ev.breach;
  return `${a}/${b}%`;
}
function DailyEdgeCard({
  apiFetch,
  onPickTicker
}) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  const load = React.useCallback(async () => {
    const mine = ++seq.current;
    setBusy(true);
    try {
      const r = await apiFetch("/api/daily-edge");
      const {
        d,
        err: pageErr
      } = await deReadJson(r);
      if (mine !== seq.current) return;
      if (d == null) {
        setData(null);
        setErr(pageErr);
        return;
      }
      setData(d);
      setErr(d.error && !(d.rows || []).length ? d.error : null);
    } catch (e) {
      if (mine === seq.current) setErr(String(e && e.message ? e.message : e));
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }, [apiFetch]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!(data && data.market_open)) return;
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [data && data.market_open, load]);
  const rows = data && data.rows || [];
  const fw = data && data.forward || {};
  const nSell = rows.filter(r => r.state === "SELL").length;
  return /*#__PURE__*/React.createElement("div", {
    className: "card sk-card de-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: DE_TIP.card
  }, "Daily edge"), /*#__PURE__*/React.createElement("h3", {
    className: "card-title"
  }, "Next-day call or put \u2014 only when the record says so")), /*#__PURE__*/React.createElement("div", {
    className: "toolbar"
  }, /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: load,
    disabled: busy,
    title: "Re-read the six ETFs"
  }, busy ? "Reading…" : "Refresh"))), data ? /*#__PURE__*/React.createElement("p", {
    className: "sl-status"
  }, /*#__PURE__*/React.createElement(DataStatus, {
    kind: busy ? "loading" : data.as_of ? "cached" : "pending",
    at: data.as_of,
    note: "Re-read every minute while the market is open, whether or not this tab is open."
  }), data.as_of ? `Read ${deTime(data.as_of)}` : "Not read yet", " · ", nSell ? `${nSell} to sell now` : data.market_open ? "nothing to sell right now" : "market closed", data.alerts && data.alerts.configured ? " · pushes on" : " · no push channel configured") : null, err ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "research-error"
  }, err), /*#__PURE__*/React.createElement("button", {
    className: "card-error-btn",
    onClick: load
  }, "Try again")) : null, rows.length ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap sk-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table mtable sk-table de-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: DE_TIP.symbol
  }, "ETF"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: DE_TIP.today
  }, "Today"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num sk-m-hide",
    title: DE_TIP.zone
  }, "Sell zone"), /*#__PURE__*/React.createElement("th", {
    title: DE_TIP.signal
  }, "Signal"), /*#__PURE__*/React.createElement("th", {
    title: DE_TIP.sell
  }, "Sell"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: DE_TIP.bid
  }, "Bid"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num sk-m-hide",
    title: DE_TIP.loss
  }, "Hist. loss"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: DE_TIP.pays
  }, "Pays?"), /*#__PURE__*/React.createElement("th", {
    className: "sk-m-hide",
    title: DE_TIP.record
  }, "Broke 1y/2y"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => {
    const sig = deSignal(r);
    const ct = r.contract || {};
    const selling = r.state === "SELL" || r.state === "WAIT";
    return /*#__PURE__*/React.createElement("tr", {
      key: r.symbol,
      className: `scan-row sk-row de-${String(r.state).toLowerCase()}`,
      title: r.reason || ""
    }, /*#__PURE__*/React.createElement("td", {
      "data-label": "ETF",
      title: DE_TIP.symbol
    }, /*#__PURE__*/React.createElement("button", {
      className: "su-blink",
      onClick: () => onPickTicker && onPickTicker(r.symbol),
      title: `Load ${r.symbol}`
    }, r.symbol)), /*#__PURE__*/React.createElement("td", {
      "data-label": "Today",
      className: "scan-num",
      title: DE_TIP.today
    }, dePct(r.move_pct), " ", /*#__PURE__*/React.createElement("span", {
      className: "sl-muted"
    }, r.z == null ? "" : `${r.z >= 0 ? "+" : ""}${deNum(r.z)}σ`)), /*#__PURE__*/React.createElement("td", {
      "data-label": "Sell zone",
      className: "scan-num sk-m-hide",
      title: DE_TIP.zone
    }, r.zone_lo_pct == null ? "—" : `+${deNum(r.zone_lo_pct)}–${deNum(r.zone_hi_pct)}%`), /*#__PURE__*/React.createElement("td", {
      "data-label": "Signal",
      title: r.reason || DE_TIP.signal
    }, /*#__PURE__*/React.createElement("span", {
      className: `st-chip ${sig.cls}`
    }, sig.text), r.fired && r.fired.at ? /*#__PURE__*/React.createElement("span", {
      className: "sl-muted",
      style: {
        whiteSpace: "nowrap"
      }
    }, " ", deTime(r.fired.at)) : null), /*#__PURE__*/React.createElement("td", {
      "data-label": "Sell",
      title: selling && ct.delta != null ? `${DE_TIP.sell} Delta ${Math.abs(ct.delta).toFixed(2)}.` : DE_TIP.sell
    }, selling ? `${deDate(r.expiry)} ${deStrike(ct.strike != null ? ct.strike : r.model_strike)} ${r.side}` : "—"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Bid",
      className: "scan-num",
      title: DE_TIP.bid
    }, selling ? deMoney(ct.bid) : "—"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Record's loss",
      className: "scan-num sk-m-hide",
      title: DE_TIP.loss
    }, selling ? deMoney(r.hist_loss) : "—"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Pays?",
      className: "scan-num",
      title: DE_TIP.pays
    }, selling && r.edge != null ? /*#__PURE__*/React.createElement("span", {
      className: r.pays ? "up" : "down"
    }, r.pays ? "Yes" : "No", " ", deMoney(r.edge)) : "—"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Breached",
      className: "sk-m-hide",
      title: DE_TIP.record
    }, deRecord(r)));
  })))) : null, rows.some(r => r.state === "SELL" && r.side === "call") ? /*#__PURE__*/React.createElement("p", {
    className: "sl-muted",
    style: {
      marginTop: 8
    }
  }, "A call is covered only with 100 shares. Without them, sell it as a spread.") : null, /*#__PURE__*/React.createElement("p", {
    className: "sl-status",
    title: DE_TIP.forward
  }, "Paper record: ", fw.graded ? `${fw.graded} graded, breached ${fw.breach_pct}%` : "no graded signals yet", fw.pnl_per_share != null ? ` · net ${deMoney(fw.pnl_per_share)}/share` : "", fw.open ? ` · ${fw.open} open` : ""), /*#__PURE__*/React.createElement(PanelMethod, {
    label: "Method \u2014 what was measured and what is not used"
  }, /*#__PURE__*/React.createElement("p", null, "Every hour of the last two years on all six ETFs: a strike 0.84 of the ETF's own daily sigma out (scaled to the time left to the next close, about 20 delta) sold for the next session's expiry and graded on that close. Only patterns that held in both years are used. Quietly up between 0.15 and 0.90 sigma from 10:30 to 3:30 is a call day on SMH, QQQ, SPY, IWM and XLF, and a put day on GLD. Down more than 0.90 sigma is never a call day on the first five. SMH adds two close rules from ten years of daily bars: the second down day in a row, and an up Friday, sell the put."), /*#__PURE__*/React.createElement("p", null, "Everything else \u2014 a small red day, a flat day, an up day past 0.90 sigma, puts after a selloff \u2014 flipped between years and is shown as SKIP. The strike and loss come from the price record; the bid comes from the live chain. Whether the bid beats the loss is the check the history could not make.")));
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  DailyEdgeCard: React.memo(DailyEdgeCard)
});
})();
