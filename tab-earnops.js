(function () {
// tab-earnops.jsx — LAZY CHUNK (v3.64), split from app-cards.jsx.
// Earnings Opportunities scanner; loaded on first Earnings-Ops-tab open.

// ═══════════════════════════════════════════════════════════════════════════
// EARNINGS OPPORTUNITIES TAB (v3.63) — Market-Chameleon-style earnings
// opportunity scanner on our own providers. NOT a calendar: every watchlist
// name reporting −4…+8 days gets scored 0–100, classified into a setup /
// status / best action, and given an explainable trade plan or an explicit
// NO TRADE. Data: /api/earnings_scan (board pattern). Demo rows are labeled.
// ═══════════════════════════════════════════════════════════════════════════

const EOP_SECTIONS = [["all", "All"], ["today", "Today"], ["pre", "Pre-earnings"], ["post", "Post movers"], ["premium", "Premium ops"], ["waiting", "Waiting confirm"], ["extended", "Extended"], ["no_trade", "No trade"]];
const EOP_ACTION = {
  watch: ["WATCH", "mut"],
  enter_on_confirmation: ["ENTER ON CONFIRM", "mut"],
  confirmed_entry: ["CONFIRMED", "up"],
  sell_premium: ["SELL PREMIUM", "up"],
  already_extended: ["EXTENDED", "down"],
  avoid: ["AVOID", "down"],
  no_trade: ["NO TRADE", "mut"]
};
const EOP_SETUP_LABEL = {
  high_premium: "High premium",
  cheap_implied: "Cheap implied",
  put_selling: "Put selling",
  covered_call: "Covered call",
  post_earnings_continuation: "Continuation",
  post_earnings_reversal: "Reversal",
  gap_and_go: "Gap & go",
  gap_fill: "Gap fill",
  vwap_reclaim: "VWAP reclaim",
  vwap_rejection: "VWAP reject",
  breakout: "Breakout",
  breakdown: "Breakdown",
  pre_earnings_momentum: "Pre-E momentum",
  pre_earnings_fade: "Pre-E fade",
  short_candidate: "Short",
  no_trade: "No trade"
};
function eopMcap(v) {
  if (v == null) return "—";
  return v >= 1e12 ? (v / 1e12).toFixed(1) + "T" : v >= 1e9 ? (v / 1e9).toFixed(1) + "B" : (v / 1e6).toFixed(0) + "M";
}
function eopPct(v, d = 1) {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`;
}

// ═══════════════════════════════════════════════════════════════════════════
// EARNINGS WHISPERS WEEKLY CALENDAR (v3.87) — the @eWhispers weekly
// earnings-calendar post from X, shown natively at the top of the Earnings
// area. Data: /api/ewhispers/weekly (server-cached; the page never waits on
// X). Display: the calendar image straight from X's media CDN when the
// server has API credentials, otherwise the official X embed. Tickers come
// from the post's cashtags and click through to the global ticker.
// ═══════════════════════════════════════════════════════════════════════════

// The official X embed script, loaded ONCE and only when a card actually
// needs the embed fallback (an image-less post). Never loaded for the
// normal image path.
let _xWidgetsPromise = null;
function loadXWidgets() {
  if (_xWidgetsPromise) return _xWidgetsPromise;
  _xWidgetsPromise = new Promise((resolve, reject) => {
    if (window.twttr && window.twttr.widgets) {
      resolve(window.twttr);
      return;
    }
    const s = document.createElement("script");
    s.src = "https://platform.twitter.com/widgets.js";
    s.async = true;
    s.onload = () => window.twttr && window.twttr.widgets ? resolve(window.twttr) : reject(new Error("X embed script loaded without widgets"));
    s.onerror = () => {
      _xWidgetsPromise = null;
      reject(new Error("X embed script failed to load"));
    };
    document.head.appendChild(s);
  });
  return _xWidgetsPromise;
}
function XPostEmbed({
  postId,
  postUrl
}) {
  // Official createTweet factory — no third-party HTML is ever injected by
  // us; the widget renders into its own sandboxed iframe.
  const ref = useRef(null);
  const [phase, setPhase] = useState("loading");
  useEffect(() => {
    let stop = false;
    setPhase("loading");
    if (ref.current) ref.current.innerHTML = "";
    loadXWidgets().then(tw => tw.widgets.createTweet(String(postId), ref.current, {
      theme: "dark",
      dnt: true,
      align: "center",
      conversation: "none"
    })).then(el => {
      if (!stop) setPhase(el ? "ready" : "failed");
    }).catch(() => {
      if (!stop) setPhase("failed");
    });
    return () => {
      stop = true;
    };
  }, [postId]);
  return /*#__PURE__*/React.createElement("div", {
    className: "ew-embed"
  }, /*#__PURE__*/React.createElement("div", {
    ref: ref
  }), phase === "loading" && /*#__PURE__*/React.createElement("div", {
    className: "skel ew-imgskel",
    "aria-hidden": "true"
  }), phase === "failed" && /*#__PURE__*/React.createElement("div", {
    className: "ew-note"
  }, "The X embed couldn't load here (often an ad-blocker).", " ", /*#__PURE__*/React.createElement("a", {
    className: "ew-xlink",
    href: postUrl,
    target: "_blank",
    rel: "noopener noreferrer"
  }, "Open the post on X \u2197")));
}
function EwTickerChips({
  tickers,
  onOpenTicker
}) {
  const [showAll, setShowAll] = useState(false);
  if (!tickers || !tickers.length) return null;
  const CAP = 28;
  const shown = showAll ? tickers : tickers.slice(0, CAP);
  return /*#__PURE__*/React.createElement("div", {
    className: "ew-ticks",
    "aria-label": "Companies reporting this week"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ew-ticks-label",
    title: "Cashtags from the @eWhispers post \u2014 the week's headline reporters. Click one to load it in Analyze."
  }, "Reporting:"), shown.map(t => /*#__PURE__*/React.createElement("button", {
    key: t,
    type: "button",
    className: "ew-tick",
    title: `Open ${t} in Analyze`,
    onClick: () => onOpenTicker && onOpenTicker(t)
  }, t)), tickers.length > CAP && /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ew-tick ew-tick-more",
    onClick: () => setShowAll(v => !v)
  }, showAll ? "less" : `+${tickers.length - CAP} more`));
}

// The calendar image is served by OUR backend (downloaded from X once,
// cached on disk) and fetched here as a blob via apiFetch — same-origin,
// API key intact, immune to ad-blockers that eat twimg.com. The direct X
// URL is only the fallback if the proxy can't serve.
function useEwImageSrc(apiFetch, post, size) {
  const [src, setSrc] = useState(null);
  const proxy = post && (size === "full" ? post.image_proxy_full : post.image_proxy);
  const direct = post && (size === "full" ? post.image_url_full || post.image_url : post.image_url);
  useEffect(() => {
    let alive = true,
      obj = null;
    if (!direct) {
      setSrc(null);
      return undefined;
    }
    if (!proxy) {
      setSrc(direct);
      return undefined;
    }
    setSrc(null);
    // noCache: apiFetch's GET dedupe reads bodies as TEXT, which mangles
    // binary — this flag routes to a raw fetch (the browser's own HTTP
    // cache still applies via the endpoint's Cache-Control).
    apiFetch(proxy, {
      noCache: true
    }).then(r => {
      if (!r.ok) throw new Error(`proxy ${r.status}`);
      return r.blob();
    }).then(b => {
      if (!alive) return;
      obj = URL.createObjectURL(b);
      setSrc(obj);
    }).catch(() => {
      if (alive) setSrc(direct);
    });
    return () => {
      alive = false;
      if (obj) URL.revokeObjectURL(obj);
    };
  }, [post && post.post_id, proxy, direct]);
  return src;
}
function EarningsWhispersCard({
  apiFetch,
  onOpenTicker
}) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [viewWeek, setViewWeek] = useState(null); // null = relevant week
  const [zoom, setZoom] = useState(false);
  const [lbZoom, setLbZoom] = useState(false); // magnified inside the lightbox
  const [fullBroken, setFullBroken] = useState(false); // full-res variant failed to load
  const [imgBroken, setImgBroken] = useState(false);
  const [showText, setShowText] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualUrl, setManualUrl] = useState("");
  const [manualMsg, setManualMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const aliveRef = useRef(true);
  useEffect(() => () => {
    aliveRef.current = false;
  }, []);
  const urlFor = week => `/api/ewhispers/weekly${week ? `?week=${encodeURIComponent(week)}` : ""}`;
  const load = (week, {
    fresh
  } = {}) => {
    const p = fresh ? apiFetch(urlFor(week)).then(r => r.json()) : sharedJson(apiFetch, urlFor(week), 5 * 60000);
    return p.then(d => {
      if (!aliveRef.current) return d;
      setData(d);
      setErr(null);
      setImgBroken(false);
      return d;
    }).catch(e => {
      if (aliveRef.current) setErr(String(e && e.message || e));
      return null;
    });
  };
  useEffect(() => {
    load(viewWeek);
  }, [viewWeek]);
  useEffect(() => {
    // Gentle keep-fresh poll; the server serves its cache instantly and
    // checks X on its own few-hour cadence, so this is cheap.
    const id = setInterval(skipWhenHidden(() => load(viewWeek)), 15 * 60000);
    return () => clearInterval(id);
  }, [viewWeek]);
  const refresh = async () => {
    setBusy(true);
    try {
      await apiFetch("/api/ewhispers/refresh?force=1");
      // Detection runs in the background — give it a moment, then re-read
      // (twice, in case the first lands mid-check).
      for (const ms of [2500, 4000]) {
        await new Promise(res => setTimeout(res, ms));
        const d = await load(viewWeek, {
          fresh: true
        });
        if (d && !d.checking) break;
      }
    } catch (e) {
      if (aliveRef.current) setErr(String(e && e.message || e));
    }
    if (aliveRef.current) setBusy(false);
  };
  const saveManual = async url => {
    setBusy(true);
    setManualMsg(null);
    try {
      const r = await apiFetch("/api/ewhispers/manual", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          url
        })
      });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
      setManualMsg(url ? "Saved — showing that post." : "Cleared.");
      setManualUrl("");
      setViewWeek(null);
      await load(null, {
        fresh: true
      });
    } catch (e) {
      setManualMsg(String(e && e.message || e));
    }
    if (aliveRef.current) setBusy(false);
  };

  // Esc closes the enlarged view; the magnify state resets with it.
  useEffect(() => {
    if (!zoom) {
      setLbZoom(false);
      setFullBroken(false);
      return undefined;
    }
    const onKey = e => {
      if (e.key === "Escape") setZoom(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoom]);
  const post = data && data.post;
  const hasImage = !!(post && post.image_url) && !imgBroken;
  const canEmbed = !!(post && post.post_id) && !hasImage;
  const imgSrc = useEwImageSrc(apiFetch, hasImage ? post : null, "card");
  const fullSrc = useEwImageSrc(apiFetch, zoom && hasImage ? post : null, "full");
  // The post text minus link clutter; cashtags already render as chips.
  const cleanText = post && post.text ? post.text.replace(/https?:\/\/t\.co\/\w+/g, " ").replace(/pic\.twitter\.com\/\w+/g, " ").replace(/\s+/g, " ").trim() : null;
  const aspect = post && post.image_width && post.image_height ? {
    aspectRatio: `${post.image_width} / ${post.image_height}`
  } : null;
  const navBtn = (week, label, title) => /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "tsy-serbtn",
    disabled: !week || busy,
    title: title,
    onClick: () => setViewWeek(week)
  }, label);
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card ew-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Weekly earnings calendar \xB7 @eWhispers on X"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Earnings Whispers")), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl ew-ctrl"
  }, data && data.weeks && data.weeks.length > 1 && /*#__PURE__*/React.createElement(React.Fragment, null, navBtn(data.prev_week, "‹", data.prev_week ? `Show ${data.prev_week}` : "No earlier week stored"), !data.is_current_week && navBtn(data.current_week, "Current week", "Back to the current trading week"), navBtn(data.next_week, "›", data.next_week ? `Show ${data.next_week}` : "No later week stored")), post && /*#__PURE__*/React.createElement("a", {
    className: "scan-run-btn ew-xbtn",
    href: post.post_url,
    target: "_blank",
    rel: "noopener noreferrer",
    title: "Open the original post on X in a new tab."
  }, "View on X \u2197"), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: refresh,
    disabled: busy || !!(data && data.checking),
    title: "Ask the server to check @eWhispers for a newer weekly post now."
  }, busy || data && data.checking ? "Checking…" : "Refresh"))), data && data.week_label && /*#__PURE__*/React.createElement("div", {
    className: "ew-weekline"
  }, /*#__PURE__*/React.createElement("span", {
    className: "tsy-datechip num"
  }, data.week_label), data.showing === "previous" && /*#__PURE__*/React.createElement("span", {
    className: "tsy-pill warn",
    title: data.note || ""
  }, "LAST WEEK"), data.post_source === "pinned" && /*#__PURE__*/React.createElement("span", {
    className: "tsy-pill mut",
    title: "Taken from the post @eWhispers currently has pinned, which is always the current week."
  }, "PINNED"), data.showing === "manual" && /*#__PURE__*/React.createElement("span", {
    className: "tsy-pill mut",
    title: "This post was supplied manually below."
  }, "MANUAL"), data.showing === "history" && /*#__PURE__*/React.createElement("span", {
    className: "tsy-pill mut"
  }, "ARCHIVE"), post && post.published_at && /*#__PURE__*/React.createElement("span", {
    className: "muted ew-posted"
  }, "posted ", fmtUSDate(post.published_at))), data && data.showing === "previous" && /*#__PURE__*/React.createElement("div", {
    className: "ew-stale",
    title: data.note || ""
  }, /*#__PURE__*/React.createElement("b", null, "This is last week\u2019s calendar, not this week\u2019s."), " ", data.note || "This week's post has not been detected yet.", " ", data.credentials ? "Press Refresh to look again." : "Automatic detection needs an X API key (X_BEARER_TOKEN), " + "or paste this week's @eWhispers post link below."), data && data.week_assumed && data.showing === "current" && /*#__PURE__*/React.createElement("div", {
    className: "ew-assumed",
    title: data.note || ""
  }, data.note), err && !data && /*#__PURE__*/React.createElement("div", {
    className: "tsy-err"
  }, "Couldn't reach the dashboard server: ", err), !data && !err && /*#__PURE__*/React.createElement("div", {
    className: "ew-body",
    "aria-busy": "true",
    "aria-label": "Loading Earnings Whispers calendar\u2026"
  }, /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "38%"
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel ew-imgskel"
  })), data && !post && /*#__PURE__*/React.createElement("div", {
    className: "ew-empty"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ew-empty-title"
  }, "No weekly calendar to show yet"), /*#__PURE__*/React.createElement("div", {
    className: "ew-note"
  }, data.note || "Nothing has been detected for this week so far.")), post && /*#__PURE__*/React.createElement("div", {
    className: "ew-body"
  }, hasImage && !imgSrc && /*#__PURE__*/React.createElement("div", {
    className: "skel ew-imgskel",
    "aria-hidden": "true"
  }), hasImage && imgSrc && /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ew-imgwrap",
    onClick: () => setZoom(true),
    title: "Click to enlarge the calendar.",
    style: aspect || undefined
  }, /*#__PURE__*/React.createElement("img", {
    className: "ew-img",
    src: imgSrc,
    loading: "lazy",
    decoding: "async",
    referrerPolicy: "no-referrer",
    alt: `Earnings Whispers calendar — ${data.week_label || "weekly earnings"}`,
    onError: () => setImgBroken(true)
  }), /*#__PURE__*/React.createElement("span", {
    className: "ew-zoom",
    "aria-hidden": "true"
  }, "\u2922 enlarge")), canEmbed && /*#__PURE__*/React.createElement(React.Fragment, null, post.image_status && post.image_status !== "ok" && /*#__PURE__*/React.createElement("div", {
    className: "ew-note"
  }, "Calendar image unavailable (", String(post.image_status).replace(/_/g, " "), ") \u2014 showing the X post. Refresh retries."), /*#__PURE__*/React.createElement(XPostEmbed, {
    postId: post.post_id,
    postUrl: post.post_url
  })), !hasImage && !canEmbed && /*#__PURE__*/React.createElement("div", {
    className: "ew-note"
  }, "This post has no displayable media \u2014", " ", /*#__PURE__*/React.createElement("a", {
    className: "ew-xlink",
    href: post.post_url,
    target: "_blank",
    rel: "noopener noreferrer"
  }, "view it on X \u2197"), "."), (post.images || []).length > 0 && hasImage && /*#__PURE__*/React.createElement("div", {
    className: "ew-note"
  }, "+", post.images.length, " more image", post.images.length > 1 ? "s" : "", " in the original post."), /*#__PURE__*/React.createElement(EwTickerChips, {
    tickers: post.tickers,
    onOpenTicker: onOpenTicker
  }), cleanText && /*#__PURE__*/React.createElement("div", {
    className: "ew-textwrap"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ew-texttoggle",
    onClick: () => setShowText(v => !v)
  }, showText ? "Hide post text" : "Show post text"), showText && /*#__PURE__*/React.createElement("div", {
    className: "ew-text"
  }, cleanText))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-foot ew-foot"
  }, /*#__PURE__*/React.createElement("span", null, data && data.attribution || "Calendar and post © Earnings Whispers (@eWhispers) on X"), /*#__PURE__*/React.createElement("span", {
    className: "ew-foot-right"
  }, data && data.last_checked ? `Checked for newer posts ${new Date(data.last_checked).toLocaleString()}` : data && !data.credentials ? "Automatic checks are off (no X API key on the server)" : "Not checked yet", " · ", /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ew-texttoggle",
    onClick: () => setManualOpen(v => !v)
  }, manualOpen ? "Hide manual link" : "Set post link manually"))), manualOpen && /*#__PURE__*/React.createElement("div", {
    className: "ew-manual"
  }, /*#__PURE__*/React.createElement("input", {
    className: "sb-select ew-manual-in",
    type: "url",
    value: manualUrl,
    placeholder: data && data.manual_url ? data.manual_url : "https://x.com/eWhispers/status/…",
    onChange: e => setManualUrl(e.target.value),
    disabled: busy
  }), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    disabled: busy || !manualUrl.trim(),
    onClick: () => saveManual(manualUrl.trim())
  }, "Save"), data && data.manual_url && /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    disabled: busy,
    onClick: () => saveManual(""),
    title: "Remove the manually supplied post and go back to automatic detection."
  }, "Clear"), manualMsg && /*#__PURE__*/React.createElement("span", {
    className: "ew-note"
  }, manualMsg), /*#__PURE__*/React.createElement("span", {
    className: "ew-note"
  }, "Paste the weekly calendar post's link from @eWhispers if automatic detection is unavailable.")), zoom && hasImage && /*#__PURE__*/React.createElement("div", {
    className: `ew-lightbox${lbZoom ? " zoomed" : ""}`,
    role: "dialog",
    "aria-modal": "true",
    "aria-label": "Earnings Whispers weekly calendar, enlarged",
    onClick: () => setZoom(false)
  }, /*#__PURE__*/React.createElement("img", {
    src: !fullBroken && fullSrc || imgSrc,
    alt: `Earnings Whispers calendar — ${data.week_label || ""}`,
    title: lbZoom ? "Tap to fit the screen" : "Tap to magnify",
    onClick: e => {
      e.stopPropagation();
      setLbZoom(v => !v);
    },
    onError: () => setFullBroken(true)
  }), !lbZoom && /*#__PURE__*/React.createElement("span", {
    className: "ew-lb-hint",
    "aria-hidden": "true"
  }, "tap image to magnify \xB7 tap outside to close"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "hk-close ew-lb-close",
    "aria-label": "Close",
    onClick: () => setZoom(false)
  }, "\xD7")));
}
function EarnOpsAlerts({
  rows
}) {
  // Toasts for new row alerts (spec list computed server-side per row).
  const [toasts, setToasts] = useState([]);
  const seenKey = () => "jerry_eop_seen_" + new Date().toISOString().slice(0, 10);
  useEffect(() => {
    if (!rows || !rows.length) return;
    let seen;
    try {
      seen = new Set(JSON.parse(localStorage.getItem(seenKey())) || []);
    } catch (e) {
      seen = new Set();
    }
    const fresh = [];
    for (const r of rows) {
      for (const a of r.alerts || []) {
        const k = `${r.ticker}|${a}`;
        if (!seen.has(k)) {
          fresh.push({
            id: k + Date.now(),
            sym: r.ticker,
            msg: a
          });
          seen.add(k);
        }
      }
    }
    try {
      localStorage.setItem(seenKey(), JSON.stringify([...seen]));
    } catch (e) {}
    if (fresh.length) setToasts(ts => [...ts, ...fresh.slice(0, 3)].slice(-3));
  }, [rows]);
  useEffect(() => {
    if (!toasts.length) return undefined;
    const id = setTimeout(() => setToasts(ts => ts.slice(1)), 12000);
    return () => clearTimeout(id);
  }, [toasts]);
  if (!toasts.length) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: "toast-stack",
    "aria-live": "polite"
  }, toasts.map(t => /*#__PURE__*/React.createElement("button", {
    key: t.id,
    className: "toast toast-radar toast-long",
    onClick: () => setToasts(ts => ts.filter(x => x.id !== t.id))
  }, /*#__PURE__*/React.createElement("span", {
    className: "toast-ico"
  }, "\u25E7"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("b", null, t.sym), " \u2014 ", t.msg))));
}
function EarnOpsRow({
  r,
  open,
  onToggle,
  onOpenTicker,
  onOpenIntraday,
  demo
}) {
  const act = EOP_ACTION[r.action] || [r.action, "mut"];
  const em = r.implied,
    hist = r.hist,
    ave = r.actual_vs_expected,
    ivh = r.iv_vs_hist;
  const plan = r.plan,
    sd = r.score_detail || {};
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("tr", {
    className: `eop-row ${open ? "open" : ""}`,
    onClick: onToggle,
    title: "Click to expand the score breakdown and trade plan."
  }, /*#__PURE__*/React.createElement("td", {
    className: "eop-tk"
  }, /*#__PURE__*/React.createElement("b", null, r.ticker), demo || r.demo ? /*#__PURE__*/React.createElement("span", {
    className: "eop-demo"
  }, "DEMO") : null, /*#__PURE__*/React.createElement("div", {
    className: "eop-co"
  }, r.company || "")), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("div", {
    className: "eop-scorebar",
    title: `Opportunity score ${r.score}/100`
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      width: `${r.score}%`
    },
    className: r.score >= 65 ? "hi" : r.score >= 45 ? "mid" : "lo"
  }), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, r.score))), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${act[1]}`
  }, act[0])), /*#__PURE__*/React.createElement("td", {
    className: "eop-setup"
  }, EOP_SETUP_LABEL[r.setup] || r.setup, /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, (r.status || "").replace(/_/g, " "))), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.report_date ? r.report_date.slice(5) : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, r.timing || "—", r.days_to != null ? ` · ${r.days_to === 0 ? "today" : r.days_to > 0 ? `in ${r.days_to}d` : `${-r.days_to}d ago`}` : "")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.price != null ? fmt$(r.price, r.price >= 1000 ? 0 : 2) : "—"), /*#__PURE__*/React.createElement("td", {
    className: `num ${r.change_pct != null ? r.change_pct >= 0 ? "cu" : "cd" : ""}`
  }, eopPct(r.change_pct)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.rel_volume != null ? r.rel_volume.toFixed(1) + "×" : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num",
    title: em ? `Expected move ±${em.pct}% (±$${em.dollars}) → range ${em.lower}–${em.upper}` : "Implied move unavailable"
  }, em ? `±${em.pct}%` : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, em ? `$${em.lower}–${em.upper}` : "")), /*#__PURE__*/React.createElement("td", {
    className: "num",
    title: hist ? `Historical earnings reactions (n=${hist.n}): avg |${hist.avg_abs}|%, median |${hist.med_abs}|%, last ${hist.last >= 0 ? "+" : ""}${hist.last}%` : "Needs ≥3 past reactions"
  }, hist ? `${hist.avg_abs}%` : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, hist ? `med ${hist.med_abs} · last ${hist.last >= 0 ? "+" : ""}${hist.last}` : "")), /*#__PURE__*/React.createElement("td", {
    title: ivh ? `Implied ${ivh.ratio}× the historical average move` : ""
  }, ivh ? /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${ivh.label === "rich" ? "down" : ivh.label === "cheap" ? "up" : "mut"}`
  }, ivh.label.toUpperCase(), " ", ivh.ratio, "\xD7") : "—"), /*#__PURE__*/React.createElement("td", {
    title: ave ? `|actual| ${ave.actual}% vs expected ${ave.expected}% — basis: ${ave.basis}` : "Not reported yet (or no basis)"
  }, ave ? /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${ave.label === "exceeded" ? "up" : ave.label === "undershot" ? "down" : "mut"}`
  }, ave.label.toUpperCase(), " ", ave.ratio, "\xD7") : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.options_volume != null ? (r.options_volume / 1000).toFixed(0) + "k" : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, "OI ", r.open_interest != null ? (r.open_interest / 1000).toFixed(0) + "k" : "—")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.atm_iv != null ? r.atm_iv.toFixed(0) : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, r.spread ? `spr ${r.spread.label}` : "")), /*#__PURE__*/React.createElement("td", null, r.weekly_options === true ? "✓" : r.weekly_options === false ? "—" : "?"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, eopMcap(r.market_cap), /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, r.sector || ""))), open && /*#__PURE__*/React.createElement("tr", {
    className: "eop-detail"
  }, /*#__PURE__*/React.createElement("td", {
    colSpan: "16"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eop-dgrid"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("em", null, "WHY IT RANKS (", r.score, "/100)"), (sd.reasons || []).map((x, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "eop-li up"
  }, "\u25B8 ", x)), /*#__PURE__*/React.createElement("em", {
    style: {
      marginTop: 8
    }
  }, "RISKS"), (sd.risks || []).length ? (sd.risks || []).map((x, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "eop-li down"
  }, "\u25B8 ", x)) : /*#__PURE__*/React.createElement("div", {
    className: "eop-li"
  }, "\u2014"), /*#__PURE__*/React.createElement("em", {
    style: {
      marginTop: 8
    }
  }, "CONFIRMS / INVALIDATES"), /*#__PURE__*/React.createElement("div", {
    className: "eop-li"
  }, "confirm: ", r.confirm_text || "—"), /*#__PURE__*/React.createElement("div", {
    className: "eop-li"
  }, "invalidate: ", r.invalidate_text || "—")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("em", null, "TRADE PLAN"), plan ? /*#__PURE__*/React.createElement("div", {
    className: "eop-plan num"
  }, /*#__PURE__*/React.createElement("span", null, "bias ", /*#__PURE__*/React.createElement("b", {
    className: plan.bias === "long" ? "cu" : plan.bias === "short" ? "cd" : ""
  }, plan.bias)), plan.entry != null && /*#__PURE__*/React.createElement("span", null, "entry ", /*#__PURE__*/React.createElement("b", null, plan.entry)), plan.confirmation != null && /*#__PURE__*/React.createElement("span", null, "confirm ", /*#__PURE__*/React.createElement("b", null, plan.confirmation)), plan.max_chase != null && /*#__PURE__*/React.createElement("span", null, "max chase ", /*#__PURE__*/React.createElement("b", null, plan.max_chase)), plan.invalidation != null && /*#__PURE__*/React.createElement("span", null, "stop ", /*#__PURE__*/React.createElement("b", null, plan.invalidation)), plan.target1 != null && /*#__PURE__*/React.createElement("span", null, "T1 ", /*#__PURE__*/React.createElement("b", null, plan.target1)), plan.target2 != null && /*#__PURE__*/React.createElement("span", null, "T2 ", /*#__PURE__*/React.createElement("b", null, plan.target2)), plan.rr != null && /*#__PURE__*/React.createElement("span", null, "R:R ", /*#__PURE__*/React.createElement("b", null, plan.rr)), /*#__PURE__*/React.createElement("span", null, "hold ", /*#__PURE__*/React.createElement("b", null, plan.holding)), plan.note && /*#__PURE__*/React.createElement("span", {
    className: "eop-plannote"
  }, plan.note)) : /*#__PURE__*/React.createElement("div", {
    className: "eop-li"
  }, "No actionable plan \u2014 ", r.action === "no_trade" ? "explicit NO TRADE" : "not confirmed yet", "."), /*#__PURE__*/React.createElement("em", {
    style: {
      marginTop: 8
    }
  }, "PAST EARNINGS REACTIONS"), /*#__PURE__*/React.createElement("div", {
    className: "eop-hist num"
  }, hist && hist.moves ? hist.moves.map((m, i) => /*#__PURE__*/React.createElement("span", {
    key: i,
    className: m >= 0 ? "cu" : "cd"
  }, m >= 0 ? "+" : "", m)) : "—"), /*#__PURE__*/React.createElement("em", {
    style: {
      marginTop: 8
    }
  }, "LEVELS"), /*#__PURE__*/React.createElement("div", {
    className: "eop-plan num"
  }, r.prev_high != null && /*#__PURE__*/React.createElement("span", null, "PDH ", /*#__PURE__*/React.createElement("b", null, r.prev_high)), r.prev_low != null && /*#__PURE__*/React.createElement("span", null, "PDL ", /*#__PURE__*/React.createElement("b", null, r.prev_low)), r.pm_high != null && /*#__PURE__*/React.createElement("span", null, "PM H ", /*#__PURE__*/React.createElement("b", null, r.pm_high)), r.pm_low != null && /*#__PURE__*/React.createElement("span", null, "PM L ", /*#__PURE__*/React.createElement("b", null, r.pm_low)), r.or_high != null && /*#__PURE__*/React.createElement("span", null, "OR H ", /*#__PURE__*/React.createElement("b", null, r.or_high)), r.or_low != null && /*#__PURE__*/React.createElement("span", null, "OR L ", /*#__PURE__*/React.createElement("b", null, r.or_low)), r.vwap && /*#__PURE__*/React.createElement("span", null, "VWAP ", /*#__PURE__*/React.createElement("b", null, r.vwap.vwap), " (", r.vwap.above ? "above" : "below", r.vwap.event ? ` · ${r.vwap.event}` : "", ")"), r.gap && /*#__PURE__*/React.createElement("span", null, "gap ", /*#__PURE__*/React.createElement("b", null, eopPct(r.gap.gap_pct)), " fill @ ", /*#__PURE__*/React.createElement("b", null, r.gap.fill_level), r.gap.filled != null ? r.gap.filled ? " (filled)" : " (open)" : ""), r.day_high != null && /*#__PURE__*/React.createElement("span", null, "post-E H/L ", /*#__PURE__*/React.createElement("b", null, r.day_high, "/", r.day_low))), /*#__PURE__*/React.createElement("div", {
    className: "eop-actions"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "scan-run-btn",
    onClick: e => {
      e.stopPropagation();
      onOpenIntraday && onOpenIntraday(r.ticker);
    }
  }, "Intraday chart (VWAP + levels)"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "scan-run-btn",
    onClick: e => {
      e.stopPropagation();
      onOpenTicker && onOpenTicker(r.ticker);
    }
  }, "Open in Analyze (chart + EM + chain)")))))));
}
function EarningsOpsTab({
  apiFetch,
  onOpenTicker,
  onOpenIntraday
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [section, setSection] = useState("all");
  const [sortK, setSortK] = useState("score");
  const [sortD, setSortD] = useState(-1);
  const [openTk, setOpenTk] = useState(null);
  const [flt, setFlt] = useState({
    window: "all",
    timing: "all",
    watchOnly: false,
    weeklyOnly: false,
    hiRelVol: false,
    hiOptVol: false,
    hiIV: false,
    largeCap: false,
    confirmedOnly: false,
    hideNoTrade: true
  });
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/earnings_scan");
      const d = await r.json();
      setBoard(d);
      return d;
    } catch (e) {
      setErr(String(e));
      return null;
    }
  };
  const watchScan = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 4000);
  };
  useEffect(() => {
    load().then(d => {
      if (!d) return;
      if (d.status && d.status.scanning) {
        watchScan();
        return;
      }
      const age = d.status && d.status.last_scan ? Date.now() - new Date(d.status.last_scan).getTime() : Infinity;
      if (age > 30 * 60000) {
        apiFetch("/api/earnings_scan/scan").catch(() => {});
        watchScan();
      }
    });
    return () => pollRef.current && clearInterval(pollRef.current);
  }, []);
  const rescan = async () => {
    setErr(null);
    try {
      await apiFetch("/api/earnings_scan/scan?force=1");
    } catch (e) {
      setErr(String(e));
      return;
    }
    await load();
    watchScan();
  };
  const status = board && board.status || {};
  const allRows = board && board.rows || [];
  const demo = !!(board && board.demo);
  const filtered = useMemo(() => allRows.filter(r => {
    if (section !== "all" && r.bucket !== section) return false;
    const d = r.days_to;
    if (flt.window === "today" && d !== 0) return false;
    if (flt.window === "tomorrow" && d !== 1) return false;
    if (flt.window === "week" && !(d != null && d >= 0 && d <= 5)) return false;
    if (flt.window === "reported" && !r.reported_recently) return false;
    if (flt.timing === "bmo" && r.timing !== "BMO") return false;
    if (flt.timing === "amc" && r.timing !== "AMC") return false;
    if (flt.weeklyOnly && r.weekly_options !== true) return false;
    if (flt.hiRelVol && !(r.rel_volume != null && r.rel_volume >= 1.5)) return false;
    if (flt.hiOptVol && !(r.options_volume != null && r.options_volume >= 5000)) return false;
    if (flt.hiIV && !(r.atm_iv != null && r.atm_iv >= 60)) return false;
    if (flt.largeCap && !(r.market_cap != null && r.market_cap >= 10e9)) return false;
    if (flt.confirmedOnly && !(r.status === "confirmed_long" || r.status === "confirmed_short")) return false;
    if (flt.hideNoTrade && r.status === "no_trade" && section !== "no_trade") return false;
    return true;
  }), [allRows, section, flt]);
  const sorted = useMemo(() => {
    const key = r => {
      switch (sortK) {
        case "ticker":
          return r.ticker || "";
        case "report":
          return r.report_date || "9999";
        case "price":
          return r.price ?? -1;
        case "chg":
          return r.change_pct ?? -999;
        case "relvol":
          return r.rel_volume ?? -1;
        case "im":
          return (r.implied && r.implied.pct) ?? -1;
        case "hist":
          return (r.hist && r.hist.avg_abs) ?? -1;
        case "ivh":
          return (r.iv_vs_hist && r.iv_vs_hist.ratio) ?? -1;
        case "ave":
          return (r.actual_vs_expected && r.actual_vs_expected.ratio) ?? -1;
        case "optvol":
          return r.options_volume ?? -1;
        case "iv":
          return r.atm_iv ?? -1;
        case "mcap":
          return r.market_cap ?? -1;
        default:
          return r.score ?? 0;
      }
    };
    return [...filtered].sort((a, b) => {
      const ka = key(a),
        kb = key(b);
      // sortD −1 = descending (big first) — was inverted (* -sortD) since
      // v3.63, so "Score ↓" actually showed the LOWEST scores first.
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * sortD;
    });
  }, [filtered, sortK, sortD]);
  const th = (label, k, tip) => /*#__PURE__*/React.createElement("th", {
    className: sortK === k ? "on" : "",
    title: tip || `Sort by ${label}`,
    onClick: () => {
      if (sortK === k) setSortD(d => -d);else {
        setSortK(k);
        setSortD(-1);
      }
    }
  }, label, sortK === k ? sortD === -1 ? " ↓" : " ↑" : "");
  const chip = (k, label) => /*#__PURE__*/React.createElement("button", {
    key: k,
    type: "button",
    className: `tsy-serbtn ${flt[k] ? "on" : ""}`,
    onClick: () => setFlt(f => ({
      ...f,
      [k]: !f[k]
    }))
  }, label);
  const counts = useMemo(() => {
    const c = {};
    for (const r of allRows) c[r.bucket] = (c[r.bucket] || 0) + 1;
    c.all = allRows.length;
    return c;
  }, [allRows]);
  return /*#__PURE__*/React.createElement("div", {
    className: "eop"
  }, /*#__PURE__*/React.createElement(EarnOpsAlerts, {
    rows: allRows
  }), /*#__PURE__*/React.createElement(CardErrorBoundary, {
    label: "Earnings Whispers"
  }, /*#__PURE__*/React.createElement(EarningsWhispersCard, {
    apiFetch: apiFetch,
    onOpenTicker: onOpenTicker
  })), /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Earnings opportunity scanner \xB7 your watchlist \xB7 \u22124 to +8 days"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Earnings Opportunities")), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl"
  }, board && board.spy_chg != null && /*#__PURE__*/React.createElement("span", {
    className: "tsy-datechip num",
    title: "SPY day change \u2014 market alignment input to the score."
  }, "SPY ", eopPct(board.spy_chg, 2)), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: rescan,
    disabled: !!status.scanning
  }, status.scanning ? `Scanning… ${status.scanned || 0}/${status.total || 0}` : "Rescan"))), demo && /*#__PURE__*/React.createElement("div", {
    className: "eop-demobar"
  }, "DEMO DATA \u2014 live providers unavailable; rows are seeded examples so the workflow stays testable. Nothing here is a real quote."), err && /*#__PURE__*/React.createElement("div", {
    className: "tsy-err"
  }, err), status.last_scan && /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, "Last scan ", new Date(status.last_scan).toLocaleString(), " \xB7 ", allRows.length, " candidates", status.error ? ` · ${status.error}` : ""), /*#__PURE__*/React.createElement("div", {
    className: "eop-sections"
  }, EOP_SECTIONS.map(([k, label]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    type: "button",
    className: `eop-sec ${section === k ? "on" : ""}`,
    onClick: () => setSection(k)
  }, label, " ", /*#__PURE__*/React.createElement("b", null, counts[k] || 0)))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl eop-filters"
  }, /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: flt.window,
    onChange: e => setFlt(f => ({
      ...f,
      window: e.target.value
    }))
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any date"), /*#__PURE__*/React.createElement("option", {
    value: "today"
  }, "Today"), /*#__PURE__*/React.createElement("option", {
    value: "tomorrow"
  }, "Tomorrow"), /*#__PURE__*/React.createElement("option", {
    value: "week"
  }, "This week"), /*#__PURE__*/React.createElement("option", {
    value: "reported"
  }, "Recently reported")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: flt.timing,
    onChange: e => setFlt(f => ({
      ...f,
      timing: e.target.value
    }))
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "BMO + AMC"), /*#__PURE__*/React.createElement("option", {
    value: "bmo"
  }, "Before open"), /*#__PURE__*/React.createElement("option", {
    value: "amc"
  }, "After close")), chip("weeklyOnly", "Weeklys"), chip("hiRelVol", "RelVol ≥1.5×"), chip("hiOptVol", "OptVol ≥5k"), chip("hiIV", "IV ≥60"), chip("largeCap", "Large cap"), chip("confirmedOnly", "Confirmed"), chip("hideNoTrade", "Hide no-trade"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 11.5
    }
  }, sorted.length, " shown")), /*#__PURE__*/React.createElement("div", {
    className: "eop-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "eop-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, th("Ticker", "ticker", "The reporting company's symbol. Click a row to expand its full setup breakdown."), th("Score", "score", "Earnings Opportunity Score 0–100 — liquidity, rel volume, options liquidity, weeklys, implied-vs-historical edge, move-vs-expected, confirmation, spread, market alignment, R:R. Click a row for the full breakdown."), /*#__PURE__*/React.createElement("th", {
    title: "What the scanner suggests doing with this name \u2014 or NO TRADE when the setup does not qualify."
  }, "Action"), /*#__PURE__*/React.createElement("th", {
    title: "Which setup pattern this name matches, and where that setup currently stands."
  }, "Setup \xB7 status"), th("Report", "report", "Report date + BMO/AMC (from the earnings-dates timestamp)."), th("Price", "price", "Latest traded price for the stock going into its report."), th("Day %", "chg", "Session-aware change (includes pre/after-market when that's the latest print)."), th("RelVol", "relvol", "Today's volume ÷ average volume."), th("Imp move", "im", "ATM straddle mid — the option market's expected move. ± range shown beneath."), th("Hist move", "hist", "This name's own past earnings reactions: average |move|, median, last."), th("Imp/Hist", "ivh", "Implied ÷ historical average — RICH ≥1.3×, CHEAP ≤0.75×."), th("Act/Exp", "ave", "Post-print: |actual| ÷ expected. Basis = pre-print implied recorded by this scanner, else historical average (labeled in tooltip)."), th("OptVol", "optvol", "Front-expiry options volume + open interest."), th("IV", "iv", "ATM implied volatility + ATM spread quality."), /*#__PURE__*/React.createElement("th", {
    title: "Weekly options available"
  }, "Wkly"), th("MCap", "mcap", "Market capitalisation — a size filter; the biggest names usually have the deepest options."))), /*#__PURE__*/React.createElement("tbody", null, sorted.map(r => /*#__PURE__*/React.createElement(EarnOpsRow, {
    key: r.ticker,
    r: r,
    demo: demo,
    open: openTk === r.ticker,
    onToggle: () => setOpenTk(openTk === r.ticker ? null : r.ticker),
    onOpenTicker: onOpenTicker,
    onOpenIntraday: onOpenIntraday
  })))), !sorted.length && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, allRows.length ? "Nothing matches the filters." : status.scanning ? "Scanning your watchlist for earnings names…" : "No earnings candidates in the −4…+8 day window. Rescan after the watchlist board refreshes.")), /*#__PURE__*/React.createElement("div", {
    className: "tsy-foot"
  }, board && board.note, " Sources: watchlist board (earnings dates, mcap, sector) \xB7 Schwab quotes/chains/intraday (VWAP, levels) \xB7 yfinance history (past reactions, fallback). EPS consensus/surprise: no free source \u2014 not shown.")));
}
Object.assign(window, {
  EarningsOpsTab: React.memo(EarningsOpsTab)
});
})();
