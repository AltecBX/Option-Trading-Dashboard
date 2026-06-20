// App shell + mount — split out of the app.jsx monolith (v1.40).
// Loads last, after app-lib.js and app-cards.js, and resolves their
// bindings as window globals published by those files.

// Single source of truth for the app version. The sidebar pill renders
// this, and index.html's ?v= cache-bust is kept identical to it so there
// is ONE version number everywhere. Bump both together on each change.
const APP_VERSION = "1.84";
// Published to window because the sidebar version pill renders from a
// component in app-cards.js and resolves APP_VERSION as a bare global.
Object.assign(window, { APP_VERSION });

function App() {
  // Floating-point-safe strike key. yfinance can return strikes with
  // tiny FP drift (e.g., 317.5 vs 317.4999999998), so we round to cents
  // and stringify before using strikes as map keys or comparing them.
  // Hoisted to App scope so any card or block can use it directly
  // without redeclaring.
  const skey = s => (Math.round(s * 100) / 100).toFixed(2);

  // API helper. Reads window.__APP_CONFIG (loaded from config.js) to get
  // the absolute backend base URL and API key. In local dev both are
  // empty strings, which means relative URLs and no header — exactly
  // what the colocated Python server expects. In production the config
  // points at Railway and includes a key matching the server's API_KEY
  // env var.
  // Stable identity so React.memo'd cards don't re-render on unrelated state.
  const apiFetch = React.useCallback((path, opts = {}) => {
    const cfg = window.__APP_CONFIG || {};
    const base = (cfg.apiBase || "").replace(/\/$/, "");  // trim trailing slash
    const url = path.startsWith("http") ? path : `${base}${path}`;
    const headers = { ...(opts.headers || {}) };
    if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey;
    return fetch(url, { ...opts, headers });
  }, []);
  const TWEAK_KEY = "weeklyOptionsTimer.tweaks.v1";
  const persistedTweaks = (() => {
    try { return JSON.parse(localStorage.getItem(TWEAK_KEY) || "{}"); }
    catch { return {}; }
  })();
  const TWEAK_DEFAULTS = Object.assign({
    "accent": "emerald",
    "typeface": "sans",
    "density": "comfortable",
    "chartStyle": "candles",
    "layout": "default",
    "theme": "light"
  }, persistedTweaks);

  const [tweakVals, setTweakVal] = window.useTweaks(/*EDITMODE-BEGIN*/TWEAK_DEFAULTS/*EDITMODE-END*/);
  const tweaks = { values: tweakVals, setValue: setTweakVal };

  // Persist tweak values to localStorage so theme, accent, etc. survive a reload.
  useEffect(() => {
    try { localStorage.setItem(TWEAK_KEY, JSON.stringify(tweakVals)); } catch {}
  }, [tweakVals]);

  // ── Persisted settings ────────────────────────────────────────────────
  // Read once from localStorage so the app reopens with the same ticker,
  // weeks, baseline, etc. as you left them.
  const STORAGE_KEY = "weeklyOptionsTimer.settings.v1";
  const persisted = useMemo(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch { return {}; }
  }, []);

  const [ticker, setTicker] = useState(persisted.ticker || "AAPL");
  const [tickerInput, setTickerInput] = useState(persisted.ticker || "AAPL");
  const [weeks, setWeeks] = useState(persisted.weeks ?? 12);
  const [bufferPct, setBufferPct] = useState(persisted.bufferPct ?? 2.0);
  const [baseline, setBaseline] = useState(persisted.baseline || "monday"); // "monday" | "friday"
  const [expiration, setExpiration] = useState(""); // "" = use server default (next Friday)
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [dataVersion, setDataVersion] = useState(0);
  const [navOpen, setNavOpen] = useState(false);      // mobile sidebar drawer
  const [reloadNonce, setReloadNonce] = useState(0);  // manual refresh trigger
  const refreshData = () => setReloadNonce(n => n + 1);
  // Stable ticker switcher (used as a memo-friendly prop for cards).
  const switchTicker = React.useCallback((sym) => {
    setTicker(sym); setTickerInput(sym);
  }, []);
  // Analyst data lifted to App level so the covered-call recommendation
  // engine and other downstream consumers can read it. AnalystCard owns
  // the fetch and reports up via the setAnalystData callback below.
  // Cleared on ticker change so we never use stale data.
  const [analystData, setAnalystData] = useState(null);
  useEffect(() => { setAnalystData(null); }, [ticker]);

  // ── IV Rank for the active ticker (v1.16) ──────────────────────
  // Reuses the watchlist scanner's snapshot endpoint since it already
  // computes iv_rank from local history. Single ticker fetch is cheap.
  // Re-runs on ticker change.
  const [ivRankInfo, setIvRankInfo] = useState(null);
  useEffect(() => {
    if (!ticker) { setIvRankInfo(null); return; }
    setIvRankInfo(null);
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch(`/api/scan?tickers=${encodeURIComponent(ticker)}`);
        if (!r.ok) return;
        const j = await r.json();
        const snap = (j.results || [])[0];
        if (cancelled || !snap) return;
        setIvRankInfo({
          iv_rank: snap.iv_rank != null ? Number(snap.iv_rank) : null,
          iv_pct: snap.iv_pct != null ? Number(snap.iv_pct) : null,
          iv_rank_days: snap.iv_rank_days || 0,
        });
      } catch {}
    })();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker]);

  // Data source status — polled from /api/data_source. Shows badge in
  // brand area indicating Schwab vs yfinance.
  const [dataSource, setDataSource] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await apiFetch("/api/data_source");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!cancelled) setDataSource(j);
      } catch (_) {}
    };
    poll();
    const id = setInterval(skipWhenHidden(poll), 30000);  // refresh every 30s
    return () => { cancelled = true; clearInterval(id); };
  }, []);
  // Unusual Whales health — polled from /api/uw/health. Shows a small
  // status pill alongside the data-source badge so Jerry can see at a
  // glance whether UW is reachable and how much quota remains.
  const [uwHealth, setUwHealth] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await apiFetch("/api/uw/health");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!cancelled) setUwHealth(j);
      } catch (_) {}
    };
    poll();
    const id = setInterval(skipWhenHidden(poll), 60000);  // refresh every 60s
    return () => { cancelled = true; clearInterval(id); };
  }, []);
  // Strike-level flow snapshot for the active ticker. Used by the
  // Suggested Strikes card to show today's UW volume / ask premium /
  // sweep flag next to each candidate strike. Polled only when UW is
  // connected; reuses the cached flow_alerts under the hood.
  const [strikeFlow, setStrikeFlow] = useState([]);
  // Clear immediately on ticker change so stale strikes from the
  // previous symbol never render against the new strike card.
  useEffect(() => { setStrikeFlow([]); }, [ticker]);
  useEffect(() => {
    if (!ticker || !uwHealth?.connected) {
      setStrikeFlow([]);
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await apiFetch(`/api/uw/strike_flow?symbol=${encodeURIComponent(ticker)}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!cancelled) setStrikeFlow(Array.isArray(j.data) ? j.data : []);
      } catch (_) {
        if (!cancelled) setStrikeFlow([]);
      }
    };
    poll();
    const id = setInterval(skipWhenHidden(poll), 30000);  // refresh every 30s
    return () => { cancelled = true; clearInterval(id); };
  }, [ticker, uwHealth?.connected]);

  // Market-wide flow dashboard — polls /api/uw/market_dashboard
  // every 60s when UW is connected. Composed of market tide, sector
  // flow, and recent spike list. Drives the collapsible Market Flow
  // card at the bottom of the page.
  const [marketDashboard, setMarketDashboard] = useState(null);
  useEffect(() => {
    if (!uwHealth?.connected) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await apiFetch("/api/uw/market_dashboard");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!cancelled) setMarketDashboard(j);
      } catch {}
    };
    poll();
    const id = setInterval(skipWhenHidden(poll), 60000);
    return () => { cancelled = true; clearInterval(id); };
  }, [uwHealth?.connected]);
  // Persisted expand/collapse for the market dashboard card.
  const [marketDashOpen, setMarketDashOpen] = useState(() => {
    try { return localStorage.getItem("weeklyOptionsTimer.marketDash.open.v1") === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("weeklyOptionsTimer.marketDash.open.v1", marketDashOpen ? "1" : "0"); } catch {}
  }, [marketDashOpen]);
  // Metric toggle for the by-strike options activity chart (v1.19).
  // "volume" = today's traded contracts, "oi" = total open interest.
  const [oiChartMetric, setOiChartMetric] = useState(() => {
    try { return localStorage.getItem("weeklyOptionsTimer.oiChart.metric.v1") || "volume"; } catch { return "volume"; }
  });
  useEffect(() => {
    try { localStorage.setItem("weeklyOptionsTimer.oiChart.metric.v1", oiChartMetric); } catch {}
  }, [oiChartMetric]);
  // Live quote state — populated by polling effects further down (after
  // dependent state is declared). Components use getLivePrice() to read.
  const [liveQuotes, setLiveQuotes] = useState({}); // {sym: {last, change_pct, source, ts}}
  // Live wall clock — ticks every second so the sidebar timestamp
  // updates without a page refresh. Stops while tab is hidden to
  // avoid pointless re-renders.
  const [nowTs, setNowTs] = useState(() => Date.now());
  useEffect(() => {
    let timer = null;
    const start = () => {
      if (timer) return;
      timer = setInterval(() => {
        if (!document.hidden) setNowTs(Date.now());
      }, 1000);
    };
    const stop = () => {
      if (timer) { clearInterval(timer); timer = null; }
    };
    const onVis = () => {
      if (document.hidden) stop();
      else { setNowTs(Date.now()); start(); }
    };
    document.addEventListener("visibilitychange", onVis);
    start();
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      stop();
    };
  }, []);
  const getLivePrice = (sym) => liveQuotes[sym]?.last ?? null;
  const [selectedStrategy, setSelectedStrategy] = useState(persisted.selectedStrategy || "short_strangle");
  const [thetaSide, setThetaSide] = useState(persisted.thetaSide || "call");
  // Strike picker: "delta" picks short strikes by absolute delta target,
  // "buffer" uses the historical-range + buffer% method. Delta is the
  // professional convention and what tastytrade et al. teach for premium
  // selling. Default delta + 0.20 matches Jerry's preference.
  const [strikeMode, setStrikeMode] = useState(persisted.strikeMode || "delta");
  const [targetDelta, setTargetDelta] = useState(persisted.targetDelta ?? 0.20);
  // Chart overlays — toggleable moving averages
  const [showMA50, setShowMA50] = useState(persisted.showMA50 ?? false);
  const [showMA200, setShowMA200] = useState(persisted.showMA200 ?? false);
  const [showEMA21, setShowEMA21] = useState(persisted.showEMA21 ?? false);
  const [showProbCone, setShowProbCone] = useState(persisted.showProbCone ?? false);
  const [showRSI, setShowRSI] = useState(persisted.showRSI ?? false);
  // Strategy mode (Phase C, v1.12). "both" shows the CC and CSP banners,
  // strike cards, and analyst warning columns side by side. "cc" hides
  // the CSP side, "csp" hides the CC side. Default is "both" to preserve
  // v1.11 behavior. Persisted so the picked focus survives reload.
  const [strategyMode, setStrategyMode] = useState(persisted.strategyMode || "both");
  const [activeTab, setActiveTab] = useState(() => {
    try {
      const t = localStorage.getItem(TAB_KEY);
      return TABS.some(x => x.id === t) ? t : "trade";
    } catch { return "trade"; }
  });
  // Per-tab scroll memory. Switching tabs used to jump to the top every
  // time. Instead, save where you were on the tab you are leaving and
  // restore where you were on the tab you are entering. Panels stay
  // mounted but toggle display, so the page height changes on switch;
  // restore after layout settles via requestAnimationFrame.
  const tabScroll = React.useRef({});
  const changeTab = React.useCallback((t) => {
    setActiveTab((prev) => {
      if (prev === t) return prev;
      tabScroll.current[prev] = window.scrollY || window.pageYOffset || 0;
      const y = tabScroll.current[t] ?? 0;
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          try { window.scrollTo({ top: y, behavior: "auto" }); } catch {}
        });
      });
      return t;
    });
    try { localStorage.setItem(TAB_KEY, t); } catch {}
  }, []);
  // Visible days on the price chart. Two ways to drive it: preset
  // buttons (30D/60D/120D/250D) which set chartDays, or mouse interaction
  // (wheel zoom or drag-pan) which sets a custom viewRange. When viewRange
  // is non-null it overrides chartDays. Switching ticker or clicking a
  // preset clears viewRange to fall back to the chartDays preset.
  const [chartDays, setChartDays] = useState(persisted.chartDays ?? 120);
  const [viewRange, setViewRange] = useState(null);
  useEffect(() => { setViewRange(null); }, [ticker]);

  // Watchlist scanner state. scanResults is keyed by symbol so re-scans
  // mutate in place. scanRunning flips during a scan to disable the
  // button. Last-scan timestamp tells Jerry how stale the data is.
  const [scanResults, setScanResults] = useState({});
  const [scanRunning, setScanRunning] = useState(false);
  const [scanAt, setScanAt] = useState(null);
  // Scanner sort state. null = use default sort (conviction desc for
  // watchlist scanner; original watchlist order for weekly range).
  // Click cycles: null → desc → asc → null.
  const [scanSort, setScanSort] = useState(null); // {key, dir} | null
  const [wrSort, setWrSort] = useState(null); // {key, dir} | null
  const cycleSort = (current, key) => {
    if (!current || current.key !== key) return { key, dir: "desc" };
    if (current.dir === "desc") return { key, dir: "asc" };
    return null; // back to default
  };

  // Score a scan snapshot using the same rules as the main strategy
  // ranker, but adapted for the lighter data we have per-ticker. Picks
  // the single best strategy for that ticker right now and returns
  // {strategy, score, reasons}.
  const scoreSnapshot = (snap) => {
    if (!snap || snap.error || snap.price == null) return null;
    const iv = snap.iv30_avg, hv = snap.hv20;
    const richness = snap.richness;
    const callSafe = snap.call_safe_pct, putSafe = snap.put_safe_pct;
    const earnD = snap.earnings_in_days;
    const change = snap.change_pct || 0;

    const isRich = richness != null && richness >= 1.2;
    const isCheap = richness != null && richness <= 0.95;
    const earnSoon = earnD != null && earnD >= 0 && earnD <= 14;
    const earnVeryClose = earnD != null && earnD >= 0 && earnD <= 7;
    const isBullish = change > 0.5;
    const isBearish = change < -0.5;
    const isNeutral = !isBullish && !isBearish;
    const safeBoth = callSafe != null && putSafe != null && callSafe >= 3 && putSafe >= 3;

    // Score each candidate and keep the best
    const candidates = [];
    if (safeBoth && !earnSoon) {
      let s = 50;
      const reasons = ["both sides safe"];
      if (isRich) { s += 18; reasons.push("premium rich"); }
      if (isNeutral) { s += 8; reasons.push("neutral bias"); }
      if (callSafe >= 5 && putSafe >= 5) { s += 8; reasons.push("wide expected range"); }
      candidates.push({ key: "short_strangle", name: "Short Strangle", score: s, reasons });
    }
    if (safeBoth && callSafe >= 4 && putSafe >= 4 && !earnSoon) {
      let s = 48;
      const reasons = ["defined risk"];
      if (isRich) { s += 16; reasons.push("premium rich"); }
      if (isNeutral) { s += 6; reasons.push("neutral bias"); }
      candidates.push({ key: "iron_condor", name: "Iron Condor", score: s, reasons });
    }
    if (putSafe != null && putSafe >= 3 && !earnSoon) {
      let s = 40;
      const reasons = ["put safe"];
      if (isBullish) { s += 14; reasons.push("bullish bias"); }
      if (isRich) { s += 10; reasons.push("premium rich"); }
      candidates.push({ key: "cash_secured_put", name: "Cash-Secured Put", score: s, reasons });
    }
    if (callSafe != null && callSafe >= 3 && !earnSoon) {
      let s = 38;
      const reasons = ["call safe"];
      if (isBearish) { s += 12; reasons.push("bearish bias"); }
      if (isRich) { s += 8; reasons.push("premium rich"); }
      candidates.push({ key: "covered_call", name: "Covered Call", score: s, reasons });
    }
    if (isCheap && earnSoon) {
      let s = 55;
      const reasons = ["cheap premium", "earnings ahead"];
      if (earnVeryClose) { s += 12; reasons.push("event in 7d"); }
      candidates.push({ key: "long_straddle", name: "Long Straddle", score: s, reasons });
    }
    if (isRich && earnSoon && safeBoth) {
      let s = 44;
      const reasons = ["rich + close earnings", "defined risk"];
      candidates.push({ key: "iron_butterfly", name: "Short Iron Butterfly", score: s, reasons });
    }
    if (isRich && putSafe != null && putSafe >= 4 && !earnSoon) {
      let s = 42;
      const reasons = ["rich premium", "put cushion"];
      candidates.push({ key: "jade_lizard", name: "Jade Lizard", score: s, reasons });
    }
    if (!candidates.length) return null;
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0];
  };

  // Run a scan against /api/scan with the current (filter-aware) watchlist.
  // Batches in chunks of 25 to stay under server-side cap. Updates results
  // live so user sees rows fill in as batches complete.
  const runScan = async () => {
    if (scanRunning || !filteredWatchlistSymbols.length) return;
    setScanRunning(true);
    const symbols = [...filteredWatchlistSymbols];
    const next = {};
    const BATCH = 25;
    try {
      for (let i = 0; i < symbols.length; i += BATCH) {
        const batch = symbols.slice(i, i + BATCH);
        const url = `/api/scan?tickers=${encodeURIComponent(batch.join(","))}`;
        const r = await apiFetch(url);
        if (!r.ok) throw new Error(`scan ${r.status}`);
        const data = await r.json();
        (data.results || []).forEach(snap => {
          next[snap.symbol] = snap;
        });
        // Live update so user sees results fill in
        setScanResults({...next});
      }
      setScanAt(Date.now());
    } catch (err) {
      console.warn("scan failed:", err);
    } finally {
      setScanRunning(false);
    }
  };
  // Weekly range scanner — calls /api/weekly_range in batches of 25 to
  // stay under server-side caps. Aggregates results into a single map.
  // Skips run if already running or watchlist empty. Logs partial errors
  // but continues so a single bad symbol doesn't abort the whole scan.
  const runWeeklyRange = async () => {
    if (weeklyRangeRunning || !filteredWatchlistSymbols.length) return;
    setWeeklyRangeRunning(true);
    setWeeklyRangeError(null);
    const symbols = [...filteredWatchlistSymbols];
    setWeeklyRangeProgress({done: 0, total: symbols.length});
    const next = {};
    const BATCH = 25;
    try {
      for (let i = 0; i < symbols.length; i += BATCH) {
        const batch = symbols.slice(i, i + BATCH);
        const url = `/api/weekly_range?tickers=${encodeURIComponent(batch.join(","))}`;
        const r = await apiFetch(url);
        if (!r.ok) throw new Error(`weekly_range ${r.status}`);
        const data = await r.json();
        (data.results || []).forEach(rng => {
          next[rng.symbol] = rng;
        });
        setWeeklyRangeProgress({done: Math.min(i + BATCH, symbols.length), total: symbols.length});
        // Live update so user sees rows fill in as batches complete
        setWeeklyRange({...next});
      }
      setWeeklyRangeAt(Date.now());
    } catch (err) {
      console.warn("weekly_range failed:", err);
      setWeeklyRangeError(err.message || "Scan failed");
    } finally {
      setWeeklyRangeRunning(false);
    }
  };

  // Pullback scanner — same batching pattern as weekly range. Calls
  // /api/pullback_scan with up to 25 tickers per batch.
  const runPullbackScan = async () => {
    if (pullbackScanRunning || !filteredWatchlistSymbols.length) return;
    setPullbackScanRunning(true);
    setPullbackScanError(null);
    const symbols = [...filteredWatchlistSymbols];
    setPullbackScanProgress({done: 0, total: symbols.length});
    const next = {};
    const BATCH = 25;
    try {
      for (let i = 0; i < symbols.length; i += BATCH) {
        const batch = symbols.slice(i, i + BATCH);
        const url = `/api/pullback_scan?tickers=${encodeURIComponent(batch.join(","))}&days=180`;
        const r = await apiFetch(url);
        if (!r.ok) throw new Error(`pullback_scan ${r.status}`);
        const data = await r.json();
        (data.results || []).forEach(row => {
          next[row.symbol] = row;
        });
        setPullbackScanProgress({done: Math.min(i + BATCH, symbols.length), total: symbols.length});
        setPullbackScan({...next});
      }
      setPullbackScanAt(Date.now());
    } catch (err) {
      console.warn("pullback_scan failed:", err);
      setPullbackScanError(err.message || "Scan failed");
    } finally {
      setPullbackScanRunning(false);
    }
  };

  // Premium richness scanner — UW-driven. One ticker per request
  // (premium_richness needs a single symbol). Sequenced rather than
  // batched so we can show progress; UW basic plan is 120 req/min so
  // a 30-ticker scan finishes well inside the limit.
  const runRichnessScan = async () => {
    if (richnessRunning || !filteredWatchlistSymbols.length) return;
    if (!uwHealth?.connected) {
      setRichnessError("Unusual Whales not connected");
      return;
    }
    setRichnessRunning(true);
    setRichnessError(null);
    const symbols = [...filteredWatchlistSymbols];
    setRichnessProgress({done: 0, total: symbols.length});
    const next = {};
    try {
      for (let i = 0; i < symbols.length; i++) {
        const sym = symbols[i];
        try {
          const r = await apiFetch(`/api/uw/premium_richness?symbol=${encodeURIComponent(sym)}`);
          if (r.ok) {
            const j = await r.json();
            next[sym] = j;
          } else {
            next[sym] = {symbol: sym, error: `HTTP ${r.status}`};
          }
        } catch (e) {
          next[sym] = {symbol: sym, error: String(e)};
        }
        setRichnessProgress({done: i + 1, total: symbols.length});
        setRichnessScan({...next});
      }
      setRichnessAt(Date.now());
    } catch (err) {
      console.warn("richness scan failed:", err);
      setRichnessError(err.message || "Scan failed");
    } finally {
      setRichnessRunning(false);
    }
  };

  // Intraday momentum scanner runner. One backend call per ticker
  // (the backend internally combines Schwab quote + UW flow_score).
  const runMomentumScan = async () => {
    if (momentumRunning || !filteredWatchlistSymbols.length) return;
    setMomentumRunning(true);
    setMomentumError(null);
    const symbols = [...filteredWatchlistSymbols];
    setMomentumProgress({done: 0, total: symbols.length});
    const next = {};
    try {
      for (let i = 0; i < symbols.length; i++) {
        const sym = symbols[i];
        try {
          const r = await apiFetch(`/api/uw/momentum?symbol=${encodeURIComponent(sym)}`);
          if (r.ok) {
            const j = await r.json();
            next[sym] = j;
          } else {
            next[sym] = {symbol: sym, error: `HTTP ${r.status}`};
          }
        } catch (e) {
          next[sym] = {symbol: sym, error: String(e)};
        }
        setMomentumProgress({done: i + 1, total: symbols.length});
        setMomentumScan({...next});
      }
      setMomentumAt(Date.now());
    } catch (err) {
      console.warn("momentum scan failed:", err);
      setMomentumError(err.message || "Scan failed");
    } finally {
      setMomentumRunning(false);
    }
  };

  // Market-wide UW scanner runner. Two-stage workflow:
  //   1. Fetch candidate list from /api/uw/market_scan_candidates
  //      (excludes watchlist tickers).
  //   2. Iterate candidates, calling /api/uw/market_scan_score per
  //      ticker (flow score + earnings + IV rank). Streams progress.
  // Each candidate uses ~3 UW calls (cached), so 50 candidates = up to
  // 150 calls — well inside UW's 120/min if cache is warm.
  const runMarketScan = async () => {
    if (marketScanRunning) return;
    if (!uwHealth?.connected) {
      setMarketScanError("Unusual Whales not connected");
      return;
    }
    setMarketScanRunning(true);
    setMarketScanError(null);
    setMarketScanRows([]);
    try {
      // Stage 1 — candidates
      const exclude = filteredWatchlistSymbols.join(",");
      const candR = await apiFetch(`/api/uw/market_scan_candidates?limit=50&exclude=${encodeURIComponent(exclude)}`);
      if (!candR.ok) throw new Error(`Candidates fetch failed: HTTP ${candR.status}`);
      const candJ = await candR.json();
      const cands = candJ.candidates || [];
      if (!cands.length) {
        setMarketScanError("No market-wide flow alerts returned. Markets may be closed or quiet.");
        setMarketScanRunning(false);
        return;
      }
      setMarketScanProgress({done: 0, total: cands.length});

      // Stage 2 — score each candidate
      const rows = [];
      for (let i = 0; i < cands.length; i++) {
        const c = cands[i];
        try {
          const r = await apiFetch(`/api/uw/market_scan_score?symbol=${encodeURIComponent(c.symbol)}`);
          if (r.ok) {
            const j = await r.json();
            rows.push({...c, ...j});
          } else {
            rows.push({...c, error: `HTTP ${r.status}`});
          }
        } catch (e) {
          rows.push({...c, error: String(e)});
        }
        setMarketScanProgress({done: i + 1, total: cands.length});
        setMarketScanRows([...rows]);
      }
      setMarketScanAt(Date.now());
    } catch (err) {
      console.warn("market scan failed:", err);
      setMarketScanError(err.message || "Scan failed");
    } finally {
      setMarketScanRunning(false);
    }
  };

  // EMA pullback backtest runner — fetches /api/strategy/ema_pullback for
  // the active dashboard ticker. Re-runs whenever direction changes.
  const runEmaBacktest = async () => {
    if (emaBacktestRunning || !ticker) return;
    setEmaBacktestRunning(true);
    setEmaBacktestError(null);
    try {
      const r = await apiFetch(`/api/strategy/ema_pullback?symbol=${encodeURIComponent(ticker)}&direction=${emaDirection}&lookback=365&ema_fast=${emaFast}&ema_med=${emaMed}&ema_slow=${emaSlow}&slope_bars=${emaSlopeBars}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      setEmaBacktest(j);
    } catch (err) {
      console.warn("ema backtest failed:", err);
      setEmaBacktestError(err.message || "Backtest failed");
    } finally {
      setEmaBacktestRunning(false);
    }
  };

  // EMA pullback watchlist scanner — runs setup_state per ticker.
  // Each call is one daily-bars fetch + indicators (no UW/options data),
  // so runs much faster than the UW scanners (~150ms per ticker).
  const runEmaScan = async () => {
    if (emaScanRunning || !filteredWatchlistSymbols.length) return;
    setEmaScanRunning(true);
    setEmaScanError(null);
    const symbols = [...filteredWatchlistSymbols];
    setEmaScanProgress({done: 0, total: symbols.length});
    const next = {};
    try {
      for (let i = 0; i < symbols.length; i++) {
        const sym = symbols[i];
        try {
          const r = await apiFetch(`/api/strategy/ema_pullback_state?symbol=${encodeURIComponent(sym)}&direction=${emaDirection}&ema_fast=${emaFast}&ema_med=${emaMed}&ema_slow=${emaSlow}&slope_bars=${emaSlopeBars}`);
          if (r.ok) {
            const j = await r.json();
            next[sym] = j;
          } else {
            next[sym] = {symbol: sym, error: `HTTP ${r.status}`};
          }
        } catch (e) {
          next[sym] = {symbol: sym, error: String(e)};
        }
        setEmaScanProgress({done: i + 1, total: symbols.length});
        setEmaScan({...next});
      }
      setEmaScanAt(Date.now());
    } catch (err) {
      console.warn("ema scan failed:", err);
      setEmaScanError(err.message || "Scan failed");
    } finally {
      setEmaScanRunning(false);
    }
  };
  // Schema: {version, symbols: [{symbol, tags, notes, preferred_strategy, starred, added_at}], tag_order}
  // Loaded once on mount from /api/watchlist; saves are debounced PUTs.
  const [watchlistData, setWatchlistData] = useState({
    version: 1, symbols: [], tag_order: []
  });
  const [watchlistLoaded, setWatchlistLoaded] = useState(false);
  const [watchlistTagFilter, setWatchlistTagFilter] = useState(null); // null = no filter
  const [showWatchlistManager, setShowWatchlistManager] = useState(false);
  // Load watchlist from server on mount. CRITICAL: if the user
  // interacted with the watchlist before this load completed (rare but
  // possible on slow networks or while modal is open), we must MERGE
  // instead of overwriting. We track "dirty" via a ref that mutation
  // helpers set when called pre-load.
  // ALSO CRITICAL: if the load FAILS (network error, 404, 500), we
  // must NOT set watchlistLoaded=true — that would cause the save
  // effect to fire and PUT the empty initial state, wiping the
  // server-side data.
  const watchlistDirtyRef = useRef(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch("/api/watchlist");
        if (!r.ok) {
          throw new Error(`watchlist GET ${r.status}`);
        }
        const data = await r.json();
        if (cancelled) return;
        if (!data || !Array.isArray(data.symbols)) {
          throw new Error("watchlist response malformed");
        }
        if (watchlistDirtyRef.current) {
          // User added/changed something before load arrived.
          // Merge server symbols in (preferring user's edits).
          setWatchlistData(prev => {
            const userSymbols = new Map(prev.symbols.map(s => [s.symbol, s]));
            for (const s of data.symbols) {
              if (!userSymbols.has(s.symbol)) {
                userSymbols.set(s.symbol, s);
              }
            }
            return {
              version: 1,
              symbols: Array.from(userSymbols.values()),
              tag_order: data.tag_order || prev.tag_order || [],
            };
          });
        } else {
          setWatchlistData(data);
        }
        // Only mark loaded after a SUCCESSFUL load. If we get here on a
        // failure path, watchlistLoaded stays false and the save effect
        // never fires — which is what we want, because saving the empty
        // initial state would clobber the user's data.
        setWatchlistLoaded(true);
      } catch (e) {
        console.error("Watchlist load failed (NOT marking loaded — will retry):", e);
        // Auto-retry once after 3s in case of transient network issue
        if (!cancelled) {
          setTimeout(async () => {
            if (cancelled) return;
            try {
              const r = await apiFetch("/api/watchlist");
              if (r.ok) {
                const data = await r.json();
                if (data && Array.isArray(data.symbols)) {
                  setWatchlistData(data);
                  setWatchlistLoaded(true);
                  return;
                }
              }
              console.error("Watchlist retry also failed; staying in safe-mode (no saves).");
            } catch (e2) {
              console.error("Watchlist retry error:", e2);
            }
          }, 3000);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);
  // Debounced save: any change to watchlistData triggers a PUT after 600ms.
  // Don't save until initial load has completed (avoids overwriting server
  // with the empty initial state on first paint).
  //
  // CRITICAL: We CANNOT clear the pending timer when the component
  // unmounts, because that would cancel a save right before the page
  // refreshes — losing the user's most recent edits. Instead the
  // timer fires on its own; the only cleanup is when the dependency
  // changes (so we replace the previous pending save with a newer one).
  // We also flush on visibilitychange + beforeunload so a fast refresh
  // doesn't lose data.
  const saveTimerRef = useRef(null);
  const latestWatchlistRef = useRef(watchlistData);
  useEffect(() => {
    latestWatchlistRef.current = watchlistData;
  }, [watchlistData]);

  // Synchronous flush helper. Uses navigator.sendBeacon when available
  // for reliable delivery during page unload, otherwise fetch with keepalive.
  const flushWatchlist = React.useCallback(() => {
    if (!watchlistLoaded) return;
    const data = latestWatchlistRef.current;
    if (!data || !Array.isArray(data.symbols)) return;
    const cfg = window.__APP_CONFIG || {};
    const base = (cfg.apiBase || "").replace(/\/$/, "");
    const url = `${base}/api/watchlist`;
    const body = JSON.stringify(data);
    try {
      // sendBeacon is the only reliably-delivered network request
      // during page unload. It only supports POST, so we use a
      // fetch-with-keepalive as the primary path and beacon fallback.
      const headers = { "Content-Type": "application/json" };
      if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey;
      fetch(url, { method: "PUT", headers, body, keepalive: true })
        .catch(() => {
          // best-effort beacon fallback (note: can't set headers, so
          // only works when API key gating is off)
          try { navigator.sendBeacon && navigator.sendBeacon(url, body); }
          catch (_) {}
        });
    } catch (_) {}
  }, [watchlistLoaded]);

  useEffect(() => {
    if (!watchlistLoaded) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      try {
        const r = await apiFetch("/api/watchlist", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(watchlistData),
        });
        if (!r.ok) {
          const txt = await r.text().catch(() => "");
          console.error("Watchlist save failed:", r.status, txt);
        }
      } catch (e) {
        console.error("Watchlist save error:", e);
      }
    }, 600);
    // Intentionally NO cleanup — letting the timer fire even after
    // re-render is the correct behavior; it always reads the latest
    // state from the closure.
  }, [watchlistData, watchlistLoaded]);

  // Flush on tab close / refresh / hide so rapid interactions don't
  // lose data. visibilitychange covers iOS Safari which doesn't always
  // fire beforeunload.
  useEffect(() => {
    if (!watchlistLoaded) return undefined;
    const onUnload = () => flushWatchlist();
    const onVis = () => { if (document.visibilityState === "hidden") flushWatchlist(); };
    window.addEventListener("beforeunload", onUnload);
    window.addEventListener("pagehide", onUnload);
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      window.removeEventListener("pagehide", onUnload);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [watchlistLoaded, flushWatchlist]);
  // Helper: array of just symbol strings (for legacy code that expects this)
  const watchlist = watchlistData.symbols.map(s => s.symbol);
  // Helper: starred subset for sidebar (max 10)
  const starredSymbols = watchlistData.symbols.filter(s => s.starred).slice(0, 10).map(s => s.symbol);
  // Helper: filtered subset (by current tag filter) — used by scanner
  const filteredWatchlistSymbols = watchlistTagFilter
    ? watchlistData.symbols.filter(s => (s.tags || []).includes(watchlistTagFilter)).map(s => s.symbol)
    : watchlistData.symbols.map(s => s.symbol);

  // Live-quote polling. Hits /api/quote every 15s for the active ticker
  // (sidebar) and starred subset (so positions card P/L updates), every
  // 60s for the watchlist scanner. Pauses when tab hidden or outside
  // US market hours. Brief flash on price change via .price-flash class.
  // US equities trading sessions:
  //   Pre-market:  4:00am - 9:30am ET (240-570 min)
  //   Regular:     9:30am - 4:00pm ET (570-960 min)
  //   Post-market: 4:00pm - 8:00pm ET (960-1200 min)
  // Jerry trades pre-market 8-9:30am, so we poll quotes during the
  // full extended session (4am-8pm ET, Mon-Fri).
  const isMarketOpen = () => {
    const now = new Date();
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
    });
    const parts = fmt.formatToParts(now);
    const wd = parts.find(p => p.type === "weekday")?.value;
    const hh = parseInt(parts.find(p => p.type === "hour")?.value || "0", 10);
    const mm = parseInt(parts.find(p => p.type === "minute")?.value || "0", 10);
    if (wd === "Sat" || wd === "Sun") return false;
    const minutes = hh * 60 + mm;
    return minutes >= 240 && minutes < 1200; // 4:00am to 8:00pm ET
  };
  // Strict regular-hours check — used where we need to know we're in
  // RTH specifically (not extended). Currently unused but available.
  const isRegularHours = () => {
    const now = new Date();
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
    });
    const parts = fmt.formatToParts(now);
    const wd = parts.find(p => p.type === "weekday")?.value;
    const hh = parseInt(parts.find(p => p.type === "hour")?.value || "0", 10);
    const mm = parseInt(parts.find(p => p.type === "minute")?.value || "0", 10);
    if (wd === "Sat" || wd === "Sun") return false;
    const minutes = hh * 60 + mm;
    return minutes >= 570 && minutes < 960;
  };
  // Fast poll: active ticker + starred symbols + open position tickers.
  // Note: we use a ref for positions to avoid a TDZ error from the deps
  // array being evaluated before the positions state is declared further
  // below. Positions are read live inside the tick closure each interval.
  const positionsRef = useRef([]);
  useEffect(() => {
    let cancelled = false;
    const fetchQuotes = async (symbols) => {
      if (!symbols.length) return;
      try {
        const url = `/api/quote?tickers=${encodeURIComponent(symbols.join(","))}`;
        const r = await apiFetch(url);
        if (!r.ok) return;
        const data = await r.json();
        if (cancelled) return;
        const ts = Date.now();
        setLiveQuotes(prev => {
          const next = { ...prev };
          Object.entries(data.results || {}).forEach(([sym, q]) => {
            next[sym] = { ...q, ts };
          });
          return next;
        });
      } catch (_) {}
    };
    const tick = () => {
      if (document.hidden) return;
      if (!isMarketOpen()) return;
      const set = new Set([ticker, ...starredSymbols]);
      (positionsRef.current || []).filter(p => p.status === "open").forEach(p => set.add(p.ticker));
      fetchQuotes(Array.from(set));
    };
    tick();
    const timer = setInterval(skipWhenHidden(tick), 5000);
    const onVis = () => { if (!document.hidden) tick(); };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [ticker, starredSymbols.join(",")]);

  // Slow poll: watchlist scanner symbols. Once per 60s. Same gates.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (document.hidden) return;
      if (!isMarketOpen()) return;
      if (!filteredWatchlistSymbols.length) return;
      if (Object.keys(scanResults).length === 0) return;
      try {
        const url = `/api/quote?tickers=${encodeURIComponent(filteredWatchlistSymbols.slice(0, 25).join(","))}`;
        const r = await apiFetch(url);
        if (!r.ok) return;
        const data = await r.json();
        if (cancelled) return;
        const ts = Date.now();
        setLiveQuotes(prev => {
          const next = { ...prev };
          Object.entries(data.results || {}).forEach(([sym, q]) => {
            next[sym] = { ...q, ts };
          });
          return next;
        });
      } catch (_) {}
    };
    const timer = setInterval(skipWhenHidden(tick), 60000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [filteredWatchlistSymbols.join(","), Object.keys(scanResults).length]);
  // Mutation helpers
  const wlAddSymbol = (sym, opts = {}) => {
    const symbol = (sym || "").toUpperCase().trim();
    if (!symbol) return;
    watchlistDirtyRef.current = true;
    setWatchlistData(prev => {
      if (prev.symbols.some(s => s.symbol === symbol)) return prev;
      return {
        ...prev,
        symbols: [
          ...prev.symbols,
          {
            symbol,
            tags: opts.tags || [],
            notes: opts.notes || "",
            preferred_strategy: opts.preferred_strategy || null,
            starred: !!opts.starred,
            added_at: Math.floor(Date.now() / 1000),
          },
        ],
      };
    });
  };
  const wlRemoveSymbol = (sym) => {
    watchlistDirtyRef.current = true;
    setWatchlistData(prev => ({
      ...prev,
      symbols: prev.symbols.filter(s => s.symbol !== sym),
    }));
  };
  const wlToggleStar = (sym) => {
    watchlistDirtyRef.current = true;
    setWatchlistData(prev => ({
      ...prev,
      symbols: prev.symbols.map(s =>
        s.symbol === sym ? { ...s, starred: !s.starred } : s),
    }));
  };
  const wlUpdateSymbol = (sym, patch) => {
    watchlistDirtyRef.current = true;
    setWatchlistData(prev => ({
      ...prev,
      symbols: prev.symbols.map(s =>
        s.symbol === sym ? { ...s, ...patch } : s),
    }));
  };
  const wlBulkAdd = (text) => {
    // Accept comma- or newline-separated list. Skip empties + dupes.
    const tokens = (text || "")
      .split(/[\s,;\n]+/)
      .map(t => t.toUpperCase().trim())
      .filter(t => t && /^[A-Z0-9./-]{1,12}$/.test(t));
    if (!tokens.length) return 0;
    watchlistDirtyRef.current = true;
    setWatchlistData(prev => {
      const existing = new Set(prev.symbols.map(s => s.symbol));
      const now = Math.floor(Date.now() / 1000);
      const added = [];
      for (const t of tokens) {
        if (existing.has(t)) continue;
        existing.add(t);
        added.push({
          symbol: t, tags: [], notes: "", preferred_strategy: null,
          starred: false, added_at: now,
        });
      }
      return { ...prev, symbols: [...prev.symbols, ...added] };
    });
    return tokens.length;
  };
  // Setter compat for legacy callers — treats their array as a "starred"
  // re-ordering and ensures all symbols exist in the watchlist.
  const setWatchlist = (updater) => {
    setWatchlistData(prev => {
      const oldSymbols = prev.symbols.map(s => s.symbol);
      const newSymbols = typeof updater === "function" ? updater(oldSymbols) : updater;
      if (!Array.isArray(newSymbols)) return prev;
      const existing = new Map(prev.symbols.map(s => [s.symbol, s]));
      const now = Math.floor(Date.now() / 1000);
      const out = [];
      for (const sym of newSymbols) {
        if (existing.has(sym)) {
          out.push(existing.get(sym));
          existing.delete(sym);
        } else {
          out.push({
            symbol: sym, tags: [], notes: "", preferred_strategy: null,
            starred: true, added_at: now,
          });
        }
      }
      // Append any symbols that were in the old data but not the new array
      for (const remaining of existing.values()) out.push(remaining);
      return { ...prev, symbols: out };
    });
  };
  // Auto-refresh interval in seconds. 0 = off. Refresh re-fetches everything
  // (chain, daily, weekly) so GEX, OI, Vol Rank all stay current.
  const [autoRefreshSec, setAutoRefreshSec] = useState(persisted.autoRefreshSec ?? 0);
  const [lastFetched, setLastFetched] = useState(null);
  // Position tracker — separate storage key so it doesn't conflict with
  // the rest of the user's settings if the schema changes later.
  const POSITIONS_KEY = "weeklyOptionsTimer.positions.v1";
  const [positions, setPositions] = useState(() => {
    try {
      const raw = localStorage.getItem(POSITIONS_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch { return []; }
  });
  useEffect(() => {
    try { localStorage.setItem(POSITIONS_KEY, JSON.stringify(positions)); } catch {}
  }, [positions]);
  // Mirror positions into ref so the live-quote effect (which runs before
  // positions is declared in source order) can read them inside its tick.
  useEffect(() => { positionsRef.current = positions; }, [positions]);

  // Re-enabled in v109. PositionsCard owns these; lifted to App so
  // they survive component remounts (e.g. CardErrorBoundary recovery).
  const [showAddPosition, setShowAddPosition] = useState(false);
  const [positionFilter, setPositionFilter] = useState("all");  // all | open | closed

  // Auto-refresh during US market hours. Uses ET clock heuristically — we
  // refresh every `autoRefreshSec` seconds when the local-time hour falls
  // in the broad 9-17 ET window. Outside market hours the timer is idle.
  useEffect(() => {
    if (!autoRefreshSec || autoRefreshSec < 60) return;
    const id = setInterval(skipWhenHidden(() => {
      // Best effort ET hour check — assumes user's clock is reasonable.
      const now = new Date();
      const utcH = now.getUTCHours();
      // ET = UTC - 4 (DST) or UTC - 5 (standard). Be permissive and allow
      // the refresh to fire across 13:30-21:00 UTC, i.e. roughly 9:30am
      // - 5:00pm ET regardless of DST.
      const inMarketHours = utcH >= 13 && utcH <= 21 && now.getUTCDay() >= 1 && now.getUTCDay() <= 5;
      if (inMarketHours) {
        setDataVersion(v => v + 1);
      }
    }), autoRefreshSec * 1000);
    return () => clearInterval(id);
  }, [autoRefreshSec]);
  // Ticker autocomplete state
  const [searchResults, setSearchResults] = useState([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchActive, setSearchActive] = useState(-1);
  const tickerInputRef = useRef(null);

  // Type to resume search. After Enter the user often wants to immediately
  // type a new ticker without clicking back into the input. We listen at
  // the window level for a single letter keypress while no other input is
  // focused, refocus the ticker box, clear it, and seed it with the typed
  // letter. Modifier keys (cmd/ctrl/alt) are passed through so shortcuts
  // still work, and we ignore the case where focus is already in a form
  // control or contenteditable.
  const [showHelp, setShowHelp] = useState(false);
  const [showRef, setShowRef] = useState(false);
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      const tag = (t.tagName || "").toLowerCase();
      const inField = tag === "input" || tag === "textarea" || tag === "select" || t.isContentEditable;

      // Action hot keys — work whether or not we're in a field, since they
      // never conflict with letter input.
      if (e.key === "?") {
        if (inField) return;
        e.preventDefault();
        setShowHelp(s => !s);
        return;
      }
      if (e.key === "Escape") {
        if (showHelp) { e.preventDefault(); setShowHelp(false); return; }
        // Otherwise fall through (input components handle their own Esc).
      }
      if (e.key === "/" && !inField) {
        e.preventDefault();
        if (tickerInputRef.current) {
          tickerInputRef.current.focus();
          tickerInputRef.current.select();
        }
        return;
      }
      if (e.key === "[" && !inField) {
        e.preventDefault();
        // Previous expiration in the list (closer-dated)
        const exps = (window.__LIVE && window.__LIVE.expirations) || [];
        if (!exps.length) return;
        const cur = expiration || (window.__LIVE && window.__LIVE.expiration);
        const idx = exps.indexOf(cur);
        if (idx > 0) setExpiration(exps[idx - 1]);
        return;
      }
      if (e.key === "]" && !inField) {
        e.preventDefault();
        const exps = (window.__LIVE && window.__LIVE.expirations) || [];
        if (!exps.length) return;
        const cur = expiration || (window.__LIVE && window.__LIVE.expiration);
        const idx = exps.indexOf(cur);
        if (idx >= 0 && idx < exps.length - 1) setExpiration(exps[idx + 1]);
        return;
      }
      if (/^[1-9]$/.test(e.key) && !inField) {
        e.preventDefault();
        const rank = parseInt(e.key, 10);
        // Select strategy by rank — looked up at runtime since `strategies`
        // isn't in this effect's closure. Use window.__strategiesByRank
        // which we publish on each render.
        const list = window.__strategiesByRank || [];
        const target = list[rank - 1];
        if (target) setSelectedStrategy(target);
        return;
      }

      // Type-to-resume — only fires for plain letter keystrokes outside a field.
      if (e.key.length !== 1) return;
      if (!/^[a-zA-Z]$/.test(e.key)) return;
      if (inField) return;
      e.preventDefault();
      setTickerInput(e.key.toUpperCase());
      setSearchOpen(true);
      setSearchActive(-1);
      requestAnimationFrame(() => {
        if (tickerInputRef.current) {
          tickerInputRef.current.focus();
          tickerInputRef.current.setSelectionRange(1, 1);
        }
      });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expiration, showHelp]);

  // Persist settings whenever any tracked field changes. Expiration is
  // intentionally excluded because it is per-ticker and a stale date for a
  // new ticker silently falls back to the server default anyway.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        ticker, weeks, bufferPct, baseline, selectedStrategy, thetaSide, watchlist, strikeMode, targetDelta, showMA50, showMA200, showEMA21, showRSI, showProbCone, autoRefreshSec, chartDays, strategyMode,
      }));
    } catch { /* quota or private mode — silently skip */ }
  }, [ticker, weeks, bufferPct, baseline, selectedStrategy, thetaSide, watchlist, strikeMode, targetDelta, showMA50, showMA200, showEMA21, showRSI, showProbCone, autoRefreshSec, chartDays, strategyMode]);

  // Apply theme + accent CSS vars
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = tweaks.values.theme;
    root.dataset.density = tweaks.values.density;
    root.dataset.typeface = tweaks.values.typeface;
    const a = ACCENT_PRESETS[tweaks.values.accent] || ACCENT_PRESETS.emerald;
    root.style.setProperty("--accent-h", a.h);
    root.style.setProperty("--accent-c", a.c);
    root.style.setProperty("--accent-l", a.l);
  }, [tweaks.values]);

  // Live fetch from /api/ticker (served by options_dashboard.py --serve).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    let url = `/api/ticker?symbol=${encodeURIComponent(ticker)}`
            + `&weeks=${weeks}&baseline=${baseline}`;
    if (expiration) url += `&expiration=${encodeURIComponent(expiration)}`;
    apiFetch(url)
      .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e)))
      .then(payload => {
        if (cancelled) return;
        if (window.__installLive) window.__installLive(payload);
        else window.__bootstrapLive && window.__bootstrapLive(payload);
        setDataVersion(v => v + 1);
        setLastFetched(Date.now());
        setLoading(false);
      })
      .catch(err => {
        if (cancelled) return;
        const msg = err?.error || err?.message || "Fetch failed";
        const offline = /Failed to fetch|NetworkError|TypeError/.test(msg);
        const havePreset = !!window.MockData?.PRESETS?.[ticker];
        if (offline && havePreset) {
          setLoadError(null);
        } else {
          setLoadError(
            offline
              ? "Live API offline. Start with: python3 options_dashboard.py --serve"
              : msg
          );
          // Still log full error so it shows up in DevTools.
          console.warn("ticker fetch failed:", err);
        }
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [ticker, weeks, baseline, expiration, reloadNonce]);

  // Reset expiration override whenever the ticker changes — different
  // symbols have different chains, so a stale date will silently fall back
  // to the server default.
  useEffect(() => { setExpiration(""); }, [ticker]);

  // Close the mobile drawer when the ticker changes (e.g. picked from it).
  useEffect(() => { setNavOpen(false); }, [ticker]);

  // Lock body scroll while the mobile drawer is open.
  useEffect(() => {
    document.body.style.overflow = navOpen ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [navOpen]);

  // Swipe left/right between sections on mobile. Guarded so it never fires
  // inside horizontally-scrollable zones (tables, charts, chip strips).
  useEffect(() => {
    const NOSWIPE = ".scan-table-wrap,.oc-table-scroll,.tab-bar,.sidebar,.mobile-overlay,"
      + "canvas,input,select,textarea,.swing-levels,.news-srcnav,.swing-histnav,"
      + ".screener-subnav,.ab-chips,.pcalc-panel,.mobile-bottombar";
    let sx = 0, sy = 0, st = 0, tracking = false;
    const onStart = (e) => {
      if (window.innerWidth > 900 || e.touches.length !== 1) { tracking = false; return; }
      const t = e.target;
      if (t && t.closest && t.closest(NOSWIPE)) { tracking = false; return; }
      sx = e.touches[0].clientX; sy = e.touches[0].clientY; st = Date.now(); tracking = true;
    };
    const onEnd = (e) => {
      if (!tracking) return; tracking = false;
      if (window.innerWidth > 900) return;
      const tch = e.changedTouches && e.changedTouches[0]; if (!tch) return;
      const dx = tch.clientX - sx, dy = tch.clientY - sy;
      if (Date.now() - st > 600) return;
      if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 2) return;
      const tabs = window.TABS || [];
      const idx = tabs.findIndex(t => t.id === activeTab);
      const next = dx < 0 ? idx + 1 : idx - 1;
      if (idx < 0 || next < 0 || next >= tabs.length) return;
      changeTab(tabs[next].id);
    };
    document.addEventListener("touchstart", onStart, { passive: true });
    document.addEventListener("touchend", onEnd, { passive: true });
    return () => {
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchend", onEnd);
    };
  }, [activeTab, changeTab]);

  // Debounced ticker autocomplete. Calls the local /api/search proxy that
  // the Python server exposes. Falls back to the existing PRESETS list when
  // the proxy is offline.
  useEffect(() => {
    const q = (tickerInput || "").trim();
    if (!q || q === ticker) {
      setSearchResults([]);
      return;
    }
    let cancelled = false;
    const id = setTimeout(() => {
      apiFetch(`/api/search?q=${encodeURIComponent(q)}`)
        .then(r => r.ok ? r.json() : Promise.reject(r))
        .then(data => {
          if (cancelled) return;
          setSearchResults(Array.isArray(data?.results) ? data.results : []);
          setSearchActive(-1);
        })
        .catch(() => {
          if (cancelled) return;
          // Offline fallback: filter the mock preset list by prefix match.
          const presets = Object.keys(window.MockData?.PRESETS || {});
          const matches = presets
            .filter(t => t.startsWith(q.toUpperCase()))
            .map(sym => ({ symbol: sym, name: window.MockData.PRESETS[sym].name || sym, type: "EQUITY", exchange: "" }));
          setSearchResults(matches.slice(0, 10));
        });
    }, 180);
    return () => { cancelled = true; clearTimeout(id); };
  }, [tickerInput, ticker]);

  // Compute data
  const dataset = useMemo(() => {
    const have = window.MockData?.PRESETS?.[ticker];
    if (!have) {
      const fallback = Object.keys(window.MockData?.PRESETS || {})[0];
      const t = fallback || ticker;
      const { rows, current } = window.MockData.buildWeekly(t, weeks);
      const daily = window.MockData.buildDaily(t, 90);
      const chain = window.MockData.buildOptionChain(t, current.current);
      return { rows, current, daily, chain };
    }
    const { rows, current } = window.MockData.buildWeekly(ticker, weeks);
    const daily = window.MockData.buildDaily(ticker, 90);
    const chain = window.MockData.buildOptionChain(ticker, current.current);
    return { rows, current, daily, chain };
  }, [ticker, weeks, dataVersion]);

  const { rows, current, daily: _payloadDaily, chain } = dataset;

  // Build a "today" candle from the live quote — same as ThinkorSwim/TV.
  // Tracks intraday high/low across ticks. Resets at midnight ET.
  // Persists ONLY in-memory; on refresh we re-seed from the live quote
  // and let it grow again. Outside market hours: skip — the last
  // historical bar is already today's close (or Friday's if weekend).
  const todayBarRef = useRef({ date: null, ticker: null, open: null, high: null, low: null });
  const liveTickerQuote = liveQuotes[ticker];
  const todayET = useMemo(() => {
    try {
      const fmt = new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/New_York",
        year: "numeric", month: "2-digit", day: "2-digit",
      });
      return fmt.format(new Date()); // YYYY-MM-DD
    } catch { return null; }
  }, [liveTickerQuote?.ts]);
  const isMarketOpenForChart = () => {
    const now = new Date();
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
    });
    const parts = fmt.formatToParts(now);
    const wd = parts.find(p => p.type === "weekday")?.value;
    const hh = parseInt(parts.find(p => p.type === "hour")?.value || "0", 10);
    const mm = parseInt(parts.find(p => p.type === "minute")?.value || "0", 10);
    if (wd === "Sat" || wd === "Sun") return false;
    const minutes = hh * 60 + mm;
    return minutes >= 570 && minutes < 960;
  };
  const daily = useMemo(() => {
    const base = _payloadDaily || [];
    const live = liveTickerQuote;
    if (!live || live.last == null || !todayET) return base;
    if (!isMarketOpenForChart()) return base;
    // Reset today's bar state if EITHER the date rolled over OR the
    // user switched tickers. Without the ticker check, the ref keeps
    // accumulating high/low across symbols, producing a candle that
    // spans both tickers' ranges and looks like a giant bar.
    const cur = todayBarRef.current;
    if (cur.date !== todayET || cur.ticker !== ticker) {
      todayBarRef.current = {
        date: todayET,
        ticker: ticker,
        open: live.open ?? live.last,
        high: live.high ?? live.last,
        low: live.low ?? live.last,
      };
    } else {
      // Track running high/low against new tick
      cur.high = Math.max(cur.high ?? live.last, live.last, live.high ?? live.last);
      cur.low  = Math.min(cur.low  ?? live.last, live.last, live.low  ?? live.last);
    }
    const last = todayBarRef.current;
    // Match payload shape: `date` is a Date instance (see data.js:238)
    const todayDateObj = new Date(todayET + "T00:00:00");
    const todayCandle = {
      date: todayDateObj,
      open: last.open,
      high: last.high,
      low: last.low,
      close: live.last,
      volume: 0,
      synthetic: true,
    };
    // Helper: extract YYYY-MM-DD from a base bar's date (Date or string).
    const dateKey = (d) => {
      if (!d) return "";
      if (typeof d === "string") return d.slice(0, 10);
      if (d instanceof Date) {
        try {
          return new Intl.DateTimeFormat("en-CA", {
            timeZone: "America/New_York",
            year: "numeric", month: "2-digit", day: "2-digit",
          }).format(d);
        } catch {
          return d.toISOString().slice(0, 10);
        }
      }
      return "";
    };
    if (base.length > 0 && dateKey(base[base.length - 1].date) === todayET) {
      return [...base.slice(0, -1), todayCandle];
    }
    return [...base, todayCandle];
  }, [_payloadDaily, liveTickerQuote?.last, liveTickerQuote?.ts, todayET, ticker]);

  const medianHigh = window.MockData.median(rows.map(r => r.high_return));
  const medianLow = window.MockData.median(rows.map(r => r.low_return));
  const medianClose = window.MockData.median(rows.map(r => r.close_return));
  const typicalHighDay = window.MockData.mode(rows.map(r => r.high_day_name));
  const typicalLowDay = window.MockData.mode(rows.map(r => r.low_day_name));

  const baselinePrice = current.baseline;
  // currentPrice prefers the live polled quote when available, falls
  // back to the payload's snapshot. This keeps every downstream calc
  // (P/L curve, expected range, ATM marker, suggested strike rounding)
  // synced with the auto-refreshed sidebar price.
  const _payloadPrice = current.current;
  const _livePrice = getLivePrice(ticker);
  const currentPrice = _livePrice != null ? _livePrice : _payloadPrice;
  const currReturn = (currentPrice / baselinePrice - 1) * 100;
  const expHigh = baselinePrice * (1 + medianHigh / 100);
  const expLow = baselinePrice * (1 + medianLow / 100);

  // Snap target prices to actual strikes available in the chain.
  const calls = chain.calls, puts = chain.puts;
  const nearest = (arr, target) => arr.length
    ? arr.reduce((a, b) => Math.abs(a.strike - target) < Math.abs(b.strike - target) ? a : b)
    : { strike: target, bid: 0, ask: 0, last: 0, volume: 0, openInterest: 0, iv: 0, delta: 0 };

  // Manual strike overrides — when set, these take precedence over the
  // auto picker. They reset whenever the ticker changes so a stale
  // override on $400 doesn't haunt a different stock. Stored as numbers,
  // not strings; null = use auto pick.
  const [manualCallStrike, setManualCallStrike] = useState(null);
  const [manualPutStrike, setManualPutStrike] = useState(null);
  // Wing strikes for spreads — shift-click in the chain to set a long
  // wing on the call or put side. When set, the dashboard treats the
  // selected setup as a credit spread (short primary + long wing) and
  // surfaces aggregate Greeks accordingly.
  const [manualCallWing, setManualCallWing] = useState(null);
  const [manualPutWing, setManualPutWing] = useState(null);
  // Drag-select state — when the user mousedowns on a chain cell and
  // drags, we track the start strike and the current hover strike so
  // the chain can highlight the range. On mouseup the range becomes a
  // spread (start = primary short, end = long wing).
  const [drag, setDrag] = useState(null);  // {side: "call"|"put", start: number, end: number}

  // Custom strategy builder — Jerry checks boxes on chain rows to add
  // legs to a tray, then visualizes P/L at expiration. Each leg:
  //   {type: "call"|"put", strike, qty: ±100, premium}
  // Premium is captured at the moment of selection from chain mid;
  // qty is positive for long, negative for short. Default direction is
  // "long" — Jerry can flip via the tray button.
  const [customLegs, setCustomLegs] = useState([]);
  const [showPayoff, setShowPayoff] = useState(false);

  // Position sizing config — persisted to localStorage so settings
  // survive page reloads. Account size and max risk % drive the contract
  // count calculator. Defaults reflect a conservative ~1% risk per
  // trade on a $50k account, which Jerry can adjust.
  const [sizingConfig, setSizingConfig] = useState(() => {
    try {
      const raw = localStorage.getItem("jerryDash.sizing.v1");
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") return parsed;
      }
    } catch (_) {}
    return { accountSize: 50000, maxRiskPct: 1.0 };
  });
  useEffect(() => {
    try { localStorage.setItem("jerryDash.sizing.v1", JSON.stringify(sizingConfig)); }
    catch (_) {}
  }, [sizingConfig]);

  // Position helpers. The positions state itself is declared earlier
  // (under POSITIONS_KEY) so we just hook into it here.
  const openCurrentAsPosition = (entryTicker, entryPrice, entryExpDate, contracts) => {
    if (!customLegs.length) return;
    const pos = {
      id: `pos_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      ticker: entryTicker,
      legs: customLegs.map(l => ({...l})),
      openedAt: new Date().toISOString(),
      entryPrice,
      expDate: entryExpDate,
      contracts: contracts || 1,
      status: "open",
      notes: "",
    };
    setPositions(prev => [pos, ...prev]);
  };
  const closePosition = (id, exitPrice) => {
    setPositions(prev => {
      const updated = prev.map(p => p.id === id
        ? {...p, status: "closed", closedAt: new Date().toISOString(), exitPrice}
        : p);
      // v1.15: persist to trade journal so the win-rate tracker picks
      // it up. Best-effort POST: if the request fails, the position
      // local state still updates correctly.
      const closed = updated.find(p => p.id === id);
      if (closed && (closed.type === "call" || closed.type === "put" || closed.kind === "call" || closed.kind === "put")) {
        const journalEntry = {
          ticker: closed.ticker || closed.symbol || "",
          type: closed.type || closed.kind,
          strike: closed.strike != null ? Number(closed.strike) : null,
          expiration: closed.expDate || closed.expiration || null,
          qty: closed.contracts != null ? -Math.abs(Number(closed.contracts)) : (closed.qty || -1),
          entry_premium: closed.entryPrice != null ? Number(closed.entryPrice) : (closed.entryPremium || 0),
          closed_premium: exitPrice != null ? Number(exitPrice) : 0,
          entry_delta: closed.entryDelta != null ? Number(closed.entryDelta) : null,
          opened_at: closed.openedAt || null,
          closed_at: closed.closedAt || new Date().toISOString(),
        };
        try {
          apiFetch("/api/trade_journal", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(journalEntry),
          })
            .then(() => window.dispatchEvent(new CustomEvent("jerry:position-closed", { detail: journalEntry })))
            .catch(e => console.warn("trade journal write failed", e));
        } catch (e) {
          console.warn("trade journal write threw", e);
        }
      }
      return updated;
    });
  };
  const deletePosition = (id) => {
    setPositions(prev => prev.filter(p => p.id !== id));
  };

  // Research tool state — earnings ladder (#3) and backtest (#5).
  // Both cached in state and recomputed on demand. Loading flags so we
  // don't fire duplicate requests; results stick until ticker changes.
  const [earningsLadder, setEarningsLadder] = useState(null);
  const [earningsLoading, setEarningsLoading] = useState(false);
  const [earningsError, setEarningsError] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [backtestLoading, setBacktestLoading] = useState(false);
  const [backtestError, setBacktestError] = useState(null);
  const [backtestStrategy, setBacktestStrategy] = useState("short_strangle");
  const [backtestWeeks, setBacktestWeeks] = useState(52);
  // Weekly range scanner — runs across the (filtered) watchlist and
  // returns implied weekly range + 0.20 delta strike suggestions for
  // each symbol. Cached as map keyed by symbol so partial failures
  // can still display the ones that succeeded.
  const [weeklyRange, setWeeklyRange] = useState({}); // {symbol: result}
  const [weeklyRangeRunning, setWeeklyRangeRunning] = useState(false);
  const [weeklyRangeAt, setWeeklyRangeAt] = useState(null);
  const [weeklyRangeError, setWeeklyRangeError] = useState(null);
  const [weeklyRangeProgress, setWeeklyRangeProgress] = useState({done: 0, total: 0});
  // Pullback scanner — historical open-to-low pullback stats per symbol.
  // Used to rank watchlist for short-the-open candidates.
  const [pullbackScan, setPullbackScan] = useState({});
  const [pullbackScanRunning, setPullbackScanRunning] = useState(false);
  const [pullbackScanAt, setPullbackScanAt] = useState(null);
  const [pullbackScanError, setPullbackScanError] = useState(null);
  const [pullbackScanProgress, setPullbackScanProgress] = useState({done: 0, total: 0});
  // Pullback scanner sort — default rank by score (verdict attractiveness)
  const [pbSort, setPbSort] = useState({key: "_score", dir: "desc"});
  // Pullback scanner direction — short or long. Persisted.
  const PBSCAN_DIR_KEY = "weeklyOptionsTimer.pullbackScan.direction.v1";
  const [pbScanDir, setPbScanDir] = useState(() => {
    try { return localStorage.getItem(PBSCAN_DIR_KEY) || "short"; } catch { return "short"; }
  });
  useEffect(() => {
    try { localStorage.setItem(PBSCAN_DIR_KEY, pbScanDir); } catch {}
  }, [pbScanDir]);

  // Premium richness scanner — UW-driven. Manual run only (button click)
  // because each row consumes 2 UW calls (ticker_options_volume + stock_state).
  const [richnessScan, setRichnessScan] = useState({});  // {sym: payload}
  const [richnessRunning, setRichnessRunning] = useState(false);
  const [richnessError, setRichnessError] = useState(null);
  const [richnessProgress, setRichnessProgress] = useState({done: 0, total: 0});
  const [richnessAt, setRichnessAt] = useState(null);
  const [richnessSort, setRichnessSort] = useState({key: "score", dir: "desc"});

  // Intraday momentum scanner — blends UW flow + price action. Manual run.
  const [momentumScan, setMomentumScan] = useState({});
  const [momentumRunning, setMomentumRunning] = useState(false);
  const [momentumError, setMomentumError] = useState(null);
  const [momentumProgress, setMomentumProgress] = useState({done: 0, total: 0});
  const [momentumAt, setMomentumAt] = useState(null);
  const [momentumSort, setMomentumSort] = useState({key: "score", dir: "desc"});

  // Market-wide UW scanner — finds tickers with unusual flow that are
  // NOT in the watchlist. Manual run only. Results array of full row
  // objects (one per scanned candidate).
  const [marketScanRows, setMarketScanRows] = useState([]);
  const [marketScanRunning, setMarketScanRunning] = useState(false);
  const [marketScanError, setMarketScanError] = useState(null);
  const [marketScanProgress, setMarketScanProgress] = useState({done: 0, total: 0});
  const [marketScanAt, setMarketScanAt] = useState(null);
  const [marketScanSort, setMarketScanSort] = useState({key: "unusual_pct", dir: "desc"});

  // EMA pullback strategy — backtest + scanner.
  // Backtest results for the active dashboard ticker (refetched on ticker change).
  const [emaBacktest, setEmaBacktest] = useState(null);
  const [emaBacktestRunning, setEmaBacktestRunning] = useState(false);
  const [emaBacktestError, setEmaBacktestError] = useState(null);
  const [emaDirection, setEmaDirection] = useState("long");
  // Configurable EMA periods. Persisted so the user's preset survives reloads.
  // Defaults: 9/21/50 — the original SNDK-inspired setup.
  const EMA_PARAMS_KEY = "weeklyOptionsTimer.emaStrategy.params.v1";
  const persistedEmaParams = (() => {
    try {
      const raw = localStorage.getItem(EMA_PARAMS_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  })();
  const [emaFast, setEmaFast] = useState(persistedEmaParams?.fast ?? 9);
  // Med/Slow EMAs are FIXED for the trend filter — only Fast EMA is
  // user-tunable. Keeping these in state (rather than as constants) means
  // the API-call code below already has the right shape.
  const [emaMed] = useState(21);
  const [emaSlow] = useState(50);
  const [emaSlopeBars] = useState(10);
  useEffect(() => {
    try {
      localStorage.setItem(EMA_PARAMS_KEY, JSON.stringify({ fast: emaFast }));
    } catch {}
  }, [emaFast]);
  // Watchlist scanner results: {sym: state-payload}
  const [emaScan, setEmaScan] = useState({});
  const [emaScanRunning, setEmaScanRunning] = useState(false);
  const [emaScanError, setEmaScanError] = useState(null);
  const [emaScanProgress, setEmaScanProgress] = useState({done: 0, total: 0});
  const [emaScanAt, setEmaScanAt] = useState(null);
  // Reset backtest when ticker changes.
  useEffect(() => {
    setEmaBacktest(null);
    setEmaBacktestError(null);
  }, [ticker]);

  // Reset research caches when ticker changes — they're per-symbol.
  useEffect(() => {
    setEarningsLadder(null);
    setEarningsError(null);
    setBacktest(null);
    setBacktestError(null);
  }, [ticker]);

  useEffect(() => {
    setManualCallStrike(null); setManualPutStrike(null);
    setManualCallWing(null); setManualPutWing(null);
    setDrag(null);
    setCustomLegs([]);
    setShowPayoff(false);
  }, [ticker]);

  // Commit drag on global mouseup. If drag end ≠ drag start, set both
  // primary (start strike) and wing (end strike) on the matching side.
  // If end === start, treat as a click — primary only, no wing.
  useEffect(() => {
    if (!drag) return;
    const onUp = () => {
      if (!drag) return;
      if (drag.start !== drag.end) {
        if (drag.side === "call") {
          setManualCallStrike(drag.start);
          setManualCallWing(drag.end);
        } else {
          setManualPutStrike(drag.start);
          setManualPutWing(drag.end);
        }
      }
      setDrag(null);
    };
    window.addEventListener("mouseup", onUp);
    return () => window.removeEventListener("mouseup", onUp);
  }, [drag]);

  let callAtSug, putAtSug;
  if (strikeMode === "delta" && calls.length && calls.some(c => c.delta != null)) {
    const callPool = calls.filter(c => c.delta != null && c.delta > 0.02 && c.delta < 0.50);
    const putPool  = puts.filter(p => p.delta != null && p.delta < -0.02 && p.delta > -0.50);
    callAtSug = callPool.length
      ? callPool.reduce((a, b) => Math.abs(a.delta - targetDelta) < Math.abs(b.delta - targetDelta) ? a : b)
      : nearest(calls, currentPrice * 1.02);
    putAtSug = putPool.length
      ? putPool.reduce((a, b) => Math.abs(a.delta + targetDelta) < Math.abs(b.delta + targetDelta) ? a : b)
      : nearest(puts, currentPrice * 0.98);
  } else {
    const targetCall = expHigh * (1 + bufferPct / 100);
    const targetPut  = expLow  * (1 - bufferPct / 100);
    callAtSug = nearest(calls.filter(c => c.strike >= targetCall), targetCall) || nearest(calls, targetCall);
    putAtSug  = nearest(puts.filter(p => p.strike  <= targetPut),  targetPut)  || nearest(puts, targetPut);
  }
  // Apply manual overrides if user clicked a specific strike from the chain.
  if (manualCallStrike != null) {
    const m = nearest(calls, manualCallStrike);
    if (m && m.strike) callAtSug = m;
  }
  if (manualPutStrike != null) {
    const m = nearest(puts, manualPutStrike);
    if (m && m.strike) putAtSug = m;
  }
  // Empty-chain safety. If the API returned no chain (network failure,
  // delisted ticker, halt with no quotes), nearest() returns undefined
  // and downstream code crashes on .strike. Substitute synthetic stubs
  // anchored on currentPrice so the rest of the dashboard renders the
  // empty-state gracefully instead of going black.
  if (!callAtSug || callAtSug.strike == null) {
    callAtSug = { strike: currentPrice * 1.02, bid: 0, ask: 0, last: 0, iv: 0.30, delta: 0, gamma: 0, theta: 0, vega: 0, openInterest: 0, volume: 0 };
  }
  if (!putAtSug || putAtSug.strike == null) {
    putAtSug = { strike: currentPrice * 0.98, bid: 0, ask: 0, last: 0, iv: 0.30, delta: 0, gamma: 0, theta: 0, vega: 0, openInterest: 0, volume: 0 };
  }
  const sugCall = callAtSug.strike;
  const sugPut  = putAtSug.strike;

  // ATM straddle (real chain strike)
  const atmCall = nearest(calls, currentPrice);
  const atmPut  = nearest(puts,  currentPrice);
  const mid = q => q.bid > 0 ? q.bid : (q.bid + q.ask) / 2 || q.last || 0;
  const callMid = mid(callAtSug);
  const putMid  = mid(putAtSug);
  const atmCallMid = mid(atmCall);
  const atmPutMid  = mid(atmPut);
  const straddle = atmCallMid + atmPutMid;
  const ivMove = (straddle / currentPrice) * 100;
  const expectedDollarMove = straddle;
  const histMove = Math.abs(medianHigh) + Math.abs(medianLow);

  // Available expirations list (Friday weeklies, populated by live payload).
  const liveExpirations = useMemo(
    () => (window.MockData.getLiveExpirations ? window.MockData.getLiveExpirations(ticker) : []),
    [ticker, dataVersion]
  );

  // Active expiration date object — drives FRONT_DTE and label rendering.
  const activeExpDate = useMemo(() => {
    if (expiration) {
      try { return new Date(expiration + "T16:00:00"); } catch { /* fall through */ }
    }
    return window.MockData.nextFriday();
  }, [expiration, dataVersion]);

  // Days to the active expiration. This used to read just nextFriday; now it
  // tracks whatever the user picks in the expiration dropdown.
  const FRONT_DTE = useMemo(() => {
    const ms = activeExpDate.getTime() - Date.now();
    return Math.max(1, Math.ceil(ms / 86400000));
  }, [activeExpDate]);

  // Probabilities — % of historical weeks where the high stayed below the call strike
  // (or the low stayed above the put strike), measured against the SAME baseline used in this week.
  const callSafePct = rows.length
    ? (rows.filter(r => r.high_return < (sugCall / baselinePrice - 1) * 100).length / rows.length) * 100
    : 0;
  const putSafePct = rows.length
    ? (rows.filter(r => r.low_return > (sugPut / baselinePrice - 1) * 100).length / rows.length) * 100
    : 0;
  const bothSafePct = rows.length
    ? (rows.filter(r => r.high_return < (sugCall / baselinePrice - 1) * 100
                     && r.low_return  > (sugPut  / baselinePrice - 1) * 100).length / rows.length) * 100
    : 0;

  // ── Recommendation engine (Phase B, v1.11) ──────────────────────
  // Compute both CC and CSP variants via the shared RecEngine helpers
  // in recommendation.js. The CC path preserves v109/v110 behavior
  // exactly. The CSP path inverts directional bias and analyst overlay
  // signal direction per Jerry's v106 spec. Pure functions, also
  // exercised by test_recommendation.js.
  //
  // Backward compat: top-level rec fields (kind/title/body) stay the
  // CC variant so any consumer that hasn't been upgraded keeps working.
  // The CSP variant is exposed at rec.csp; the CC variant at rec.cc.
  const aData = analystData || null;
  const _recPair = (window.RecEngine && window.RecEngine.buildBoth)
    ? window.RecEngine.buildBoth({
        currReturn: currReturn,
        medianClose: medianClose,
        medianHigh: medianHigh,
        medianLow: medianLow,
        analystData: aData,
      })
    : { cc: { kind: "info", title: "Loading…", body: "" }, csp: { kind: "info", title: "Loading…", body: "" } };
  const rec = Object.assign({}, _recPair.cc, { cc: _recPair.cc, csp: _recPair.csp });
  // Legacy aliases retained so any downstream code reading these still works.
  const analystVerdict = aData?.verdict;
  const analystTargets = aData?.targets;

  // Colors for charts (read CSS vars)
  const chartColors = useMemo(() => {
    const cs = getComputedStyle(document.documentElement);
    return {
      up: cs.getPropertyValue("--up").trim() || "#16a34a",
      down: cs.getPropertyValue("--down").trim() || "#dc2626",
      accent: cs.getPropertyValue("--accent").trim() || "#16a34a",
      accentText: cs.getPropertyValue("--accent-text").trim() || "white",
      warn: cs.getPropertyValue("--warn").trim() || "#d97706",
      fg2: cs.getPropertyValue("--fg-2").trim() || "#9ca3af",
      fg3: cs.getPropertyValue("--fg-3").trim() || "#6b7280",
      band: cs.getPropertyValue("--accent").trim() || "#16a34a",
      bandSolid: cs.getPropertyValue("--bg-3").trim() || "#eee",
    };
  }, [tweaks.values.theme, tweaks.values.accent]);

  const chartStyle = tweaks.values.chartStyle;
  const layout = tweaks.values.layout;

  const stockDelta = currentPrice - baselinePrice;
  const stockDeltaPct = (stockDelta / baselinePrice) * 100;

  const Tweaks = window.TweaksPanel;
  const TweakSection = window.TweakSection;
  const TweakSelect = window.TweakSelect;
  const TweakRadio = window.TweakRadio;
  const Term = window.Term || (({ children }) => <span>{children}</span>);

  // Build context once, run every strategy's build(ctx). Some return null
  // (e.g. jade lizard with no callsAbove) — filter those out.
  const expFront = activeExpDate.toISOString().slice(0, 10);
  const expFrontLabel = activeExpDate.toLocaleDateString("en-US", {month: "short", day: "numeric"});
  // For calendar/diagonal: a back month roughly 30 days after front
  const _back = new Date(activeExpDate); _back.setDate(_back.getDate() + 28);
  const expBackLabel = _back.toLocaleDateString("en-US", {month: "short", day: "numeric"});
  const stratCtx = {
    sugCall, sugPut, callMid, putMid, callAtSug, putAtSug,
    atmCall, atmPut, atmCallMid, atmPutMid,
    currentPrice, calls, puts, FRONT_DTE,
    expFront, expFrontLabel, expBackLabel,
  };

  // Score each strategy against the current setup. The signals are:
  //   richness     ivMove / histMove   (>1 = market pricing more vol than history)
  //   bothSafePct  prob both legs of a strangle held last N weeks
  //   medianClose  directional bias from history
  //   earningsThisWeek  binary risk flag
  // The scoring is heuristic, not optimal. It biases toward selling when
  // premium is rich and toward calendars / diagonals when premium is cheap.
  const conds = useMemo(() => ({
    richness: histMove > 0 ? ivMove / histMove : 1,
    bothSafePct, callSafePct, putSafePct,
    medianClose, ivMove, histMove,
    earningsThisWeek: !!current.earnings,
  }), [ivMove, histMove, bothSafePct, callSafePct, putSafePct, medianClose, current.earnings]);

  function scoreFor(key, c) {
    const isRich = c.richness > 1.10;
    const isVeryRich = c.richness > 1.25;
    const isCheap = c.richness < 0.85;
    const isBullish = c.medianClose > 1.5;
    const isBearish = c.medianClose < -1.5;
    const isNeutral = !isBullish && !isBearish;
    const isHighProb = c.bothSafePct > 65;
    const isVeryHighProb = c.bothSafePct > 80;
    const earn = c.earningsThisWeek;
    let s = 50;
    const why = [];
    switch (key) {
      case "short_strangle":
        if (isRich) { s += 18; why.push("premium rich"); }
        if (isVeryRich) s += 8;
        if (isHighProb) { s += 12; why.push("history holds"); }
        if (isCheap) { s -= 25; why.push("premium cheap"); }
        if (earn) { s -= 30; why.push("earnings risk"); }
        break;
      case "iron_condor":
        s = 55;
        why.push("defined risk");
        if (isRich) { s += 14; why.push("premium rich"); }
        if (isHighProb) { s += 10; why.push("history holds"); }
        if (earn) { s -= 12; why.push("earnings risk"); }
        if (isCheap) { s -= 18; why.push("premium cheap"); }
        break;
      case "short_straddle":
        s = 30;
        if (isVeryRich && isVeryHighProb) { s += 35; why.push("max premium env"); }
        else if (isVeryRich) { s += 15; why.push("very rich"); }
        else if (!isRich) why.push("not rich enough");
        if (earn) { s -= 35; why.push("earnings risk"); }
        break;
      case "covered_call":
        s = 60;
        why.push("steady income");
        if (isRich) { s += 12; why.push("premium rich"); }
        if (isBearish) { s += 10; why.push("bias matches"); }
        if (isVeryRich) s += 5;
        break;
      case "cash_secured_put":
        s = 60;
        why.push("entry tool");
        if (isRich) { s += 12; why.push("premium rich"); }
        if (isBullish) { s += 10; why.push("bias matches"); }
        if (c.putSafePct > 70) { s += 8; why.push("put strike safe"); }
        break;
      case "bull_put_spread":
        s = 45;
        why.push("defined risk");
        if (isBullish) { s += 16; why.push("bias matches"); }
        else if (isBearish) { s -= 12; why.push("bias against"); }
        if (isRich) s += 8;
        if (c.putSafePct > 70) s += 6;
        break;
      case "bear_call_spread":
        s = 45;
        why.push("defined risk");
        if (isBearish) { s += 16; why.push("bias matches"); }
        else if (isBullish) { s -= 12; why.push("bias against"); }
        if (isRich) s += 8;
        if (c.callSafePct > 70) s += 6;
        break;
      case "calendar_spread":
        s = 40;
        why.push("buys vol");
        if (isCheap) { s += 22; why.push("premium cheap"); }
        if (isNeutral) { s += 8; why.push("expect pin"); }
        if (isRich) { s -= 15; why.push("premium rich"); }
        break;
      case "diagonal_spread":
        s = 40;
        why.push("buys vol with bias");
        if (isCheap) { s += 18; why.push("premium cheap"); }
        if (!isNeutral) { s += 10; why.push("bias present"); }
        if (isRich) s -= 12;
        break;
      case "jade_lizard":
        s = 50;
        why.push("no upside risk");
        if (isRich) { s += 12; why.push("premium rich"); }
        if (isBullish || isNeutral) s += 8;
        if (c.putSafePct > 70) s += 8;
        if (earn) s -= 10;
        break;
      case "ratio_spread":
        s = 40;
        why.push("directional sell");
        if (isRich) s += 10;
        if (!isNeutral) { s += 10; why.push("bias present"); }
        if (earn) { s -= 18; why.push("earnings risk"); }
        break;
      case "wheel":
        s = 55;
        why.push("systematic income");
        if (isRich) s += 10;
        if (isBullish) { s += 10; why.push("bullish bias"); }
        break;
      case "long_call":
        s = 30;
        why.push("directional bull");
        if (isBullish) { s += 22; why.push("bias matches"); }
        else if (isBearish) { s -= 18; why.push("bias against"); }
        if (isCheap) { s += 10; why.push("premium cheap"); }
        if (isRich) s -= 8;
        break;
      case "long_put":
        s = 30;
        why.push("directional bear");
        if (isBearish) { s += 22; why.push("bias matches"); }
        else if (isBullish) { s -= 18; why.push("bias against"); }
        if (isCheap) { s += 10; why.push("premium cheap"); }
        if (isRich) s -= 8;
        break;
      case "long_straddle":
      case "long_strangle":
        s = 25;
        why.push("vol expansion");
        if (isCheap) { s += 28; why.push("premium cheap"); }
        if (earn) { s += 12; why.push("earnings ahead"); }
        if (isRich) { s -= 18; why.push("premium rich"); }
        break;
      case "bull_call_spread":
        s = 40;
        why.push("defined risk bull");
        if (isBullish) { s += 18; why.push("bias matches"); }
        else if (isBearish) { s -= 18; why.push("bias against"); }
        if (isCheap) s += 6;
        break;
      case "bear_put_spread":
        s = 40;
        why.push("defined risk bear");
        if (isBearish) { s += 18; why.push("bias matches"); }
        else if (isBullish) { s -= 18; why.push("bias against"); }
        if (isCheap) s += 6;
        break;
      case "long_butterfly":
        s = 35;
        why.push("pin play");
        if (isNeutral) { s += 12; why.push("neutral bias"); }
        if (isCheap) { s += 8; why.push("premium cheap"); }
        if (earn) s -= 18;
        break;
      case "iron_butterfly":
        s = 50;
        why.push("defined risk credit");
        if (isRich) { s += 14; why.push("premium rich"); }
        if (isVeryHighProb) { s += 8; why.push("history holds"); }
        if (earn) s -= 12;
        if (isCheap) s -= 18;
        break;
      case "long_risk_reversal":
        s = 40;
        why.push("synthetic long");
        if (isBullish) { s += 16; why.push("bias matches"); }
        if (c.richness > 1.0 && c.putSafePct < c.callSafePct) { s += 8; why.push("put skew rich"); }
        if (isBearish) s -= 22;
        break;
      case "short_risk_reversal":
        s = 30;
        why.push("synthetic short");
        if (isBearish) { s += 16; why.push("bias matches"); }
        if (isBullish) s -= 22;
        if (earn) s -= 14;
        break;
      case "call_ratio_backspread":
        s = 25;
        why.push("upside vol play");
        if (isBullish && isCheap) { s += 25; why.push("cheap upside"); }
        else if (isBearish) s -= 18;
        if (earn) { s += 10; why.push("earnings ahead"); }
        break;
      case "put_ratio_backspread":
        s = 25;
        why.push("downside vol play");
        if (isBearish && isCheap) { s += 25; why.push("cheap downside"); }
        else if (isBullish) s -= 18;
        if (earn) { s += 10; why.push("earnings ahead"); }
        break;
      case "long_synthetic":
        s = 40;
        why.push("100-share equiv");
        if (isBullish) { s += 18; why.push("bias matches"); }
        if (isBearish) s -= 22;
        break;
      case "short_synthetic":
        s = 25;
        why.push("100-short equiv");
        if (isBearish) { s += 18; why.push("bias matches"); }
        if (isBullish) s -= 22;
        if (earn) s -= 12;
        break;
      default: break;
    }
    s = Math.max(0, Math.min(100, Math.round(s)));
    // dedupe reasons, keep first three
    const seen = new Set();
    const reasons = [];
    why.forEach(w => { if (!seen.has(w)) { seen.add(w); reasons.push(w); } });
    return { score: s, reason: reasons.slice(0, 3).join(", ") };
  }

  // Build, score, sort. Strategies that the build phase rejected (returned
  // null) are filtered out before ranking.
  const strategies = useMemo(() => {
    const built = window.OptionStrats.STRATEGIES.map(s => {
      let out;
      try {
        out = s.build(stratCtx);
      } catch (err) {
        // A single misbehaving strategy build (e.g. ratio backspread on a
        // ticker with sparse OTM strikes) shouldn't kill the whole list.
        // Drop just that one and keep going.
        console.warn(`Strategy ${s.key} build failed:`, err);
        return null;
      }
      if (!out) return null;
      const sc = scoreFor(s.key, conds);
      return { ...s, ...out, score: sc.score, reason: sc.reason };
    }).filter(Boolean);
    built.sort((a, b) => b.score - a.score);
    return built.map((s, i) => ({ ...s, rank: i + 1 }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stratCtx.sugCall, stratCtx.sugPut, stratCtx.callMid, stratCtx.putMid, stratCtx.currentPrice, conds]);

  // Resolve the active strategy. If selected was filtered out, fall back to first.
  const activeStrat = strategies.find(s => s.key === selectedStrategy) || strategies[0];

  // Publish keys ranked for hot keys (1-9 selects strategy by rank).
  useEffect(() => {
    window.__strategiesByRank = strategies.map(s => s.key);
  }, [strategies]);

  // When the ticker changes, snap the selected strategy back to the top
  // ranked one for the new symbol. Manual clicks still stick within a ticker.
  const topKey = strategies[0]?.key;
  useEffect(() => {
    if (topKey) setSelectedStrategy(topKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker]);

  // Live earnings history (past dates within window + next future date).
  const liveEarnings = useMemo(
    () => (window.MockData.getLiveEarnings ? window.MockData.getLiveEarnings(ticker) : { past: [], next: null }),
    [ticker, dataVersion]
  );

  // Compute live P/L details for the active strategy.
  const plLegs = activeStrat ? activeStrat.legs : [];
  const plRange = useMemo(() => {
    const optionStrikes = plLegs.filter(l => l.type !== "stock").map(l => l.strike);
    const minStrike = optionStrikes.length ? Math.min(...optionStrikes) : currentPrice * 0.9;
    const maxStrike = optionStrikes.length ? Math.max(...optionStrikes) : currentPrice * 1.1;
    const half = Math.max(maxStrike - currentPrice, currentPrice - minStrike, currentPrice * 0.10) * 1.7;
    return { lower: Math.max(0.5, currentPrice - half), upper: currentPrice + half };
  }, [plLegs, currentPrice]);
  const plCurve = useMemo(
    () => plLegs.length ? window.OptionStrats.pnlCurve(plLegs, plRange.lower, plRange.upper, 240) : [],
    [plLegs, plRange.lower, plRange.upper]
  );
  const plBounds = useMemo(
    () => plCurve.length ? window.OptionStrats.pnlBounds(plCurve) : { min: 0, max: 0 },
    [plCurve]
  );
  const plBreakEvens = useMemo(
    () => plCurve.length ? window.OptionStrats.breakEvens(plCurve) : [],
    [plCurve]
  );
  const plNetCredit = activeStrat ? window.OptionStrats.netCredit(plLegs) : 0;
  const fmt$ = window.fmt$ || (v => `${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(2)}`);

  // Net Greeks for the active strategy. Maps each leg to its actual Greeks
  // via the chain (FP-safe lookup with rounded keys). Stock legs contribute
  // pure delta = qty/100. Back-month legs that don't appear in the front
  // chain are skipped — for calendar/diagonal we'd need a separate fetch
  // for the back expiration's chain to get accurate Greeks, which is on
  // the roadmap. For now, those strategies show "front leg only" with a
  // tag indicating partial.
  const netGreeks = useMemo(() => {
    if (!activeStrat || !plLegs.length) return null;
    const skey = s => (Math.round(s * 100) / 100).toFixed(2);
    const callMap = Object.fromEntries(calls.map(c => [skey(c.strike), c]));
    const putMap = Object.fromEntries(puts.map(p => [skey(p.strike), p]));
    let netDelta = 0, netGamma = 0, netTheta = 0, netVega = 0;
    let coveredLegs = 0, totalLegs = 0;
    for (const L of plLegs) {
      totalLegs += 1;
      const qty = L.qty / 100; // contracts (or 100-share blocks for stock)
      if (L.type === "stock") {
        netDelta += qty;        // 1 delta per share, 100 shares per "qty"
        coveredLegs += 1;
        continue;
      }
      const map = L.type === "call" ? callMap : putMap;
      const row = map[skey(L.strike)];
      // If leg's DTE is meaningfully different from the active expiration,
      // treat as out-of-coverage rather than misattributing front-month
      // Greeks to a back-month leg.
      const isFront = !FRONT_DTE || Math.abs((L.dte || 0) - FRONT_DTE) <= 7;
      if (!row || !isFront) continue;
      coveredLegs += 1;
      netDelta += qty * (row.delta || 0);
      netGamma += qty * (row.gamma || 0);
      netTheta += qty * (row.theta || 0);
      netVega  += qty * (row.vega  || 0);
    }
    return {
      delta: netDelta,
      gamma: netGamma,
      theta: netTheta,
      vega: netVega,
      coveredLegs,
      totalLegs,
      partial: coveredLegs < totalLegs,
    };
  }, [activeStrat, plLegs, calls, puts, FRONT_DTE]);

  const isBackMonthStrat = activeStrat && (activeStrat.key === "calendar_spread" || activeStrat.key === "diagonal_spread");

  // Mobile sticky header values.
  const _mhChg = (liveQuotes[ticker]?.change_pct != null) ? liveQuotes[ticker].change_pct : stockDeltaPct;
  const _sectionLabel = activeTab ? activeTab.charAt(0).toUpperCase() + activeTab.slice(1) : "";
  const _staleMin = lastFetched ? Math.floor((nowTs - lastFetched) / 60000) : null;
  const _isStale = _staleMin != null && _staleMin >= 5;

  return (
    <div className="shell">
      {/* Mobile sticky header (phones/tablets only; hidden on desktop via CSS) */}
      <header className="mobile-header">
        <button className="mh-btn mh-burger" aria-label="Open menu" onClick={() => setNavOpen(true)}>☰</button>
        <button className="mh-ident" onClick={() => setNavOpen(true)} aria-label="Switch ticker">
          <span className="mh-sym">{ticker}<span className="mh-search-ico" aria-hidden="true">⌕</span></span>
          {!loadError && currentPrice != null && (
            <span className="mh-quote">
              ${Number(currentPrice).toFixed(2)}
              <span className={`mh-chg ${_mhChg >= 0 ? "up" : "down"}`}>{_mhChg >= 0 ? "▲" : "▼"} {Math.abs(_mhChg).toFixed(2)}%</span>
            </span>
          )}
        </button>
        <span className="mh-section">{loading ? "Loading…" : _isStale ? `${_staleMin}m old` : _sectionLabel}</span>
        <button className={`mh-btn mh-refresh${_isStale ? " stale" : ""}`} aria-label="Refresh"
                title={lastFetched ? `Updated ${_staleMin || 0}m ago` : "Refresh"}
                onClick={refreshData} disabled={loading}>↻</button>
      </header>
      <div className={`mobile-overlay${navOpen ? " show" : ""}`} onClick={() => setNavOpen(false)} aria-hidden="true" />

      {/* Tab bar (v1.25) — full-width section switcher, spans both columns */}
      <TabBar active={activeTab} onChange={changeTab} ticker={ticker}
              earnDate={loadError ? null : current.next_earnings}
              earnDays={loadError ? null : current.days_to_earnings} />
      {/* ── SIDEBAR ───────────────────────────────────────────────────────── */}
      <aside className={`sidebar${navOpen ? " nav-open" : ""}`}>
        <div className="sb-version-pill" title="App version">v{APP_VERSION}</div>
        <WeatherBadge />
        <div className="sb-section sb-brand">
          <img className="brand-mark" src="/assets/app-logo.png" alt="Jerry" />
          <div className="sb-brand-text">
            <div className="sb-status">
              {loading
                ? "Fetching."
                : window.__LIVE
                  ? `Live. ${(() => {
                      // Live ET wall clock — updates every second via nowTs state.
                      try {
                        const d = new Date(nowTs);
                        const dateFmt = new Intl.DateTimeFormat("en-US", {
                          timeZone: "America/New_York",
                          year: "numeric", month: "numeric", day: "numeric",
                        }).format(d).replace(/\//g, "-");
                        const timeFmt = new Intl.DateTimeFormat("en-US", {
                          timeZone: "America/New_York",
                          hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true,
                        }).format(d);
                        return `${dateFmt} ${timeFmt}`;
                      } catch {
                        return new Date(nowTs).toString();
                      }
                    })()}`
                  : "Static snapshot"}
            </div>
            {loadError && <div className="sb-status err">{loadError}</div>}
            <div className="sb-source-badges">
            {dataSource && (() => {
              const sw = dataSource.schwab || {};
              const last = dataSource.last_source || "yfinance";
              if (!sw.configured) {
                return (
                  <div className="src-badge src-badge-yf"
                       title={`Schwab not configured (${sw.reason || "unknown"}). Using yfinance.`}>
                    <span className="src-dot" /> yfinance
                  </div>
                );
              }
              if (last === "schwab") {
                const accLeft = Math.round((sw.access_remaining_sec || 0) / 60);
                const refrLeft = sw.refresh_remaining_days;
                return (
                  <div className={`src-badge src-badge-schwab${sw.needs_refresh_soon ? " src-badge-warn" : ""}`}
                       title={`Schwab live · access token ${accLeft}m left · refresh ${refrLeft}d left`}>
                    <span className="src-dot" /> Schwab live
                  </div>
                );
              }
              return (
                <div className="src-badge src-badge-yf"
                     title="Schwab configured but last call fell back to yfinance.">
                  <span className="src-dot" /> yfinance fallback
                </div>
              );
            })()}
            {uwHealth && (() => {
              if (!uwHealth.configured) {
                return (
                  <div className="src-badge src-badge-uw-off"
                       title="Unusual Whales not configured. Set UW_API_KEY in ~/.jerry-dashboard/.env to enable flow data.">
                    <span className="src-dot" /> UW off
                  </div>
                );
              }
              const rate = uwHealth.rate || {};
              const rem = rate.req_per_minute_remaining;
              const daily = rate.daily_req_count;
              const limit = rate.token_req_limit;
              if (!uwHealth.connected) {
                return (
                  <div className="src-badge src-badge-uw-err"
                       title={`UW configured but last call failed: ${uwHealth.error || "unknown"}`}>
                    <span className="src-dot" /> UW error
                  </div>
                );
              }
              // Color the badge based on minute-quota remaining
              const lowQuota = typeof rem === "number" && rem <= 5;
              const dailyTxt = (typeof daily === "number" && typeof limit === "number")
                ? `${daily.toLocaleString()}/${limit.toLocaleString()}`
                : (typeof daily === "number" ? `${daily.toLocaleString()}` : "—");
              const remTxt = typeof rem === "number" ? `${rem}/min left` : "min quota unknown";
              return (
                <div className={`src-badge src-badge-uw${lowQuota ? "-warn" : ""}`}
                     title={`Unusual Whales connected · ${remTxt} · today ${dailyTxt} requests`}>
                  <span className="src-dot" /> UW · {typeof rem === "number" ? rem : "—"}/min
                </div>
              );
            })()}
            </div>
          </div>
          <div className="sb-brand-actions">
            <button className="sb-icon-btn sb-theme-btn"
                    onClick={() => tweaks.setValue("theme", tweaks.values.theme === "dark" ? "light" : "dark")}
                    title={tweaks.values.theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
                    aria-label="Toggle theme">
              {/* Toggle pill icon — orientation flips with current theme */}
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <rect x="2" y="7" width="20" height="10" rx="5"
                      fill="none" stroke="currentColor" strokeWidth="1.7" />
                <circle cx={tweaks.values.theme === "dark" ? "17" : "7"} cy="12" r="3.2"
                        fill="currentColor" />
              </svg>
            </button>
            <button className="sb-icon-btn sb-ref-btn" onClick={() => setShowRef(true)}
                    title="Strategy reference" aria-label="Strategy cheat sheet">
              {/* Open book icon */}
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none"
                   stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="M2 4 C 6 3, 10 3.5, 12 5 C 14 3.5, 18 3, 22 4 L 22 20 C 18 19, 14 19.5, 12 21 C 10 19.5, 6 19, 2 20 Z" />
                <line x1="12" y1="5" x2="12" y2="21" />
              </svg>
            </button>
            <button className="sb-icon-btn sb-help-btn" onClick={() => setShowHelp(true)}
                    title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts">
              {/* Cmd-K icon — command glyph + K */}
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none"
                   stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                {/* Command symbol on the left */}
                <g transform="translate(2.5, 5.5)">
                  <circle cx="2" cy="2" r="1.6" />
                  <circle cx="2" cy="9" r="1.6" />
                  <circle cx="9" cy="2" r="1.6" />
                  <circle cx="9" cy="9" r="1.6" />
                  <line x1="3.6" y1="2" x2="7.4" y2="2" />
                  <line x1="3.6" y1="9" x2="7.4" y2="9" />
                  <line x1="2" y1="3.6" x2="2" y2="7.4" />
                  <line x1="9" y1="3.6" x2="9" y2="7.4" />
                </g>
                {/* K on the right */}
                <text x="15" y="16.5" fontSize="10" fontFamily="ui-monospace, monospace" fontWeight="800"
                      fill="currentColor" stroke="none">K</text>
              </svg>
            </button>
          </div>
        </div>

        <div className="sb-section">
          <div className="sb-label">Ticker</div>
          <div className="sb-ticker-row">
            <div className="sb-ticker-left">
              <div className="sb-ticker-wrap sb-ticker-wrap-compact">
                <input
                  ref={tickerInputRef}
                  className="sb-ticker-input"
                  value={tickerInput}
                  onChange={(e) => { setTickerInput(e.target.value.toUpperCase().slice(0, 6)); setSearchOpen(true); }}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowDown") { e.preventDefault(); setSearchActive(i => Math.min(searchResults.length - 1, i + 1)); setSearchOpen(true); }
                    else if (e.key === "ArrowUp") { e.preventDefault(); setSearchActive(i => Math.max(-1, i - 1)); }
                    else if (e.key === "Escape") { setSearchOpen(false); setSearchActive(-1); e.target.blur(); }
                    else if (e.key === "Enter") {
                      let chosen = tickerInput;
                      if (searchOpen && searchActive >= 0 && searchResults[searchActive]) {
                        chosen = searchResults[searchActive].symbol;
                        setTickerInput(chosen);
                      }
                      setTicker(chosen);
                      setSearchOpen(false);
                      setSearchActive(-1);
                      requestAnimationFrame(() => {
                        if (tickerInputRef.current) tickerInputRef.current.select();
                      });
                    }
                  }}
                  onFocus={(e) => { setSearchOpen(true); e.target.select(); }}
                  onBlur={() => { setTimeout(() => setSearchOpen(false), 120); }}
                  placeholder="search"
                  autoComplete="off"
                  spellCheck="false"
                />
                {searchOpen && searchResults.length > 0 && (
                  <div className="sb-search-dropdown">
                    {searchResults.map((r, i) => (
                      <div key={r.symbol}
                           className={`sb-search-item ${i === searchActive ? "active" : ""}`}
                           onMouseDown={(e) => { e.preventDefault(); setTicker(r.symbol); setTickerInput(r.symbol); setSearchOpen(false); }}
                           onMouseEnter={() => setSearchActive(i)}>
                        <span className="sb-search-sym">{r.symbol}</span>
                        <span className="sb-search-name">{r.name}</span>
                        {r.exchange && <span className="sb-search-ex">{r.exchange}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="sb-ticker-name-line">{loadError ? <span className="muted">—</span> : current.name}</div>
              {!loadError && current.dividend_yield != null && current.dividend_yield > 0 && (
                <div className="sb-divyield" title={`Trailing annual dividend yield for ${ticker}, from the stock's last close.`}>
                  Div yield {current.dividend_yield.toFixed(2)}%
                </div>
              )}
            </div>
            <div className="sb-ticker-display">
              <TickerLogo ticker={ticker} />
              <div className="sb-ticker-price-row">
                {loadError ? (
                  // When the active ticker fetch failed, hide the stale
                  // price/change display so user doesn't think they're
                  // seeing live data for the bad symbol. The error
                  // message is already shown elsewhere in the sidebar.
                  <span className="sb-price muted">—</span>
                ) : (() => {
                  // currentPrice is already live (overridden in App scope
                  // with getLivePrice(ticker)). Live change_pct preferred
                  // when polled, else fall back to the payload's snapshot.
                  const liveChg = liveQuotes[ticker]?.change_pct;
                  const displayChg = liveChg != null ? liveChg : stockDeltaPct;
                  // Stale-quote detection. stale_seconds comes from the
                  // backend Schwab quote and reflects how long ago the
                  // last trade printed for the picked session (regular
                  // or extended). Threshold: > 5 min is the standard
                  // "this isn't really live" cut-off for liquid tickers.
                  // For pre-market on illiquid names, last trade can be
                  // hours old; the label is informational, not a defect.
                  const staleSec = liveQuotes[ticker]?.stale_seconds;
                  const isStale = staleSec != null && staleSec > 300;
                  const staleTip = staleSec != null
                    ? (staleSec < 60 ? `Live · ${staleSec}s since last trade`
                       : staleSec < 3600 ? `Last trade ${Math.round(staleSec/60)}m ago`
                       : `Last trade ${Math.round(staleSec/3600)}h ago — illiquid or session boundary`)
                    : null;
                  return (
                    <>
                      <FlashOnChange value={currentPrice} className={`sb-price${isStale ? " sb-price-stale" : ""}`}>
                        <span title={staleTip || undefined}>
                          ${currentPrice.toFixed(2)}
                          {isStale && <span className="sb-stale-marker" title={staleTip}> · stale</span>}
                        </span>
                      </FlashOnChange>
                      <span className={`delta ${displayChg >= 0 ? "up" : "down"}`}>
                        {displayChg >= 0 ? "▲" : "▼"} {Math.abs(displayChg).toFixed(2)}%
                      </span>
                    </>
                  );
                })()}
              </div>
              {!loadError && (current.pe != null || current.forward_pe != null) && (
                <div className="sb-pe" title="Trailing and forward price-to-earnings ratio">
                  P/E {current.pe != null ? current.pe : "—"} · Fwd {current.forward_pe != null ? current.forward_pe : "—"}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="sb-section">
          <div className="sb-label-row">
            <span className="sb-label">Watchlist</span>
            <div className="sb-wl-actions">
              <button
                className="sb-pin-btn"
                title={
                  watchlistData.symbols.find(s => s.symbol === ticker)?.starred
                    ? "Unstar this ticker"
                    : (watchlist.includes(ticker) ? "Star this ticker" : "Add and star this ticker")
                }
                onClick={() => {
                  const existing = watchlistData.symbols.find(s => s.symbol === ticker);
                  if (existing) {
                    wlToggleStar(ticker);
                  } else {
                    wlAddSymbol(ticker, { starred: true });
                  }
                }}>
                {watchlistData.symbols.find(s => s.symbol === ticker)?.starred ? "★ starred" : "☆ star"}
              </button>
              <button
                className="sb-manage-btn"
                title="Manage watchlist"
                onClick={() => setShowWatchlistManager(true)}>
                Manage ({watchlistData.symbols.length})
              </button>
            </div>
          </div>
          <div className="sb-preset-row">
            {starredSymbols.length === 0 && (
              <div className="sb-watchlist-empty">
                No starred tickers. Click ☆ to star, or Manage to add.
              </div>
            )}
            {starredSymbols.map(t => (
              <button
                key={`wl-${t}`}
                className={`preset-pill ${ticker === t ? "active" : ""}`}
                onClick={() => { setTicker(t); setTickerInput(t); }}
                onContextMenu={(e) => {
                  e.preventDefault();
                  wlToggleStar(t);
                }}
                title="Click to switch · right-click to unstar"
              >{t}</button>
            ))}
          </div>
        </div>

        <div className="sb-section">
          <div className="sb-label">Presets</div>
          <div className="sb-preset-row">
            {Object.keys(window.MockData.PRESETS).map(t => (
              <button
                key={t}
                className={`preset-pill ${ticker === t ? "active" : ""}`}
                onClick={() => { setTicker(t); setTickerInput(t); }}
              >{t}</button>
            ))}
          </div>
        </div>

        <div className="sb-section">
          <div className="sb-row">
            <span className="sb-label">Weeks of history</span>
            <span className="sb-val">{weeks}</span>
          </div>
          <input className="sb-slider" type="range" min="4" max="52" step="1"
                 value={weeks} onChange={e => setWeeks(+e.target.value)} />
        </div>

        <div className="sb-section">
          <div className="sb-label">Strike picker</div>
          <div className="seg" style={{marginBottom: 8}}>
            <button className={strikeMode === "delta" ? "active" : ""} onClick={() => setStrikeMode("delta")}>By delta</button>
            <button className={strikeMode === "buffer" ? "active" : ""} onClick={() => setStrikeMode("buffer")}>By buffer</button>
          </div>
          {strikeMode === "delta" ? (
            <>
              <div className="sb-row">
                <span className="sb-label-sub">Target delta</span>
                <span className="sb-val">{targetDelta.toFixed(2)}</span>
              </div>
              <input className="sb-slider" type="range" min="0.05" max="0.45" step="0.01"
                     value={targetDelta} onChange={e => setTargetDelta(+e.target.value)} />
              <div className="sb-hint">Calls picked at +{targetDelta.toFixed(2)} · puts at -{targetDelta.toFixed(2)}</div>
            </>
          ) : (
            <>
              <div className="sb-row">
                <Term k="buffer" className="sb-label-sub">Buffer % beyond expected range</Term>
                <span className="sb-val">{bufferPct.toFixed(1)}%</span>
              </div>
              <input className="sb-slider" type="range" min="0" max="10" step="0.5"
                     value={bufferPct} onChange={e => setBufferPct(+e.target.value)} />
            </>
          )}
        </div>

        <div className="sb-section">
          <Term k="baseline" className="sb-label">Return baseline</Term>
          <select className="sb-select" value={baseline} onChange={e => setBaseline(e.target.value)}>
            <option value="monday">Monday Open</option>
            <option value="friday">Previous Friday Close</option>
          </select>
        </div>

        <div className="sb-section">
          <div className="sb-label">Expiration</div>
          {liveExpirations.length > 0 ? (
            <select
              className="sb-select"
              value={expiration || liveExpirations[0]}
              onChange={e => setExpiration(e.target.value)}
            >
              {liveExpirations.map(d => {
                const dt = new Date(d + "T16:00:00");
                const dteMs = dt.getTime() - Date.now();
                const dte = Math.max(0, Math.ceil(dteMs / 86400000));
                const label = dt.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"})
                            + `   ${dte}d`;
                return <option key={d} value={d}>{label}</option>;
              })}
            </select>
          ) : (
            <div className="sb-expiry">
              {activeExpDate.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"})}
              <span className="sb-expiry-dte">{FRONT_DTE}d</span>
            </div>
          )}
        </div>
      </aside>

      {/* ── MAIN ──────────────────────────────────────────────────────────── */}
      <main className="main">
        <TabPanel tab="discover" active={activeTab}>
          <CardErrorBoundary label="Discovery screeners">
            <ScreenersHub
              apiFetch={apiFetch}
              onSwitchTicker={switchTicker}
            />
          </CardErrorBoundary>
        </TabPanel>
        <TabPanel tab="patterns" active={activeTab}>
          <CardErrorBoundary label="Swing patterns">
            <SwingPatternCard apiFetch={apiFetch} ticker={ticker} />
          </CardErrorBoundary>
        </TabPanel>
        <TabPanel tab="news" active={activeTab}>
          <CardErrorBoundary label="News">
            <NewsCard apiFetch={apiFetch} ticker={ticker} companyName={loadError ? "" : (current && current.name) || ""} />
          </CardErrorBoundary>
        </TabPanel>
        {showRef && (
          <div className="hk-overlay" onClick={() => setShowRef(false)}>
            <div className="ref-card" onClick={e => e.stopPropagation()}>
              <div className="hk-head">
                <div className="hk-title">Strategy reference</div>
                <button className="hk-close" onClick={() => setShowRef(false)}>×</button>
              </div>
              <StrategyReferenceCard />
            </div>
          </div>
        )}
        {showWatchlistManager && (
          <div className="hk-overlay" onClick={() => setShowWatchlistManager(false)}>
            <div className="wlm-card" onClick={e => e.stopPropagation()}>
              <div className="hk-head">
                <div className="hk-title">Watchlist · {watchlistData.symbols.length} {watchlistData.symbols.length === 1 ? "symbol" : "symbols"}</div>
                <button className="hk-close" onClick={() => setShowWatchlistManager(false)}>×</button>
              </div>
              <WatchlistManager
                data={watchlistData}
                onAdd={wlAddSymbol}
                onRemove={wlRemoveSymbol}
                onToggleStar={wlToggleStar}
                onUpdate={wlUpdateSymbol}
                onBulkAdd={wlBulkAdd}
                onSwitchTicker={(t) => { setTicker(t); setTickerInput(t); setShowWatchlistManager(false); }}
              />
            </div>
          </div>
        )}
        {showHelp && (
          <div className="hk-overlay" onClick={() => setShowHelp(false)}>
            <div className="hk-card" onClick={e => e.stopPropagation()}>
              <div className="hk-head">
                <div className="hk-title">Keyboard shortcuts</div>
                <button className="hk-close" onClick={() => setShowHelp(false)}>×</button>
              </div>
              <div className="hk-grid">
                <div className="hk-row"><kbd>/</kbd><span>Focus ticker search</span></div>
                <div className="hk-row"><kbd>a</kbd>–<kbd>z</kbd><span>Start typing to search a new ticker</span></div>
                <div className="hk-row"><kbd>[</kbd> / <kbd>]</kbd><span>Previous / next expiration</span></div>
                <div className="hk-row"><kbd>1</kbd>–<kbd>9</kbd><span>Select strategy by rank (top 9)</span></div>
                <div className="hk-row"><kbd>?</kbd><span>Show / hide this help</span></div>
                <div className="hk-row"><kbd>Esc</kbd><span>Close help / exit search dropdown</span></div>
              </div>
              <div className="hk-foot">
                Hot keys ignore inputs, so you can type freely in any text field.
              </div>
            </div>
          </div>
        )}
        {/* Loading progress bar — animates during fetch, fades when done */}
        <div className={`load-bar ${loading ? "loading" : ""}`}>
          <div className="load-bar-fill"></div>
        </div>
        {/* Slide-out percent calculator — anchors to the right edge */}
        <PercentCalc activeTicker={ticker} livePrice={getLivePrice(ticker) ?? currentPrice} />
        {/* Error banner — replaces tiny sidebar error with prominent display */}
        {loadError && !loading && (
          <div className="error-banner">
            <span className="ico">⚠</span>
            <div>
              <b>Couldn't load {ticker}.</b> <span className="muted">{loadError}</span>
            </div>
            <button className="error-retry" onClick={() => setDataVersion(v => v + 1)}>Retry</button>
          </div>
        )}
        {/* Earnings banner */}
        {current.earnings && (
          <div className="earnings-banner">
            <span className="ico">⚠</span>
            <div>
              <b>Earnings reported this week.</b> <span className="muted">Expected moves can be much larger than historical medians. Consider trimming size or skipping the trade.</span>
            </div>
          </div>
        )}

        {/* Assignment risk monitor — scans all open positions every
            render. If any short leg is ITM or within 3% of current
            price (only checkable for positions whose ticker matches
            the active dashboard ticker), surface a top-of-page banner.
            Click banner to scroll to the positions card. */}
        {(() => {
          const O = window.OptionStrats;
          if (!O) return null;
          const open = positions.filter(p => p.status === "open");
          if (!open.length) return null;
          // Only positions on the active ticker can be evaluated against
          // currentPrice. For others we don't know if they're at risk.
          const evaluated = open
            .filter(p => p.ticker === ticker && currentPrice)
            .map(p => {
              let worst = null;  // {leg, distance, tier}
              for (const leg of p.legs) {
                if (leg.qty >= 0) continue;  // long legs don't get assigned
                const itm = (leg.type === "call" && currentPrice > leg.strike)
                         || (leg.type === "put"  && currentPrice < leg.strike);
                const distPct = Math.abs(currentPrice - leg.strike) / leg.strike;
                let tier = null;
                if (itm) tier = "itm";
                else if (distPct < 0.01) tier = "very-close";
                else if (distPct < 0.03) tier = "close";
                if (tier && (!worst || ["close", "very-close", "itm"].indexOf(tier)
                                       > ["close", "very-close", "itm"].indexOf(worst.tier))) {
                  worst = { leg, distPct, tier };
                }
              }
              return worst ? { p, ...worst } : null;
            })
            .filter(Boolean);
          if (!evaluated.length) return null;
          // Highest-severity tier across all flagged positions sets the
          // banner color. ITM > very-close > close.
          const order = { itm: 3, "very-close": 2, close: 1 };
          const topTier = evaluated.reduce((best, e) =>
            order[e.tier] > order[best] ? e.tier : best, "close");
          const tierLabel = topTier === "itm" ? "IN THE MONEY"
                          : topTier === "very-close" ? "VERY CLOSE"
                          : "CLOSE";
          return (
            <div className={`assign-banner assign-${topTier}`}
                 onClick={() => {
                   const el = document.querySelector(".pos-list");
                   if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
                 }}>
              <span className="assign-icon">⚠</span>
              <span className="assign-title">
                Assignment risk · {tierLabel}
              </span>
              <span className="assign-detail">
                {evaluated.length} position{evaluated.length === 1 ? "" : "s"} flagged
                {" · "}
                {evaluated.map(e => {
                  const leg = e.leg;
                  const sign = leg.qty < 0 ? "−" : "+";
                  const t = leg.type[0].toUpperCase();
                  return `${e.p.ticker} ${sign}${t}$${leg.strike.toFixed(2)}`;
                }).join(", ")}
              </span>
              <span className="assign-arrow">→ View positions</span>
            </div>
          );
        })()}

        {/* Watchlist alerts (v1.15) — fresh upgrades and downgrades on
            watchlist tickers in the past 7 days. Only renders when there
            are alerts to show. Auto-hides when empty. */}
        <WatchlistAlertsCard
          apiFetch={apiFetch}
          onSwitchTicker={switchTicker}
        />

        {/* Roll Manager — only renders if the active ticker has open
            short calls. Computes 3 roll choices (same strike +1wk,
            +$5 +1wk, +$10 +1wk) plus buy-back close, with net credit. */}
        <TabPanel tab="trade" active={activeTab}>
        {rec && (() => {
          const mode = strategyMode || "both";
          const cc = rec.cc || (rec.title ? { kind: rec.kind, title: rec.title } : null);
          const csp = rec.csp || null;
          const TONE = { success: "go", warn: "warn", danger: "down", info: "muted" };
          return (
            <div className="trade-actionread" aria-label="Trade action read">
              {(mode === "both" || mode === "cc") && cc && cc.title && (
                <span className={`tar-chip tone-${TONE[cc.kind] || "muted"}`}>
                  <span className="tar-tag">Covered call</span><b>{cc.title}</b>
                </span>
              )}
              {(mode === "both" || mode === "csp") && csp && csp.title && (
                <span className={`tar-chip tone-${TONE[csp.kind] || "muted"}`}>
                  <span className="tar-tag">Cash-secured put</span><b>{csp.title}</b>
                </span>
              )}
            </div>
          );
        })()}
        <div id="jump-roll" className="jump-anchor" aria-hidden="true"></div>
        <RollManagerCard
          ticker={ticker}
          positions={positions}
          currentPrice={currentPrice}
          livePrice={getLivePrice(ticker) ?? currentPrice}
          apiFetch={apiFetch}
          uwHealth={uwHealth}
        />

        {/* Hero: chart + strikes */}
        <div id="jump-chart" className="jump-anchor" aria-hidden="true"></div>
        <div className="hero">
          <div className="card chart-card">
            {/* Perf pills — absolutely positioned at the card's top-right
                so they don't compete with the toolbar for horizontal space. */}
            {(() => {
              if (!daily || daily.length === 0) return null;
              const last = daily[daily.length - 1];
              if (!last || last.close == null) return null;
              const lastDate = new Date(last.date);
              if (Number.isNaN(lastDate.getTime())) return null;
              const anchorClose = (threshold) => {
                for (let i = daily.length - 1; i >= 0; i--) {
                  const d = new Date(daily[i].date);
                  if (Number.isNaN(d.getTime())) continue;
                  if (d < threshold) return daily[i].close;
                }
                return null;
              };
              const wkStart = new Date(lastDate);
              const dow = wkStart.getDay();
              const daysSinceMon = (dow + 6) % 7;
              wkStart.setDate(wkStart.getDate() - daysSinceMon);
              wkStart.setHours(0, 0, 0, 0);
              const moStart = new Date(lastDate.getFullYear(), lastDate.getMonth(), 1);
              const qStartMonth = Math.floor(lastDate.getMonth() / 3) * 3;
              const qStart = new Date(lastDate.getFullYear(), qStartMonth, 1);
              const yStart = new Date(lastDate.getFullYear(), 0, 1);
              const wtdAnchor = anchorClose(wkStart);
              const mtdAnchor = anchorClose(moStart);
              const qtdAnchor = anchorClose(qStart);
              const ytdAnchor = anchorClose(yStart);
              const pct = (anchor) => anchor != null && anchor > 0 ? ((last.close - anchor) / anchor) * 100 : null;
              const wtd = pct(wtdAnchor);
              const mtd = pct(mtdAnchor);
              const qtd = pct(qtdAnchor);
              const ytd = pct(ytdAnchor);
              const fmt = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
              const cls = (v) => v == null ? "" : v >= 0 ? "up" : "down";
              return (
                <div className="chart-perf-floating" title="Performance vs the close on the day before each period started">
                  <div className="chart-perf-pill" title="Week-to-date — return since last Friday's close">
                    <div className="chart-perf-lbl">WTD</div>
                    <div className={`chart-perf-val ${cls(wtd)}`}>{fmt(wtd)}</div>
                  </div>
                  <div className="chart-perf-pill" title="Month-to-date — return since the close on the last day of the prior month">
                    <div className="chart-perf-lbl">MTD</div>
                    <div className={`chart-perf-val ${cls(mtd)}`}>{fmt(mtd)}</div>
                  </div>
                  <div className="chart-perf-pill" title="Quarter-to-date — return since the close on the last day of the prior quarter">
                    <div className="chart-perf-lbl">QTD</div>
                    <div className={`chart-perf-val ${cls(qtd)}`}>{fmt(qtd)}</div>
                  </div>
                  <div className="chart-perf-pill" title="Year-to-date — return since last year's final close">
                    <div className="chart-perf-lbl">YTD</div>
                    <div className={`chart-perf-val ${cls(ytd)}`}>{fmt(ytd)}</div>
                  </div>
                </div>
              );
            })()}
            <div className="card-head">
              <div>
                <div className="kicker">{chartDays} day price · expected weekly range</div>
                <div className="card-title">
                  {ticker} candlestick
                  {(() => {
                    if (!liveEarnings || !liveEarnings.next) return null;
                    const nd = new Date(liveEarnings.next + "T16:00:00");
                    if (Number.isNaN(nd.getTime())) return null;
                    const days = Math.ceil((nd.getTime() - Date.now()) / 86400000);
                    if (days < 0 || days > 60) return null;
                    const cls = days <= 7 ? "earn-pill urgent" : days <= 14 ? "earn-pill close" : "earn-pill";
                    const label = nd.toLocaleDateString("en-US", { month: "short", day: "numeric" });
                    return <span className={cls}>Earnings in {days}d · {label}</span>;
                  })()}
                </div>
              </div>
              <div className="toolbar">
                <div className="seg ma-toggles">
                  <button className={showEMA21 ? "active ema21" : "ema21"}
                          onClick={() => setShowEMA21(v => !v)}
                          title="Toggle EMA21 (TradingView ta.ema, length 21)">EMA21</button>
                  <button className={showMA50 ? "active ma50" : "ma50"}
                          onClick={() => setShowMA50(v => !v)}
                          title="Toggle 50-day moving average">MA50</button>
                  <button className={showMA200 ? "active ma200" : "ma200"}
                          onClick={() => setShowMA200(v => !v)}
                          title="Toggle 200-day moving average">MA200</button>
                  <button className={showRSI ? "active rsi" : "rsi"}
                          onClick={() => setShowRSI(v => !v)}
                          title="Toggle RSI14 panel below the chart. Wilder smoothing, standard 14-period.">RSI</button>
                  <button className={showProbCone ? "active cone" : "cone"}
                          onClick={() => setShowProbCone(v => !v)}
                          title="Probability cone (±1σ / ±2σ to expiration)">CONE</button>
                </div>
                <div className="seg" title="Visible day range">
                  {[[30,"30D"],[60,"60D"],[120,"120D"],[250,"250D"]].map(([n, l]) => (
                    <button key={n} className={!viewRange && chartDays === n ? "active" : ""}
                            onClick={() => { setChartDays(n); setViewRange(null); }}
                            title={`Show last ${n} trading days`}>{l}</button>
                  ))}
                  {viewRange && (
                    <button className="zoom-reset"
                            onClick={() => setViewRange(null)}
                            title="Reset zoom and pan">RESET</button>
                  )}
                </div>
                <div className="seg">
                  {[["candles","Candles"],["area","Area"],["ohlc","OHLC"]].map(([v, l]) => (
                    <button key={v} className={chartStyle === v ? "active" : ""}
                            onClick={() => tweaks.setValue("chartStyle", v)}>{l}</button>
                  ))}
                </div>
              </div>
            </div>
            {(() => {
              // Compute the visible window from either the explicit viewRange
              // (set by wheel/drag interaction) or the chartDays preset.
              const len = daily.length;
              const start = viewRange ? viewRange[0] : Math.max(0, len - chartDays);
              const end = viewRange ? viewRange[1] : len;
              const visibleDaily = daily.slice(start, end);
              return (
                <CardErrorBoundary label="Price chart">
                <PriceChart daily={visibleDaily} expHigh={expHigh} expLow={expLow}
                            callStrike={sugCall} putStrike={sugPut} currentPrice={currentPrice}
                            chartStyle={chartStyle} colors={chartColors}
                            earnings={liveEarnings}
                            showMA50={showMA50} showMA200={showMA200}
                            showEMA21={showEMA21}
                            showRSI={showRSI}
                            showProbCone={showProbCone}
                            ivAnnualized={(callAtSug.iv && putAtSug.iv) ? (callAtSug.iv + putAtSug.iv) / 2 : (callAtSug.iv || putAtSug.iv || null)}
                            dteToExp={FRONT_DTE}
                            fullDailyLength={len}
                            visibleStart={start}
                            onViewRangeChange={setViewRange} />
                </CardErrorBoundary>
              );
            })()}
            <div className="legend" style={{marginTop: 12}}>
              <span className="item"><span className="swatch dashed" style={{borderColor: chartColors.up}}></span>Suggested call</span>
              <span className="item"><span className="swatch dashed" style={{borderColor: chartColors.down}}></span>Suggested put</span>
              <span className="item"><span className="swatch" style={{background: chartColors.accent, opacity: 0.18, height: 10}}></span><Term k="expected_range">Expected weekly range</Term></span>
              <span className="item"><span className="swatch" style={{background: chartColors.up, height: 3}}></span>Current price</span>
              {showEMA21 && <span className="item"><span className="swatch" style={{background: "rgb(80, 160, 255)", height: 2}}></span>EMA 21</span>}
              {showMA50 && <span className="item"><span className="swatch" style={{background: chartColors.warn, height: 2}}></span>MA 50</span>}
              {showMA200 && <span className="item"><span className="swatch" style={{background: chartColors.accent, height: 2}}></span>MA 200</span>}
              {showProbCone && <span className="item"><span className="swatch" style={{background: chartColors.accent, opacity: 0.22, height: 10}}></span>Prob cone ±1σ/±2σ</span>}
              {showMA50 && showMA200 && (() => {
                const last = daily[daily.length - 1];
                if (!last || last.ma50 == null || last.ma200 == null) return null;
                const isAbove = last.ma50 > last.ma200;
                // Find most recent cross within the visible window
                let lastCross = null;
                for (let i = daily.length - 1; i > 0; i--) {
                  const a50 = daily[i - 1].ma50, a200 = daily[i - 1].ma200;
                  const b50 = daily[i].ma50, b200 = daily[i].ma200;
                  if (a50 == null || a200 == null || b50 == null || b200 == null) continue;
                  if ((a50 < a200 && b50 > b200) || (a50 > a200 && b50 < b200)) {
                    lastCross = { idx: i, daysAgo: daily.length - 1 - i, type: a50 < a200 ? "golden" : "death", date: daily[i].date };
                    break;
                  }
                }
                return (
                  <span className="item ma-regime">
                    <span className="ma-pip" style={{background: isAbove ? "var(--up)" : "var(--down)"}}></span>
                    <b style={{color: isAbove ? "var(--up)" : "var(--down)"}}>
                      {isAbove ? "Bull regime (50 above 200)" : "Bear regime (50 below 200)"}
                    </b>
                    {lastCross && <span style={{color: "var(--fg-3)", marginLeft: 6}}>
                      · last {lastCross.type} cross {lastCross.daysAgo}d ago
                    </span>}
                  </span>
                );
              })()}
              <span className="item"><span className="swatch" style={{background: chartColors.accent}}></span>Current price</span>
              {liveEarnings.past?.length > 0 && (
                <span className="item"><span className="swatch dot" style={{background: chartColors.warn}}></span>Past earnings</span>
              )}
              {liveEarnings.next && (
                <span className="item earn-next-legend"><span className="swatch dot" style={{background: chartColors.warn, border: "1.5px solid white"}}></span>Next earnings</span>
              )}
            </div>
            {/* Price stats grid — fills the empty space below the chart
                with useful at-a-glance context: 52w high/low, distances,
                MA distances, RSI14, ATR14, and average daily range. */}
            {(() => {
              if (!daily || daily.length < 20) return null;
              const last = daily[daily.length - 1];
              if (!last || last.close == null) return null;
              const lastClose = last.close;
              // 52-week window
              const winStart = Math.max(0, daily.length - 252);
              const win = daily.slice(winStart);
              let hi = -Infinity, hiIdx = winStart, lo = Infinity, loIdx = winStart;
              win.forEach((d, i) => {
                if (d.high != null && d.high > hi) { hi = d.high; hiIdx = winStart + i; }
                if (d.low != null && d.low < lo) { lo = d.low; loIdx = winStart + i; }
              });
              const hiDate = new Date(daily[hiIdx]?.date);
              const loDate = new Date(daily[loIdx]?.date);
              const fmtDate = (d) => Number.isNaN(d?.getTime()) ? "—" : d.toLocaleDateString("en-US", {month: "short", day: "numeric"});
              // RSI14 — Wilder's smoothing, standard
              const closes = daily.map(d => d.close).filter(c => c != null);
              const rsi = (() => {
                const period = 14;
                if (closes.length < period + 1) return null;
                let gains = 0, losses = 0;
                for (let i = 1; i <= period; i++) {
                  const ch = closes[i] - closes[i - 1];
                  if (ch >= 0) gains += ch; else losses -= ch;
                }
                let avgGain = gains / period;
                let avgLoss = losses / period;
                for (let i = period + 1; i < closes.length; i++) {
                  const ch = closes[i] - closes[i - 1];
                  const g = ch > 0 ? ch : 0;
                  const l = ch < 0 ? -ch : 0;
                  avgGain = (avgGain * (period - 1) + g) / period;
                  avgLoss = (avgLoss * (period - 1) + l) / period;
                }
                if (avgLoss === 0) return 100;
                const rs = avgGain / avgLoss;
                return 100 - (100 / (1 + rs));
              })();
              // ATR14 (Wilder)
              const atr = (() => {
                const period = 14;
                if (daily.length < period + 1) return null;
                const trs = [];
                for (let i = 1; i < daily.length; i++) {
                  const h = daily[i].high, l = daily[i].low, pc = daily[i - 1].close;
                  if (h == null || l == null || pc == null) continue;
                  trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
                }
                if (trs.length < period) return null;
                let atrVal = trs.slice(0, period).reduce((a, b) => a + b, 0) / period;
                for (let i = period; i < trs.length; i++) {
                  atrVal = (atrVal * (period - 1) + trs[i]) / period;
                }
                return atrVal;
              })();
              // Avg daily range %
              const adr = (() => {
                const win = daily.slice(-20);
                const ranges = win.map(d => (d.high != null && d.low != null && d.close != null && d.close > 0) ? ((d.high - d.low) / d.close) * 100 : null).filter(v => v != null);
                if (!ranges.length) return null;
                return ranges.reduce((a, b) => a + b, 0) / ranges.length;
              })();
              // MA distances
              const distMA = (val) => val != null && val > 0 ? ((lastClose - val) / val) * 100 : null;
              const distMA50 = distMA(last.ma50);
              const distMA200 = distMA(last.ma200);
              const distEMA21 = distMA(last.ema21);
              const dist52H = hi > 0 ? ((lastClose - hi) / hi) * 100 : null;
              const dist52L = lo > 0 ? ((lastClose - lo) / lo) * 100 : null;
              const fmt$ = (v) => v == null ? "—" : "$" + v.toFixed(2);
              const cls = (v, threshold = 0) => v == null ? "" : v >= threshold ? "up" : "down";
              const rsiCls = rsi == null ? "" : rsi >= 70 ? "down" : rsi <= 30 ? "up" : "";
              return (
                <div className="chart-stats-grid" title="Price action context for the active ticker. Helps gauge where price sits in its recent range and how stretched it is from key averages.">
                  <div className="chart-stat" title="Highest price in the last 252 trading days (~1 year)">
                    <div className="chart-stat-lbl">52W HIGH</div>
                    <div className="chart-stat-val">{fmt$(hi)}</div>
                    <div className={`chart-stat-sub ${cls(dist52H)}`}>{fmtPct(dist52H)} · {fmtDate(hiDate)}</div>
                  </div>
                  <div className="chart-stat" title="Lowest price in the last 252 trading days (~1 year)">
                    <div className="chart-stat-lbl">52W LOW</div>
                    <div className="chart-stat-val">{fmt$(lo)}</div>
                    <div className={`chart-stat-sub ${cls(dist52L)}`}>{fmtPct(dist52L)} · {fmtDate(loDate)}</div>
                  </div>
                  <div className="chart-stat" title="EMA21 value and price distance from it. Above = uptrend, below = downtrend.">
                    <div className="chart-stat-lbl">EMA21</div>
                    <div className="chart-stat-val">{fmt$(last.ema21)}</div>
                    <div className={`chart-stat-sub ${cls(distEMA21)}`}>{fmtPct(distEMA21)}</div>
                  </div>
                  <div className="chart-stat" title="MA50 value and price distance. A common short-term trend reference.">
                    <div className="chart-stat-lbl">MA50</div>
                    <div className="chart-stat-val">{fmt$(last.ma50)}</div>
                    <div className={`chart-stat-sub ${cls(distMA50)}`}>{fmtPct(distMA50)}</div>
                  </div>
                  <div className="chart-stat" title="MA200 value and price distance. The classic long-term trend filter — above = bull, below = bear.">
                    <div className="chart-stat-lbl">MA200</div>
                    <div className="chart-stat-val">{fmt$(last.ma200)}</div>
                    <div className={`chart-stat-sub ${cls(distMA200)}`}>{fmtPct(distMA200)}</div>
                  </div>
                  <div className="chart-stat" title="14-day Relative Strength Index. Below 30 = oversold (green), above 70 = overbought (red).">
                    <div className="chart-stat-lbl">RSI14</div>
                    <div className={`chart-stat-val ${rsiCls}`}>{rsi == null ? "—" : rsi.toFixed(1)}</div>
                    <div className="chart-stat-sub">
                      {rsi == null ? "" : rsi >= 70 ? "overbought" : rsi <= 30 ? "oversold" : "neutral"}
                    </div>
                  </div>
                  <div className="chart-stat" title="14-day Average True Range — typical daily price movement in dollars">
                    <div className="chart-stat-lbl">ATR14</div>
                    <div className="chart-stat-val">{fmt$(atr)}</div>
                    <div className="chart-stat-sub">{atr != null && lastClose > 0 ? ((atr / lastClose) * 100).toFixed(2) + "% of price" : ""}</div>
                  </div>
                  <div className="chart-stat" title="Average daily range as a percentage of close, last 20 days. Useful for sizing same-day trades.">
                    <div className="chart-stat-lbl">ADR 20D</div>
                    <div className="chart-stat-val">{adr == null ? "—" : adr.toFixed(2) + "%"}</div>
                    <div className="chart-stat-sub">avg high-low range</div>
                  </div>
                  <div className="chart-stat"
                       title={ivRankInfo?.iv_rank != null
                         ? `IV Rank from ${ivRankInfo.iv_rank_days} days of local history. Current ATM IV30 is at the ${ivRankInfo.iv_rank.toFixed(0)}% mark of its observed range. Above 70 = rich premium territory, below 30 = cheap. IV Pct ${ivRankInfo.iv_pct != null ? ivRankInfo.iv_pct.toFixed(0) + "%" : "n/a"} of days had lower IV.`
                         : (ivRankInfo?.iv_rank_days > 0
                             ? `Building IV history. ${ivRankInfo.iv_rank_days} of 20 days collected before rank can be computed.`
                             : "Loading IV history…")}>
                    <div className="chart-stat-lbl">IV RANK</div>
                    <div className={`chart-stat-val ${ivRankInfo?.iv_rank == null ? "" : ivRankInfo.iv_rank >= 70 ? "up" : ivRankInfo.iv_rank <= 30 ? "down" : ""}`}>
                      {ivRankInfo?.iv_rank == null ? "—" : ivRankInfo.iv_rank.toFixed(0)}
                    </div>
                    <div className="chart-stat-sub">
                      {ivRankInfo?.iv_rank == null
                        ? (ivRankInfo?.iv_rank_days > 0 ? `${ivRankInfo.iv_rank_days}/20 days` : "loading")
                        : ivRankInfo.iv_rank >= 70 ? "premium rich"
                        : ivRankInfo.iv_rank <= 30 ? "premium cheap"
                        : "neutral"}
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* Expected Move — nested inside the chart card to fill the
                dead space below the 8-stat grid and align the chart card
                height with the right column. Same data pipeline as v105:
                FRONT_DTE, atmCall, atmPut, atmCallMid, atmPutMid,
                expectedDollarMove, ivMove, currentPrice, activeExpDate.
                No outer .card wrapper since we're already inside .chart-card. */}
            <div className="expected-move-card chart-em-section">
              <div className="card-head">
                <div>
                  <div className="kicker">ATM straddle · for {activeExpDate.toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"})}</div>
                  <div className="card-title">Expected Move</div>
                </div>
                <div className="muted" style={{fontSize: 11}}>
                  {atmCall && atmPut ? (
                    <>
                      ATM strike ${atmCall.strike?.toFixed(2)} · call ${atmCallMid.toFixed(2)} · put ${atmPutMid.toFixed(2)}
                    </>
                  ) : "Waiting for chain…"}
                </div>
              </div>
              <div className="em-stats-grid">
                <div className="em-stat" title="Days remaining until the selected expiration. Time decay accelerates as this number drops, especially in the final week.">
                  <div className="em-stat-lbl">DTE</div>
                  <div className="em-stat-val">{FRONT_DTE}d</div>
                </div>
                <div className="em-stat" title="Expected dollar move from now to expiration as priced by the options market. Calculated as ATM call mid + ATM put mid (the straddle price). About 68% of the time the stock should stay within ± this amount, per Black-Scholes assumptions.">
                  <div className="em-stat-lbl">Expected $</div>
                  <div className="em-stat-val">±${expectedDollarMove.toFixed(2)}</div>
                </div>
                <div className="em-stat" title="Expected move as a percentage of current stock price. Quick read on volatility for this expiration: 1-2% is calm, 3-5% is active, 6%+ usually means earnings or big catalyst within the expiration window.">
                  <div className="em-stat-lbl">Expected %</div>
                  <div className="em-stat-val">±{ivMove.toFixed(2)}%</div>
                </div>
                <div className="em-stat" title="Upper bound of the expected range = current price + expected move. Implied 1-sigma upside the market is pricing in for this expiration. Stocks closing above this on expiration are in the upper tail (~16% of cases).">
                  <div className="em-stat-lbl">Up to</div>
                  <div className="em-stat-val up">${(currentPrice + expectedDollarMove).toFixed(2)}</div>
                </div>
                <div className="em-stat" title="Lower bound of the expected range = current price - expected move. Implied 1-sigma downside the market is pricing in for this expiration. Stocks closing below this on expiration are in the lower tail (~16% of cases).">
                  <div className="em-stat-lbl">Down to</div>
                  <div className="em-stat-val down">${(currentPrice - expectedDollarMove).toFixed(2)}</div>
                </div>
                <div className="em-stat" title="Total ATM straddle price (call mid + put mid). This is the all-in cost to buy a long straddle at the money for this expiration, and the maximum credit you'd collect from selling a short straddle. Equals the expected dollar move.">
                  <div className="em-stat-lbl">Straddle</div>
                  <div className="em-stat-val">${expectedDollarMove.toFixed(2)}</div>
                </div>
              </div>
            </div>
          </div>

          <div className="card col-list" style={{gap: 16, position: "relative"}}>
            <div className="strikes-head-row">
              <div>
                <div className="kicker">This week's setup</div>
                <div className="card-title">Suggested strikes</div>
              </div>
              <div className="strikes-head-right">
                {liveEarnings && liveEarnings.next && (() => {
                  const dt = new Date(liveEarnings.next + "T16:00:00");
                  const today = new Date(); today.setHours(0,0,0,0);
                  const days = Math.round((dt - today) / 86400000);
                  const dateStr = dt.toLocaleDateString("en-US", {month: "short", day: "numeric"});
                  const tone = days <= 7 ? "warn-strong" : days <= 14 ? "warn" : "soft";
                  const label = days < 0 ? `Past · ${dateStr}`
                              : days === 0 ? `Today · ${dateStr}`
                              : days <= 7 ? `This week · ${dateStr}`
                              : days <= 14 ? `Next week · ${dateStr}`
                              : `${dateStr} · ${days}d`;
                  return (
                    <div className={`earn-badge earn-${tone}`}>
                      <span className="earn-dot">⏵</span>
                      <span className="earn-cap">EARN</span>
                      <span className="earn-when">{label}</span>
                    </div>
                  );
                })()}
                <div className="strategy-mode-toggle"
                     title="Focus the dashboard on one side of the trade. BOTH shows everything (default). CC hides the cash-secured put side. CSP hides the covered call side. Affects the timing verdict, the strike cards, and the analyst warning columns. Persists across reloads.">
                  <div className="strategy-mode-kicker">Focus</div>
                  <div className="strategy-mode-pills">
                    <button
                      className={`strategy-mode-pill ${strategyMode === "both" ? "active" : ""}`}
                      onClick={() => setStrategyMode("both")}
                      title="Show both covered call and cash-secured put content side by side. Default mode.">
                      Both
                    </button>
                    <button
                      className={`strategy-mode-pill ${strategyMode === "cc" ? "active" : ""}`}
                      onClick={() => setStrategyMode("cc")}
                      title="Focus on covered calls only. Hides the CSP timing verdict, put strike card, and CSP analyst warnings.">
                      CC
                    </button>
                    <button
                      className={`strategy-mode-pill ${strategyMode === "csp" ? "active" : ""}`}
                      onClick={() => setStrategyMode("csp")}
                      title="Focus on cash-secured puts only. Hides the CC timing verdict, call strike card, and CC analyst warnings.">
                      CSP
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <div className={`strike-pair ${strategyMode !== "both" ? "single" : ""}`}>
              {(() => {
                // Build helper bound to current strikeFlow data. Match
                // strikes within 0.5% so we don't miss when UW has
                // 250.00 and our suggestion is 250.0001.
                const flowFor = (side, strike) => {
                  if (!strikeFlow || !strikeFlow.length || !strike) return null;
                  const tol = Math.max(0.05, strike * 0.005);
                  const matches = strikeFlow.filter(r =>
                    r.side === side && Math.abs(r.strike - strike) <= tol
                  );
                  if (!matches.length) return null;
                  // Sum across expirations at this strike
                  return matches.reduce((acc, r) => ({
                    volume: acc.volume + (r.volume || 0),
                    premium: acc.premium + (r.premium || 0),
                    ask_premium: acc.ask_premium + (r.ask_premium || 0),
                    sweep_count: acc.sweep_count + (r.sweep_count || 0),
                    trade_count: acc.trade_count + (r.trade_count || 0),
                    vol_oi_max: Math.max(acc.vol_oi_max, r.vol_oi_max || 0),
                    open_interest: r.open_interest ?? acc.open_interest,
                  }), {volume: 0, premium: 0, ask_premium: 0, sweep_count: 0, trade_count: 0, vol_oi_max: 0, open_interest: null});
                };
                const callFlow = flowFor("call", sugCall);
                const putFlow = flowFor("put", sugPut);
                return <>
                  {(strategyMode === "both" || strategyMode === "cc") && (
                  <div className={`strike-card call ${manualCallStrike != null ? "manual" : ""}`}>
                    <div className="label"><span className="dot"></span>SELL CALL{manualCallStrike != null && <span className="manual-tag">manual</span>}</div>
                    <div className="price-big"><small>$</small>{sugCall.toFixed(2)}</div>
                    <div className="meta">
                      <span><Term k="premium">Premium</Term> <b>${callAtSug.bid.toFixed(2)}</b></span>
                      <span><Term k="delta">Δ</Term> <b>{callAtSug.delta.toFixed(2)}</b></span>
                    </div>
                    <div className="meta">
                      <span><Term k="oi">OI</Term> <b>{callAtSug.openInterest.toLocaleString()}</b></span>
                      <span><Term k="iv">IV</Term> <b>{(callAtSug.iv * 100).toFixed(1)}%</b></span>
                    </div>
                    {uwHealth?.connected && callFlow && (
                      <div className="strike-flow-row" title={`Unusual Whales activity at $${sugCall.toFixed(2)} call today: ${callFlow.trade_count} unusual trades. Ask-side premium = $${callFlow.ask_premium.toFixed(0)}. Volume / OI max = ${callFlow.vol_oi_max.toFixed(2)}. ${callFlow.sweep_count} sweep(s).`}>
                        <span className="strike-flow-lbl" title="Today's volume in unusual trades at this strike">UW Vol</span>
                        <b>{callFlow.volume.toLocaleString()}</b>
                        <span className="strike-flow-lbl" title="Ask-side premium today at this strike — high = aggressive call buyers, dangerous for covered-call writers">Ask$</span>
                        <b className={callFlow.ask_premium > callFlow.premium * 0.6 ? "warn" : ""}>{fmt$M(callFlow.ask_premium)}</b>
                        {callFlow.sweep_count > 0 && (
                          <span className="strike-flow-sweep" title={`${callFlow.sweep_count} sweep(s) detected at this strike — institutional aggression`}>
                            {callFlow.sweep_count}× S
                          </span>
                        )}
                        {callFlow.vol_oi_max > 1 && (
                          <span className="strike-flow-vol-oi" title="Volume exceeded open interest in at least one trade — opening flow, new positions">
                            V&gt;OI
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  )}
                  {(strategyMode === "both" || strategyMode === "csp") && (
                  <div className={`strike-card put ${manualPutStrike != null ? "manual" : ""}`}>
                    <div className="label"><span className="dot"></span>SELL PUT{manualPutStrike != null && <span className="manual-tag">manual</span>}</div>
                    <div className="price-big"><small>$</small>{sugPut.toFixed(2)}</div>
                    <div className="meta">
                      <span><Term k="premium">Premium</Term> <b>${putAtSug.bid.toFixed(2)}</b></span>
                      <span><Term k="delta">Δ</Term> <b>{putAtSug.delta.toFixed(2)}</b></span>
                    </div>
                    <div className="meta">
                      <span><Term k="oi">OI</Term> <b>{putAtSug.openInterest.toLocaleString()}</b></span>
                      <span><Term k="iv">IV</Term> <b>{(putAtSug.iv * 100).toFixed(1)}%</b></span>
                    </div>
                    {uwHealth?.connected && putFlow && (
                      <div className="strike-flow-row" title={`Unusual Whales activity at $${sugPut.toFixed(2)} put today: ${putFlow.trade_count} unusual trades. Ask-side premium = $${putFlow.ask_premium.toFixed(0)}.`}>
                        <span className="strike-flow-lbl" title="Today's volume in unusual trades at this strike">UW Vol</span>
                        <b>{putFlow.volume.toLocaleString()}</b>
                        <span className="strike-flow-lbl" title="Ask-side premium today — high = aggressive put buyers, downside protection in demand">Ask$</span>
                        <b className={putFlow.ask_premium > putFlow.premium * 0.6 ? "warn" : ""}>{fmt$M(putFlow.ask_premium)}</b>
                        {putFlow.sweep_count > 0 && (
                          <span className="strike-flow-sweep" title={`${putFlow.sweep_count} sweep(s) detected at this strike`}>
                            {putFlow.sweep_count}× S
                          </span>
                        )}
                        {putFlow.vol_oi_max > 1 && (
                          <span className="strike-flow-vol-oi" title="Volume exceeded open interest in at least one trade — opening flow">
                            V&gt;OI
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  )}
                </>;
              })()}
            </div>

            {/* ── Decision Engine ─────────────────────────────────────
             * Catalyst badge + POP/POT for the suggested call + three
             * alternative strike picks (Income / Safety / Balanced).
             * Read-only computation from the existing chain; no extra
             * API calls.
             */}
            {(() => {
              // ── Catalyst check: does earnings fall inside expiration?
              const earnDateStr = current?.earningsDate;
              let earnInside = false;
              let earnLabel = "Clean";
              let earnCls = "cat-clean";
              if (earnDateStr) {
                try {
                  const d = new Date(earnDateStr + "T12:00:00");
                  if (!isNaN(d) && d <= activeExpDate) {
                    earnInside = true;
                    earnLabel = `Earnings ${d.toLocaleDateString("en-US", {month: "short", day: "numeric"})} inside exp · CAUTION`;
                    earnCls = "cat-caution";
                  } else if (!isNaN(d)) {
                    const days = Math.round((d - new Date()) / 86400000);
                    earnLabel = `Earnings in ${days}d · clean for this exp`;
                  }
                } catch {}
              }

              // ── POP / POT for the suggested call.
              // POP (probability OTM at exp) ≈ 1 - |delta|, market-standard
              // approximation. POT (probability of touch before exp) ≈ 2×|delta|
              // for short OTM options where |delta| < 0.5.
              const aDelta = Math.abs(callAtSug.delta || 0);
              const popPct = aDelta > 0 ? Math.max(0, Math.min(100, (1 - aDelta) * 100)) : null;
              const potPct = aDelta > 0 && aDelta < 0.5 ? Math.min(100, 2 * aDelta * 100) : null;

              // ── Three strike picks across the OTM call chain.
              // Income: highest premium yield (mid / spot * 100) with min liquidity
              // Safety: lowest |delta| with non-trivial premium (≥ 0.05)
              // Balanced: existing callAtSug
              const otmCalls = (calls || []).filter(c =>
                c && c.strike > currentPrice
                && c.delta != null && c.delta > 0
                && c.bid != null && c.ask != null
                && c.bid > 0
                && c.openInterest > 10
              );
              const callMidOf = c => {
                const b = c.bid || 0, a = c.ask || 0;
                return (b > 0 && a > 0) ? (b + a) / 2 : (b || a);
              };
              let pickIncome = null, pickSafety = null;
              if (otmCalls.length > 0 && currentPrice > 0) {
                const withYield = otmCalls
                  .filter(c => callMidOf(c) >= 0.05)
                  .map(c => ({
                    ...c,
                    _mid: callMidOf(c),
                    _yieldPct: (callMidOf(c) / currentPrice) * 100,
                  }));
                if (withYield.length > 0) {
                  pickIncome = withYield.reduce((best, c) =>
                    c._yieldPct > (best?._yieldPct || 0) ? c : best, null);
                  pickSafety = withYield.reduce((best, c) => {
                    if (!best) return c;
                    return Math.abs(c.delta) < Math.abs(best.delta) ? c : best;
                  }, null);
                }
              }
              const balancedMid = callMidOf(callAtSug);
              const pickBalanced = callAtSug.strike != null ? {
                strike: callAtSug.strike,
                delta: callAtSug.delta,
                _mid: balancedMid,
                _yieldPct: currentPrice > 0 ? (balancedMid / currentPrice) * 100 : 0,
                openInterest: callAtSug.openInterest,
              } : null;

              const formatPick = (label, pick, popOverride) => {
                if (!pick || pick.strike == null) return null;
                const aD = Math.abs(pick.delta || 0);
                const pop = aD > 0 ? Math.max(0, Math.min(100, (1 - aD) * 100)) : null;
                return (
                  <div className="pick-card" key={label}>
                    <div className="pick-label">{label}</div>
                    <div className="pick-strike">${pick.strike.toFixed(2)}</div>
                    <div className="pick-row">
                      <span>Mid</span><b>${(pick._mid || 0).toFixed(2)}</b>
                    </div>
                    <div className="pick-row">
                      <span>Yield</span><b>{(pick._yieldPct || 0).toFixed(2)}%</b>
                    </div>
                    <div className="pick-row">
                      <span>Δ</span><b>{(pick.delta || 0).toFixed(2)}</b>
                    </div>
                    <div className="pick-row">
                      <span>POP</span><b>{pop != null ? pop.toFixed(0) + "%" : "—"}</b>
                    </div>
                  </div>
                );
              };

              return (
                <div className="decision-engine">
                  <div className="decision-row">
                    <div className={`catalyst-badge ${earnCls}`} title="Earnings inside the active expiration adds risk of large gap moves and IV crush">
                      {earnLabel}
                    </div>
                    <div className="pop-pot">
                      <span className="pp-pair" title="Probability the short call expires OUT-of-the-money. Higher = safer. Approx 1 - |delta|.">
                        <span className="pp-key">POP</span>
                        <span className="pp-val">{popPct != null ? popPct.toFixed(0) + "%" : "—"}</span>
                      </span>
                      <span className="pp-pair" title="Probability price TOUCHES the strike at any point before expiration. Lower = safer. Approx 2 × |delta|.">
                        <span className="pp-key">POT</span>
                        <span className="pp-val">{potPct != null ? potPct.toFixed(0) + "%" : "—"}</span>
                      </span>
                    </div>
                  </div>
                  <div className="picks-row">
                    {formatPick("Best income", pickIncome)}
                    {formatPick("Best balanced", pickBalanced)}
                    {formatPick("Best safety", pickSafety)}
                  </div>
                </div>
              );
            })()}

            <div className="metric-grid" style={{gridTemplateColumns: "repeat(2, 1fr)"}}>
              <div className="metric compact">
                <div className="lbl">
                  <Term k={baseline === "monday" ? "monday_open" : "prev_friday_close"}>
                    {baseline === "monday" ? "Monday Open" : "Prev Fri Close"}
                  </Term>
                </div>
                <div className="val">${baselinePrice.toFixed(2)}</div>
              </div>
              <div className="metric compact">
                <div className="lbl">Current</div>
                <div className="val">${currentPrice.toFixed(2)}
                  <span className="delta-tiny" style={{color: stockDelta >= 0 ? "var(--up)" : "var(--down)"}}>{fmtPct(stockDeltaPct, 2)}</span>
                </div>
              </div>
              <div className="metric compact">
                <div className="lbl"><Term k="expected_high">Expected high</Term></div>
                <div className="val" style={{color: "var(--up)"}}>${expHigh.toFixed(2)}</div>
              </div>
              <div className="metric compact">
                <div className="lbl"><Term k="expected_low">Expected low</Term></div>
                <div className="val" style={{color: "var(--down)"}}>${expLow.toFixed(2)}</div>
              </div>
            </div>

            <RecommendationPair rec={rec} strategyMode={strategyMode} />
          </div>
        </div>
        </TabPanel>

        {/* Positions tracker — re-enabled in v109. Tracks open + closed
            short option positions in localStorage with live valuation
            against the current chain (or BS estimate when chain misses).
            Manual entry only; broker import is a future ship. */}
        <TabPanel tab="manage" active={activeTab}>
        <div id="jump-positions" className="jump-anchor" aria-hidden="true"></div>
        <PositionsCard
          positions={positions}
          setPositions={setPositions}
          showAdd={showAddPosition}
          setShowAdd={setShowAddPosition}
          filter={positionFilter}
          setFilter={setPositionFilter}
          ticker={ticker}
          currentPrice={currentPrice}
          calls={calls}
          puts={puts}
          activeExpDate={activeExpDate}
          sugCall={sugCall}
          sugPut={sugPut}
          callAtSug={callAtSug}
          putAtSug={putAtSug}
          FRONT_DTE={FRONT_DTE}
          Term={Term}
          fmt$={fmt$}
          apiFetch={apiFetch}
        />

        {/* Win rate tracker (v1.15) — realized P/L stats from the trade
            journal. Auto-refreshes when a position is closed via the
            jerry:position-closed custom event. */}
        <div id="jump-winrate" className="jump-anchor" aria-hidden="true"></div>
        <WinRateCard apiFetch={apiFetch} />

        {/* Push notifications config (v1.16) — Pushover status, test
            button, and setup help. Collapsed by default. */}
        <PushSettingsCard apiFetch={apiFetch} />

        {/* Broker import (v1.17 phase 1) — manual import of Schwab
            positions into the local tracker. Read-only fetch, user
            confirms each import or imports all new at once. */}
        <BrokerImportCard apiFetch={apiFetch}
                          positions={positions}
                          setPositions={setPositions} />
        </TabPanel>

        {/* Earnings vol crush (v1.15) — heuristic post-earnings IV crush
            for watchlist tickers with earnings inside 14 days. Auto-hides
            when no upcoming earnings on the watchlist. */}
        <TabPanel tab="flow" active={activeTab}>
        <EarningsCrushCard
          apiFetch={apiFetch}
          onSwitchTicker={switchTicker}
        />
        </TabPanel>

        {/* Median statistics */}
        <TabPanel tab="analyze" active={activeTab}>
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Behaviour summary · last {weeks} weeks</div>
              <div className="card-title">How {ticker} typically moves in a week</div>
            </div>
          </div>
          <div className="metric-grid five">
            <div className="metric">
              <div className="lbl"><Term k="median_high">Median high</Term></div>
              <div className="val" style={{color: "var(--up)"}}>{fmtPct(medianHigh)}</div>
              <div className="muted" style={{fontSize: 11, marginTop: 2}}>typical rise</div>
            </div>
            <div className="metric">
              <div className="lbl"><Term k="median_low">Median low</Term></div>
              <div className="val" style={{color: "var(--down)"}}>{fmtPct(medianLow)}</div>
              <div className="muted" style={{fontSize: 11, marginTop: 2}}>typical drop</div>
            </div>
            <div className="metric">
              <div className="lbl"><Term k="median_close">Median close</Term></div>
              <div className="val">{fmtPct(medianClose)}</div>
              <div className="muted" style={{fontSize: 11, marginTop: 2}}>where Fri lands</div>
            </div>
            <div className="metric">
              <div className="lbl"><Term k="typical_high_day">Typical high day</Term></div>
              <div className="val" style={{color: "var(--up)"}}>{typicalHighDay}</div>
              <div className="muted" style={{fontSize: 11, marginTop: 2}}>most common peak</div>
            </div>
            <div className="metric">
              <div className="lbl"><Term k="typical_low_day">Typical low day</Term></div>
              <div className="val" style={{color: "var(--down)"}}>{typicalLowDay}</div>
              <div className="muted" style={{fontSize: 11, marginTop: 2}}>most common trough</div>
            </div>
          </div>
        </div>

        {/* Two charts row */}
        <div className={`row ${layout === "swapped" ? "split-1-2" : "split-2-1"}`}>
          <div className="card">
            <div className="card-head">
              <div>
                <div className="kicker">Weekly returns history</div>
                <div className="card-title">Open, high, low, close · last {weeks} weeks</div>
              </div>
              <div className="legend">
                <span className="item"><span className="swatch" style={{background: chartColors.up}}></span>High</span>
                <span className="item"><span className="swatch" style={{background: chartColors.down}}></span>Low</span>
                <span className="item"><span className="swatch open-tick" title="Open marker — short tick on the left side of each bar"></span>Open</span>
                <span className="item"><span className="swatch ring"></span>Close</span>
                <span className="item"><span className="swatch" style={{background: chartColors.warn}}></span>This week</span>
                {liveEarnings.past?.length > 0 && (
                  <span className="item"><span className="swatch dot" style={{background: chartColors.warn}}></span>Earnings week</span>
                )}
              </div>
            </div>
            <ReturnsChart rows={rows} medianHigh={medianHigh} medianLow={medianLow}
                          medianClose={medianClose} currentReturn={currReturn} colors={chartColors}
                          earnings={liveEarnings} />
          </div>
          <div className="card">
            <div className="card-head">
              <div>
                <div className="kicker">Day of week</div>
                <div className="card-title">When highs and lows land</div>
              </div>
            </div>
            <div className="dow-seclabel dow-seclabel--up">High</div>
            <DayBarChart rows={rows} colors={chartColors} mode="high" />
            <div className="dow-seclabel dow-seclabel--down" style={{marginTop: 14}}>Low</div>
            <DayBarChart rows={rows} colors={chartColors} mode="low" />
            {(() => {
              if (!rows.length) return null;
              const days = ["Mon", "Tue", "Wed", "Thu", "Fri"];
              const fmt = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
              // Per-day max high and min low across the lookback. Each
              // row carries a `day_breakdown` dict { "Mon": {high, low}, ... }
              // capturing every weekday's intraday excursion vs prior close.
              // Aggregate across all rows: max of highs, min of lows per day.
              const extremes = days.map(d => {
                const highs = [];
                const lows = [];
                for (const r of rows) {
                  const db = r.day_breakdown && r.day_breakdown[d];
                  if (!db) continue;
                  if (db.high != null && isFinite(db.high)) highs.push(db.high);
                  if (db.low  != null && isFinite(db.low))  lows.push(db.low);
                }
                return {
                  day: d,
                  bestHigh: highs.length ? Math.max(...highs) : null,
                  worstLow: lows.length  ? Math.min(...lows)  : null,
                };
              });
              // Today's weekday short name (Mon..Fri) for column highlight.
              const _todayShort = (() => {
                try {
                  const fmt = new Intl.DateTimeFormat("en-US", {
                    timeZone: "America/New_York", weekday: "short",
                  });
                  return fmt.format(new Date());
                } catch { return ""; }
              })();
              return (
                <div className="dow-extremes">
                  <div className="dow-extremes-head">
                    <span className="dow-extremes-corner"></span>
                    {days.map(d => (
                      <span key={d}
                            className={`dow-extremes-daycol${d === _todayShort ? " today" : ""}`}>{d}</span>
                    ))}
                  </div>
                  <div className="dow-extremes-row">
                    <span className="dow-extremes-label up">Best high</span>
                    {extremes.map(e => (
                      <span key={e.day}
                            className={`dow-extremes-cell up${e.day === _todayShort ? " today" : ""}`}>{fmt(e.bestHigh)}</span>
                    ))}
                  </div>
                  <div className="dow-extremes-row">
                    <span className="dow-extremes-label down">Worst low</span>
                    {extremes.map(e => (
                      <span key={e.day}
                            className={`dow-extremes-cell down${e.day === _todayShort ? " today" : ""}`}>{fmt(e.worstLow)}</span>
                    ))}
                  </div>
                </div>
              );
            })()}
            <div className="dow-footer">
              <span className="dow-footer-chip up">Highs cluster <b>{typicalHighDay}</b></span>
              <span className="dow-footer-dot">·</span>
              <span className="dow-footer-chip down">Lows cluster <b>{typicalLowDay}</b></span>
            </div>
          </div>
        </div>

        {/* Mean reversion · today's basing levels — ALWAYS FIRST */}
        <div id="jump-basing" className="jump-anchor" aria-hidden="true"></div>
        <BasingCard ticker={ticker} weeks={weeks} apiFetch={apiFetch} livePrice={currentPrice} />
        </TabPanel>

        {/* UW Flow Score — decision-engine signal from real-time options flow */}
        <TabPanel tab="flow" active={activeTab}>
        <div id="jump-flow" className="jump-anchor" aria-hidden="true"></div>
        <FlowScoreCard ticker={ticker} currentPrice={getLivePrice(ticker) ?? currentPrice} apiFetch={apiFetch} uwHealth={uwHealth} />
        </TabPanel>

        {/* Analyst price targets, ratings, and catalyst signals */}
        <TabPanel tab="analyze" active={activeTab}>
        <div id="jump-analyst" className="jump-anchor" aria-hidden="true"></div>
        <AnalystCard ticker={ticker} currentPrice={getLivePrice(ticker) ?? currentPrice} apiFetch={apiFetch} onData={setAnalystData} strategyMode={strategyMode} />
        </TabPanel>

        {/* Trade Builder — recommendation engine that combines the
            already-picked 0.20-delta strikes with analyst overlay,
            recommendation severity, and earnings proximity to surface
            ONE specific actionable trade for each strategy (CC + CSP)
            with all the math worked out. */}
        <TabPanel tab="trade" active={activeTab}>
        <div id="jump-builder" className="jump-anchor" aria-hidden="true"></div>
        <TradeBuilderCard
          ticker={ticker}
          currentPrice={currentPrice}
          callAtSug={callAtSug}
          putAtSug={putAtSug}
          FRONT_DTE={FRONT_DTE}
          activeExpDate={activeExpDate}
          expHigh={expHigh}
          expLow={expLow}
          analystData={analystData}
          rec={rec}
          callSafePct={callSafePct}
          putSafePct={putSafePct}
          apiFetch={apiFetch}
          strategyMode={strategyMode}
        />

        {/* Level Reprice (v1.28) — intraday fade staging + level repricing */}
        <LevelRepriceCard
          ticker={ticker}
          currentPrice={currentPrice}
          calls={calls}
          puts={puts}
          sugCall={callAtSug?.strike}
          sugPut={putAtSug?.strike}
          expectedMove={expectedDollarMove}
          weeklyRows={rows}
          activeExpDate={activeExpDate}
          frontDte={FRONT_DTE}
          apiFetch={apiFetch}
          strategyMode={strategyMode}
          livePrice={getLivePrice(ticker) ?? currentPrice}
        />
        </TabPanel>
        <TabPanel tab="analyze" active={activeTab}>
        <PullbackProfileCard ticker={ticker} currentPrice={currentPrice} livePrice={getLivePrice(ticker) ?? currentPrice} apiFetch={apiFetch} />

        {/* IV vs Hist + Probabilities */}
        <div className="row two">
          <div className="card">
            <div className="card-head">
              <div>
                <div className="kicker">Implied vs historical · for {activeExpDate.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"})} ({FRONT_DTE}d)</div>
                <div className="card-title">Is premium expensive this week?</div>
              </div>
              {window.__LIVE && window.__LIVE.volRank != null && (() => {
                const rank = window.__LIVE.volRank;
                const pct = window.__LIVE.volPct;
                const tone = rank >= 67 ? "high" : rank <= 33 ? "low" : "mid";
                return (
                  <div className={`vol-rank-pill vr-${tone}`}>
                    <span className="vr-num">{rank.toFixed(0)}</span>
                    <div className="vr-meta">
                      <div className="vr-lbl">Vol rank</div>
                      <div className="vr-sub">{pct != null ? `${pct.toFixed(0)}th pct · 1y` : "1y range"}</div>
                    </div>
                  </div>
                );
              })()}
            </div>
            <div className="iv-cmp">
              <div className="iv-col">
                <div className="lbl"><Term k="expected_move">Market expected move</Term></div>
                <div className="val">±{ivMove.toFixed(2)}%</div>
                <div className="sub">±${expectedDollarMove.toFixed(2)} pts · ${(currentPrice - expectedDollarMove).toFixed(2)} to ${(currentPrice + expectedDollarMove).toFixed(2)}</div>
              </div>
              <div className="iv-col">
                <div className="lbl"><Term k="historical_range">Avg historical range</Term></div>
                <div className="val">{histMove.toFixed(2)}%</div>
                <div className="sub">±${(baselinePrice * histMove / 200).toFixed(2)} pts vs baseline</div>
              </div>
            </div>
            <div className="iv-verdict" style={{
              background: ivMove > histMove * 1.2 ? "color-mix(in oklch, var(--up), transparent 90%)" :
                          ivMove < histMove * 0.8 ? "color-mix(in oklch, var(--warn), transparent 88%)" :
                          "color-mix(in oklch, var(--accent), transparent 92%)",
              border: `1px solid ${ivMove > histMove * 1.2 ? "color-mix(in oklch, var(--up), transparent 70%)" :
                                    ivMove < histMove * 0.8 ? "color-mix(in oklch, var(--warn), transparent 70%)" :
                                    "color-mix(in oklch, var(--accent), transparent 70%)"}`
            }}>
              {ivMove > histMove * 1.2 ? <span><b style={{color: "var(--up)"}}>Premium is rich.</b> Options imply a {(ivMove / histMove * 100 - 100).toFixed(0)}% larger move than typical. Favorable for selling.</span>
                : ivMove < histMove * 0.8 ? <span><b style={{color: "var(--warn)"}}>Premium is cheap.</b> The market expects a calmer week. Selling premium pays less than usual.</span>
                : <span><b>In line with history.</b> Implied and historical moves are within 20% of each other.</span>}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div>
                <div className="kicker"><Term k="prob_profit">Probability of profit</Term></div>
                <div className="card-title">How often each side has expired worthless</div>
              </div>
              <div className="card-sub">last {weeks} weeks</div>
            </div>
            <div className="prob-row">
              <div className="label-col">
                <span className="dot" style={{background: "var(--up)"}}></span>
                <span><Term k="prob_call_safe">Stock stays <b>below ${sugCall.toFixed(2)}</b></Term></span>
              </div>
              <div className="bar-track">
                <div className="bar-fill" style={{width: `${callSafePct}%`, background: "var(--up)"}}></div>
              </div>
              <div className="pct" style={{color: "var(--up)"}}>{callSafePct.toFixed(0)}%</div>
            </div>
            <div className="prob-row">
              <div className="label-col">
                <span className="dot" style={{background: "var(--down)"}}></span>
                <span><Term k="prob_put_safe">Stock stays <b>above ${sugPut.toFixed(2)}</b></Term></span>
              </div>
              <div className="bar-track">
                <div className="bar-fill" style={{width: `${putSafePct}%`, background: "var(--down)"}}></div>
              </div>
              <div className="pct" style={{color: "var(--down)"}}>{putSafePct.toFixed(0)}%</div>
            </div>
            <div className="prob-row">
              <div className="label-col">
                <span className="dot" style={{background: "var(--accent)"}}></span>
                <span><Term k="prob_both"><b>Both</b> sides expire worthless</Term></span>
              </div>
              <div className="bar-track">
                <div className="bar-fill" style={{width: `${bothSafePct}%`, background: "var(--accent)"}}></div>
              </div>
              <div className="pct">{bothSafePct.toFixed(0)}%</div>
            </div>
            <div className="muted" style={{fontSize: 12, marginTop: 12, lineHeight: 1.5}}>
              Based on actual weekly highs and lows over the lookback window. Not implied probabilities.
            </div>
          </div>
        </div>

        {/* Volume / OI concentration for this expiration */}
        <div className="row two">
        {(() => {
          if (!calls.length && !puts.length) return null;
          const sumVol = arr => arr.reduce((a, b) => a + (b.volume || 0), 0);
          const sumOI = arr => arr.reduce((a, b) => a + (b.openInterest || 0), 0);
          const totalCallVol = sumVol(calls), totalPutVol = sumVol(puts);
          const totalCallOI = sumOI(calls), totalPutOI = sumOI(puts);
          const pcVol = totalCallVol > 0 ? totalPutVol / totalCallVol : null;
          const pcOI = totalCallOI > 0 ? totalPutOI / totalCallOI : null;
          const topBy = (arr, key, n = 5) =>
            [...arr].filter(x => (x[key] || 0) > 0).sort((a, b) => b[key] - a[key]).slice(0, n);
          const topCallVol = topBy(calls, "volume");
          const topPutVol = topBy(puts, "volume");
          const topCallOI = topBy(calls, "openInterest");
          const topPutOI = topBy(puts, "openInterest");

          // Max pain — strike where the total in-the-money value held by
          // option BUYERS is minimized, i.e. where buyers collectively
          // lose the most. A common (but contested) heuristic that price
          // tends to gravitate toward this level near expiration.
          const allStrikesMP = Array.from(new Set([
            ...calls.map(c => c.strike), ...puts.map(p => p.strike),
          ])).sort((a, b) => a - b);
          let maxPainStrike = null, minTotalValue = Infinity;
          if (allStrikesMP.length && (totalCallOI + totalPutOI) > 0) {
            for (const K of allStrikesMP) {
              let total = 0;
              for (const c of calls) {
                if (!c.openInterest) continue;
                total += Math.max(0, K - c.strike) * c.openInterest * 100;
              }
              for (const p of puts) {
                if (!p.openInterest) continue;
                total += Math.max(0, p.strike - K) * p.openInterest * 100;
              }
              if (total < minTotalValue) {
                minTotalValue = total;
                maxPainStrike = K;
              }
            }
          }
          const maxPainPct = maxPainStrike != null
            ? ((maxPainStrike - currentPrice) / currentPrice) * 100
            : null;

          const expDateLabel = activeExpDate.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"});
          const fmtN = n => n >= 10000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString();
          return (
            <div className="card" style={{marginBottom: "var(--row-gap)"}}>
              <div className="card-head">
                <div>
                  <div className="kicker">Open interest · volume · for {expDateLabel}</div>
                  <div className="card-title">Where the action is</div>
                </div>
                {maxPainStrike != null && (
                  <div className="max-pain-pill">
                    <span className="mp-lbl">Max pain</span>
                    <span className="mp-strike">${maxPainStrike.toFixed(2)}</span>
                    <span className={`mp-delta ${maxPainPct >= 0 ? "up" : "down"}`}>
                      {maxPainPct >= 0 ? "+" : ""}{maxPainPct.toFixed(1)}%
                    </span>
                  </div>
                )}
              </div>
              {(() => {
                // ── By-strike activity chart (v1.19) ──────────────────
                // Mirrored horizontal bars per strike: calls right (green),
                // puts left (red), centered on the strike axis. Shows where
                // volume or OI is concentrated relative to current price.
                // Metric toggled by oiChartMetric. Limited to strikes within
                // a sensible window around current price so far-OTM noise
                // does not flatten the bars.
                const metric = oiChartMetric;
                const metricKey = metric === "oi" ? "openInterest" : "volume";
                // Build a strike -> {call, put} map.
                const byStrike = new Map();
                for (const c of calls) {
                  if (c.strike == null) continue;
                  const v = c[metricKey] || 0;
                  if (v <= 0) continue;
                  const e = byStrike.get(c.strike) || {call: 0, put: 0};
                  e.call += v;
                  byStrike.set(c.strike, e);
                }
                for (const p of puts) {
                  if (p.strike == null) continue;
                  const v = p[metricKey] || 0;
                  if (v <= 0) continue;
                  const e = byStrike.get(p.strike) || {call: 0, put: 0};
                  e.put += v;
                  byStrike.set(p.strike, e);
                }
                if (byStrike.size === 0) {
                  return (
                    <div className="oi-chart-empty muted">
                      No {metric === "oi" ? "open interest" : "volume"} data for this expiration yet.
                    </div>
                  );
                }
                // Window: keep strikes within roughly ±15% of current price,
                // but always keep at least the 20 highest-activity strikes so
                // we never show an empty chart on a wide-strike name.
                let rows = Array.from(byStrike.entries())
                  .map(([strike, e]) => ({strike, call: e.call, put: e.put, total: e.call + e.put}));
                const within = rows.filter(r => Math.abs(r.strike - currentPrice) / currentPrice <= 0.15);
                const base = within.length >= 6 ? within
                  : rows.sort((a, b) => b.total - a.total).slice(0, 20);
                base.sort((a, b) => b.strike - a.strike); // high strike at top
                const maxSide = Math.max(
                  1, ...base.map(r => Math.max(r.call, r.put))
                );
                const fmtBar = n => n >= 10000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString();
                // Find the nearest strike to current price to mark it.
                let nearestStrike = null, nearestDist = Infinity;
                for (const r of base) {
                  const d = Math.abs(r.strike - currentPrice);
                  if (d < nearestDist) { nearestDist = d; nearestStrike = r.strike; }
                }
                return (
                  <div className="oi-chart-wrap">
                    <div className="oi-chart-controls">
                      <div className="oi-chart-legend">
                        <span className="oi-leg-item"><span className="oi-leg-dot" style={{background: "var(--up)"}}></span>Calls</span>
                        <span className="oi-leg-item"><span className="oi-leg-dot" style={{background: "var(--down)"}}></span>Puts</span>
                        <span className="oi-leg-note" title="The highlighted row is the strike nearest the current price.">· current ${currentPrice.toFixed(2)}</span>
                      </div>
                      <div className="oi-metric-toggle"
                           title="Switch between today's traded volume and total open interest. Volume shows where today's action is. OI shows where positioning has accumulated.">
                        <button className={`oi-metric-pill ${metric === "volume" ? "active" : ""}`}
                                onClick={() => setOiChartMetric("volume")}
                                title="Today's traded contracts per strike.">Volume</button>
                        <button className={`oi-metric-pill ${metric === "oi" ? "active" : ""}`}
                                onClick={() => setOiChartMetric("oi")}
                                title="Total open contracts per strike.">OI</button>
                      </div>
                    </div>
                    <div className="oi-chart">
                      {base.map(r => {
                        const callPct = (r.call / maxSide) * 100;
                        const putPct = (r.put / maxSide) * 100;
                        const isNear = r.strike === nearestStrike;
                        return (
                          <div key={r.strike} className={`oi-chart-row ${isNear ? "near" : ""}`}
                               title={`$${r.strike.toFixed(2)} · calls ${fmtBar(r.call)} · puts ${fmtBar(r.put)}`}>
                            <div className="oi-bar-side put">
                              {r.put > 0 && <span className="oi-bar-num">{fmtBar(r.put)}</span>}
                              <div className="oi-bar put-bar" style={{width: `${putPct}%`}}></div>
                            </div>
                            <div className="oi-bar-strike">${r.strike.toFixed(r.strike >= 100 ? 0 : 2)}</div>
                            <div className="oi-bar-side call">
                              <div className="oi-bar call-bar" style={{width: `${callPct}%`}}></div>
                              {r.call > 0 && <span className="oi-bar-num">{fmtBar(r.call)}</span>}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}
              <div className="oi-grid">
                <div className="oi-totals">
                  <div className="oi-total-row">
                    <span className="oi-label">Total <Term k="volume">volume</Term></span>
                    <span className="oi-pair">
                      <b style={{color: "var(--up)"}}>{fmtN(totalCallVol)}</b>
                      <span className="oi-sep">calls</span>
                      <b style={{color: "var(--down)"}}>{fmtN(totalPutVol)}</b>
                      <span className="oi-sep">puts</span>
                      {pcVol != null && <span className="oi-ratio">P/C {pcVol.toFixed(2)}</span>}
                    </span>
                  </div>
                  <div className="oi-total-row">
                    <span className="oi-label">Total <Term k="oi">OI</Term></span>
                    <span className="oi-pair">
                      <b style={{color: "var(--up)"}}>{fmtN(totalCallOI)}</b>
                      <span className="oi-sep">calls</span>
                      <b style={{color: "var(--down)"}}>{fmtN(totalPutOI)}</b>
                      <span className="oi-sep">puts</span>
                      {pcOI != null && <span className="oi-ratio">P/C {pcOI.toFixed(2)}</span>}
                    </span>
                  </div>
                </div>
                <div className="oi-tops">
                  <div className="oi-col">
                    <div className="oi-col-head" style={{color: "var(--up)"}}>Top call OI</div>
                    {topCallOI.length ? topCallOI.map(c => (
                      <div key={`coi${c.strike}`} className="oi-row">
                        <span className="oi-strike">${c.strike.toFixed(2)}</span>
                        <span className="oi-num">{fmtN(c.openInterest)}</span>
                      </div>
                    )) : <div className="oi-row muted">no data</div>}
                  </div>
                  <div className="oi-col">
                    <div className="oi-col-head" style={{color: "var(--down)"}}>Top put OI</div>
                    {topPutOI.length ? topPutOI.map(p => (
                      <div key={`poi${p.strike}`} className="oi-row">
                        <span className="oi-strike">${p.strike.toFixed(2)}</span>
                        <span className="oi-num">{fmtN(p.openInterest)}</span>
                      </div>
                    )) : <div className="oi-row muted">no data</div>}
                  </div>
                  <div className="oi-col">
                    <div className="oi-col-head" style={{color: "var(--up)"}}>Top call volume</div>
                    {topCallVol.length ? topCallVol.map(c => (
                      <div key={`cv${c.strike}`} className="oi-row">
                        <span className="oi-strike">${c.strike.toFixed(2)}</span>
                        <span className="oi-num">{fmtN(c.volume)}</span>
                      </div>
                    )) : <div className="oi-row muted">no data</div>}
                  </div>
                  <div className="oi-col">
                    <div className="oi-col-head" style={{color: "var(--down)"}}>Top put volume</div>
                    {topPutVol.length ? topPutVol.map(p => (
                      <div key={`pv${p.strike}`} className="oi-row">
                        <span className="oi-strike">${p.strike.toFixed(2)}</span>
                        <span className="oi-num">{fmtN(p.volume)}</span>
                      </div>
                    )) : <div className="oi-row muted">no data</div>}
                  </div>
                </div>
              </div>
            </div>
          );
        })()}
        </div>
        </TabPanel>


        <TabPanel tab="flow" active={activeTab}>
        {/* Market-wide flow dashboard — UW market tide + sector flow.
            Collapsed by default to save space. Hides when UW is off. */}
        {uwHealth?.connected && (
        <CardErrorBoundary label="Market dashboard">
        <div className="card market-dashboard-card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head" style={{cursor: "pointer"}}
               onClick={() => setMarketDashOpen(v => !v)}
               title="Market-wide flow snapshot. Same for every ticker — independent of the active dashboard symbol. Click to expand or collapse.">
            <div>
              <div className="kicker">Unusual Whales · whole market (not {ticker})</div>
              <div className="card-title">{marketDashOpen ? "▾" : "▸"} Market flow dashboard</div>
            </div>
            {(() => {
              if (!marketDashboard?.tide) return null;
              const tide = marketDashboard.tide;
              // tide may be array or object — handle both
              const tideRow = Array.isArray(tide) ? tide[tide.length - 1] : tide;
              if (!tideRow) return null;
              const callPrem = tideRow.net_call_premium ?? tideRow.call_premium ?? null;
              const putPrem = tideRow.net_put_premium ?? tideRow.put_premium ?? null;
              if (callPrem == null && putPrem == null) return null;
              const net = (callPrem || 0) - (putPrem || 0);
              const regimeCls = net > 0 ? "up" : "down";
              return (
                <div className="market-tide-summary" title="Current market regime read from net call vs put premium">
                  <span className="muted">Net call - put:</span>
                  <b className={regimeCls}>{fmt$M(net)}</b>
                </div>
              );
            })()}
          </div>
          {marketDashOpen && (
            <div className="market-dashboard-body">
              {!marketDashboard && (
                <div className="muted" style={{padding: "12px 0"}}>Loading market flow.</div>
              )}
              {marketDashboard && (
                <>
                  {/* Sector flow grid */}
                  {marketDashboard.sector && (() => {
                    const sectors = Array.isArray(marketDashboard.sector)
                      ? marketDashboard.sector
                      : (marketDashboard.sector.data || []);
                    if (!sectors.length) return null;
                    // Build sortable list with derived net premium
                    const enriched = sectors.map(s => {
                      const net = (s.net_call_premium != null ? s.net_call_premium : (s.call_premium || 0))
                                - (s.net_put_premium != null ? s.net_put_premium : (s.put_premium || 0));
                      return {
                        sector: s.sector || s.name || s.symbol || s.ticker || "—",
                        symbol: s.symbol || s.ticker,
                        net,
                        call: s.net_call_premium ?? s.call_premium ?? null,
                        put: s.net_put_premium ?? s.put_premium ?? null,
                        change_pct: s.change_pct ?? s.change ?? null,
                      };
                    });
                    enriched.sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
                    return (
                      <div className="market-section" title="Net call premium minus net put premium per sector ETF. Bigger absolute value = bigger institutional positioning.">
                        <div className="market-section-title">Sector flow</div>
                        <div className="sector-grid">
                          {enriched.slice(0, 12).map((s, i) => (
                            <div key={i} className={`sector-cell ${s.net >= 0 ? "bull" : "bear"}`}
                                 title={`${s.sector}: net = ${fmt$M(s.net)} · call = ${fmt$M(s.call)} · put = ${fmt$M(s.put)}`}>
                              <div className="sector-name">{s.sector}</div>
                              <div className={`sector-net ${s.net >= 0 ? "up" : "down"}`}>{fmt$M(s.net)}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })()}
                  {/* Spike list — recent unusual movers */}
                  {marketDashboard.spike && (() => {
                    const spikes = Array.isArray(marketDashboard.spike)
                      ? marketDashboard.spike
                      : (marketDashboard.spike.data || []);
                    if (!spikes.length) return null;
                    return (
                      <div className="market-section" title="Tickers UW flagged as having sudden volume or premium spikes.">
                        <div className="market-section-title">Recent spikes</div>
                        <div className="spike-list">
                          {spikes.slice(0, 15).map((s, i) => {
                            const sym = s.ticker || s.symbol || "—";
                            const t = s.type || s.kind || "";
                            return (
                              <div key={i} className="spike-row"
                                   onClick={() => { setTicker(sym); setTickerInput(sym); }}
                                   title={`Click to switch dashboard to ${sym}`}>
                                <span className="ticker-cell">{sym}</span>
                                <span className="muted">{t}</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })()}
                  {!marketDashboard.tide && !marketDashboard.sector && !marketDashboard.spike && (
                    <div className="muted" style={{padding: "12px 0"}}>
                      Market dashboard data unavailable. Some endpoints may not be on your plan.
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
        </CardErrorBoundary>
        )}

        </TabPanel>


        <TabPanel tab="scanners" active={activeTab}>
        {/* Market-wide UW scanner — finds tickers OFF the watchlist that
            have unusual flow today. Hides when UW disconnected. */}
        {uwHealth?.connected && (
        <CardErrorBoundary label="Market scanner">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Unusual Whales · tickers NOT in your watchlist</div>
              <div className="card-title">Market scanner</div>
            </div>
            <div className="research-controls">
              {marketScanAt && (
                <span className="scan-stale" title="When the last scan finished">
                  scanned {Math.round((Date.now() - marketScanAt) / 60000)} min ago
                </span>
              )}
              <button className="research-run-btn"
                      disabled={marketScanRunning}
                      onClick={runMarketScan}
                      title="Pull today's market-wide unusual flow alerts (excluding your watchlist) and score each by flow + IV rank + earnings proximity. ~3 UW calls per ticker.">
                {marketScanRunning
                  ? `Scanning. ${marketScanProgress.done}/${marketScanProgress.total}`
                  : (marketScanRows.length > 0 ? "Re-scan" : "Scan market")}
              </button>
            </div>
          </div>
          {marketScanError && (
            <div className="research-error">Error: {marketScanError}</div>
          )}
          {marketScanRows.length === 0 && !marketScanRunning && !marketScanError && (
            <div className="research-empty">
              Click <b>Scan market</b> to find tickers off your watchlist with unusual options flow today.
            </div>
          )}
          {marketScanRows.length > 0 && (() => {
            // Filter out: errors, missing flow data, and "Noisy flow" verdicts
            // (low-conviction flow that doesn't deserve attention).
            const usable = marketScanRows.filter(r =>
              !r.error
              && r.flow_score?.data_available
              && !/noisy/i.test(r.flow_score?.verdict || "")
            );
            const hiddenCount = marketScanRows.filter(r =>
              !r.error
              && r.flow_score?.data_available
              && /noisy/i.test(r.flow_score?.verdict || "")
            ).length;
            // Derive direction from sub-scores: bullish if bullish dominates,
            // bearish if bearish dominates, neutral otherwise. Threshold of
            // 10 points avoids flipping on weak signals.
            const dirOf = (r) => {
              const fs = r.flow_score;
              if (!fs) return "neutral";
              const b = fs.bullish ?? 0;
              const x = fs.bearish ?? 0;
              if (b - x >= 10) return "bullish";
              if (x - b >= 10) return "bearish";
              return "neutral";
            };
            const sortable = [...usable];
            const dir = marketScanSort.dir === "asc" ? 1 : -1;
            sortable.sort((a, b) => {
              let av, bv;
              // Per-row unusual concentration helper. r.total_premium is
              // the sum of UW unusual flow alerts; r.total_premium_today
              // is the day's total. Ratio is what tells you "is today's
              // flow unusually concentrated in big trades?"
              const unusPctOf = (r) => {
                const u = r.total_premium;
                const t = r.total_premium_today;
                if (u == null || t == null || t <= 0) return null;
                return (u / t) * 100;
              };
              switch (marketScanSort.key) {
                case "symbol": av = a.symbol; bv = b.symbol; break;
                case "flow_score": av = a.flow_score?.overall; bv = b.flow_score?.overall; break;
                case "direction":
                  // Order: bearish < neutral < bullish
                  const ord = {bearish: 0, neutral: 1, bullish: 2};
                  av = ord[dirOf(a)]; bv = ord[dirOf(b)];
                  break;
                case "verdict": av = a.flow_score?.verdict; bv = b.flow_score?.verdict; break;
                case "total_premium_today": av = a.total_premium_today; bv = b.total_premium_today; break;
                case "net_premium_today": av = a.net_premium_today; bv = b.net_premium_today; break;
                case "total_volume_today": av = a.total_volume_today; bv = b.total_volume_today; break;
                case "premium": av = a.total_premium; bv = b.total_premium; break;
                case "unusual_pct": av = unusPctOf(a); bv = unusPctOf(b); break;
                case "alert_count": av = a.alert_count; bv = b.alert_count; break;
                case "price": av = a.last_price; bv = b.last_price; break;
                case "change_pct": av = a.change_pct; bv = b.change_pct; break;
                case "iv_rank": av = a.iv_rank; bv = b.iv_rank; break;
                case "days_to_earnings": av = a.days_to_earnings; bv = b.days_to_earnings; break;
                case "analyst_signal":
                  // Sort priority: fresh upgrades first, fresh downgrades next,
                  // then overextended-warning rows, then plain rows.
                  // Higher number = higher in desc sort.
                  const _aScore = (r) => {
                    const s = r.analyst || {};
                    if (s.fresh_upgrade) return 3;
                    if (s.fresh_downgrade) return 2;
                    if (s.above_high_target) return 1;
                    return 0;
                  };
                  av = _aScore(a); bv = _aScore(b); break;
                default: av = a[marketScanSort.key]; bv = b[marketScanSort.key];
              }
              if (av == null && bv == null) return 0;
              if (av == null) return 1;
              if (bv == null) return -1;
              if (typeof av === "string") return av.localeCompare(bv) * dir;
              return (av - bv) * dir;
            });
            const SortHeader = ({label, k, tip}) => {
              const active = marketScanSort.key === k;
              const arrow = !active ? "" : marketScanSort.dir === "desc" ? " ▾" : " ▴";
              return (
                <span className={`pb-th${active ? " active" : ""}`}
                      title={tip}
                      onClick={() => {
                        if (marketScanSort.key === k) {
                          setMarketScanSort({key: k, dir: marketScanSort.dir === "desc" ? "asc" : "desc"});
                        } else {
                          setMarketScanSort({key: k, dir: "desc"});
                        }
                      }}>
                  {label}{arrow}
                </span>
              );
            };
            const fmtEarn = (iso, days, cls) => {
              if (!iso) return <span className="muted">—</span>;
              const lbl = days != null
                ? (days === 0 ? "today" : days === 1 ? "tomorrow" : `${days}d`)
                : iso;
              const pillCls = cls === "imminent" ? "earn-pill earn-imminent"
                : cls === "soon" ? "earn-pill earn-soon" : "earn-pill";
              return <span className={pillCls} title={`Next earnings on ${iso}`}>{lbl}</span>;
            };
            return (
              <div className="market-scan-table">
                <div className="market-scan-head">
                  <SortHeader label="Ticker" k="symbol" tip="Click to sort alphabetically" />
                  <SortHeader label="Score" k="flow_score" tip="UW flow score 0-100. Above 65 = bullish lean. Below 35 = bearish lean. IV rank shown on hover of each cell." />
                  <SortHeader label="Dir" k="direction" tip="Direction read from flow sub-scores. Bullish = ask-side calls dominating. Bearish = ask-side puts dominating." />
                  <SortHeader label="Verdict" k="verdict" tip="UW decision-engine read on covered-call viability." />
                  <SortHeader label="Total $" k="total_premium_today" tip="Total options premium today across ALL trades (calls + puts). From UW ticker_options_volume — what the UW website chart shows." />
                  <SortHeader label="Unus $" k="premium" tip="Premium from UW unusual flow alerts only — the trades UW flagged as unusual. Hover to see alert count." />
                  <SortHeader label="Unus %" k="unusual_pct" tip="Unusual / Total premium ratio. High = today's options flow is unusually concentrated in flagged trades. The primary 'unusual activity' signal — sort default." />
                  <SortHeader label="Net $" k="net_premium_today" tip="Net premium = call premium − put premium. Positive (green) means call premium dominates (bullish positioning). Negative (red) means put premium dominates (bearish positioning)." />
                  <SortHeader label="Vol" k="total_volume_today" tip="Total options contracts traded today. Hover to see call/put split." />
                  <SortHeader label="Price" k="price" tip="Live price (Schwab if available)." />
                  <SortHeader label="Chg%" k="change_pct" tip="Today's change vs prior close." />
                  <SortHeader label="Analyst" k="analyst_signal" tip="Fresh analyst catalyst today. ↑ = upgrade today. ↓ = downgrade today. ⚠ = trading above highest analyst target. — = no recent activity." />
                  <SortHeader label="Earnings" k="days_to_earnings" tip="Days to next earnings. Red = within 7 days. Yellow = within 14 days." />
                </div>
                {sortable.map(r => {
                  const overall = r.flow_score?.overall ?? null;
                  const cls = overall == null ? "fair"
                    : overall >= 70 ? "rich"
                    : overall >= 60 ? "moderate"
                    : overall <= 30 ? "thin"
                    : overall <= 40 ? "bear-mod"
                    : "fair";
                  const isActive = r.symbol === ticker;
                  const chgCls = r.change_pct == null ? ""
                    : r.change_pct > 0 ? "up" : "down";
                  const direction = dirOf(r);
                  const dirCls = direction === "bullish" ? "dir-bull"
                    : direction === "bearish" ? "dir-bear" : "dir-neutral";
                  const dirLabel = direction === "bullish" ? "BULL"
                    : direction === "bearish" ? "BEAR" : "—";
                  const netPrem = r.net_premium_today;
                  const netCls = netPrem == null ? ""
                    : netPrem > 0 ? "up" : netPrem < 0 ? "down" : "";
                  const totalTip = r.total_premium != null && r.total_premium_today
                    ? `Total: ${fmt$M(r.total_premium_today)} · Unusual flow: ${fmt$M(r.total_premium)} (${(r.total_premium / r.total_premium_today * 100).toFixed(0)}% of total)`
                    : `Total premium today`;
                  const volTip = (r.call_volume_today != null || r.put_volume_today != null)
                    ? `Calls: ${fmtVol(r.call_volume_today)} · Puts: ${fmtVol(r.put_volume_today)}${r.put_call_ratio != null ? ` · P/C: ${r.put_call_ratio.toFixed(2)}` : ""}`
                    : "Total options volume today";
                  const scoreTip = r.iv_rank != null
                    ? `Score ${overall ?? "—"} · IV rank ${Number(r.iv_rank).toFixed(0)}%`
                    : `Score ${overall ?? "—"}`;
                  const netTip = (r.call_premium_today != null && r.put_premium_today != null)
                    ? `Calls: ${fmt$M(r.call_premium_today)} · Puts: ${fmt$M(r.put_premium_today)} · Net: ${fmt$M(netPrem)}`
                    : "Net premium = call − put";
                  // Unusual concentration: how much of today's premium is
                  // from UW's flagged unusual flow alerts. Higher = today's
                  // flow is unusually weighted toward big institutional trades.
                  const unusPct = (r.total_premium != null && r.total_premium_today && r.total_premium_today > 0)
                    ? (r.total_premium / r.total_premium_today) * 100
                    : null;
                  const unusPctCls = unusPct == null ? ""
                    : unusPct >= 50 ? "unus-high"
                    : unusPct >= 20 ? "unus-mid"
                    : "unus-low";
                  const unusTip = `${r.alert_count ?? 0} unusual flow alert${(r.alert_count ?? 0) === 1 ? "" : "s"} today`
                    + (unusPct != null ? ` · ${unusPct.toFixed(1)}% of total premium` : "");
                  // Analyst catalyst cell — compact iconography for fast scanning.
                  // ↑ green = fresh upgrade today
                  // ↓ red   = fresh downgrade today
                  // ⚠ amber = trading above highest analyst target (overextended)
                  // — gray  = no recent analyst catalyst
                  const aSig = r.analyst || {};
                  let analystIcon = "—";
                  let analystCls = "analyst-cell-none";
                  let analystTip = "No fresh analyst catalyst";
                  if (aSig.fresh_upgrade) {
                    analystIcon = "↑"; analystCls = "analyst-cell-upgrade";
                    analystTip = "Fresh analyst upgrade today — possible bullish catalyst";
                  } else if (aSig.fresh_downgrade) {
                    analystIcon = "↓"; analystCls = "analyst-cell-downgrade";
                    analystTip = "Fresh analyst downgrade today — possible bearish catalyst";
                  } else if (aSig.above_high_target) {
                    analystIcon = "⚠"; analystCls = "analyst-cell-stretched";
                    analystTip = "Trading above highest analyst target — possible overextension";
                  }
                  if (aSig.upside_pct != null) {
                    analystTip += ` · ${aSig.upside_pct >= 0 ? "+" : ""}${aSig.upside_pct.toFixed(1)}% to avg target`;
                  }
                  return (
                    <div key={r.symbol}
                         className={`market-scan-row${isActive ? " is-active" : ""}`}
                         title={isActive ? `${r.symbol} is the active ticker` : `Click to switch dashboard to ${r.symbol}`}
                         onClick={() => { setTicker(r.symbol); setTickerInput(r.symbol); }}>
                      <span className="ticker-cell">{r.symbol}</span>
                      <span className={`richness-score ${cls}`} title={scoreTip}>
                        {overall != null ? overall : "—"}
                      </span>
                      <span className={`scan-dir-pill ${dirCls}`}
                            title={`Bullish ${r.flow_score?.bullish ?? 0} · Bearish ${r.flow_score?.bearish ?? 0}`}>
                        {dirLabel}
                      </span>
                      <span className={`richness-verdict ${r.flow_score?.verdict_class || ""}`}>
                        {r.flow_score?.verdict || "—"}
                      </span>
                      <span className="num-strong" title={totalTip}>{fmt$M(r.total_premium_today)}</span>
                      <span title={unusTip}>{fmt$M(r.total_premium)}</span>
                      <span className={`unus-pct ${unusPctCls}`} title={unusTip}>
                        {unusPct != null ? unusPct.toFixed(1) + "%" : "—"}
                      </span>
                      <span className={netCls} title={netTip}>{fmt$M(netPrem)}</span>
                      <span title={volTip}>{fmtVol(r.total_volume_today)}</span>
                      <span>{r.last_price != null ? "$" + Number(r.last_price).toFixed(2) : "—"}</span>
                      <span className={chgCls}>{fmtPct(r.change_pct)}</span>
                      <span className={`analyst-cell ${analystCls}`} title={analystTip}>{analystIcon}</span>
                      <span>{fmtEarn(r.next_earnings, r.days_to_earnings, r.earnings_class)}</span>
                    </div>
                  );
                })}
                {sortable.length === 0 && (
                  <div className="research-empty">No usable rows. Try re-scanning when markets are open.</div>
                )}
                {hiddenCount > 0 && (
                  <div className="muted" style={{padding: "8px 10px", fontSize: 11, textAlign: "right"}}>
                    {hiddenCount} {hiddenCount === 1 ? "ticker" : "tickers"} hidden as noisy flow.
                  </div>
                )}
              </div>
            );
          })()}
        </div>
        </CardErrorBoundary>
        )}

        {/* EMA Pullback Strategy — backtest the daily 9 EMA pullback rules
            on the active ticker, plus scanner across watchlist for tickers
            currently in setup. Long or short via toggle. */}
        <CardErrorBoundary label="EMA pullback strategy">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Daily EMA pullback · trend-continuation</div>
              <div className="card-title">EMA pullback strategy</div>
            </div>
            <div className="research-controls">
              <div className="ema-dir-toggle" title="Long = uptrend pullback to fast EMA. Short = downtrend bounce to fast EMA.">
                <button className={emaDirection === "long" ? "active up" : ""}
                        onClick={() => { setEmaDirection("long"); setEmaBacktest(null); setEmaScan({}); }}>
                  LONG
                </button>
                <button className={emaDirection === "short" ? "active down" : ""}
                        onClick={() => { setEmaDirection("short"); setEmaBacktest(null); setEmaScan({}); }}>
                  SHORT
                </button>
              </div>
            </div>
          </div>

          {/* EMA period setting — single Fast EMA input. Med (21) and
              Slow (50) stay fixed as the trend filter; only the
              pullback-target EMA is user-tunable. */}
          <div className="ema-params-row">
            <div className="ema-param" title="Fast EMA period — the pullback target. Try different values (5, 8, 9, 13, 20) to see which works best on this stock. Med (21) and Slow (50) EMAs are fixed.">
              <span className="ema-param-lbl">EMA</span>
              <input type="number" min="2" max="50" step="1"
                     value={emaFast}
                     onChange={e => { const v = Math.max(2, Math.min(50, parseInt(e.target.value) || 9)); setEmaFast(v); setEmaBacktest(null); }} />
            </div>
          </div>

          {/* ── Backtest section ── */}
          <div className="ema-section">
            <div className="ema-section-title">
              Backtest · {ticker} · {emaFast} EMA · 1 year of daily bars
              <button className="research-run-btn"
                      style={{marginLeft: 12}}
                      disabled={emaBacktestRunning || !ticker}
                      onClick={runEmaBacktest}
                      title="Run the strategy on this ticker's last 365 days. Each entry uses the next bar's open after a confirmation candle.">
                {emaBacktestRunning ? "Running…" : (emaBacktest ? "Re-run" : "Run backtest")}
              </button>
            </div>
            {emaBacktestError && (
              <div className="research-error">Error: {emaBacktestError}</div>
            )}
            {!emaBacktest && !emaBacktestRunning && !emaBacktestError && (
              <div className="research-empty">
                Click <b>Run backtest</b> to test the {emaDirection} EMA pullback strategy on {ticker} over the last year.
              </div>
            )}
            {emaBacktest && !emaBacktest.error && (() => {
              const t = emaBacktest;
              // Top stats grid
              const winCls = t.win_rate_pct >= 55 ? "up" : t.win_rate_pct >= 45 ? "" : "down";
              // Expectancy in R is the honest read: > 0.10R per trade is solid,
              // > 0.30R is excellent, negative means losing strategy.
              const expRCls = t.expectancy_R > 0.10 ? "up" : t.expectancy_R < 0 ? "down" : "";
              const pfCls = t.profit_factor >= 1.5 ? "up" : t.profit_factor >= 1.0 ? "" : "down";
              const retCls = t.total_return_pct > 0 ? "up" : "down";
              const totalRCls = t.total_R > 0 ? "up" : t.total_R < 0 ? "down" : "";
              return (
                <>
                  <div className="ema-stats-grid">
                    <div className="ema-stat" title="Total trades the strategy generated over the lookback period.">
                      <div className="ema-stat-lbl">Trades</div>
                      <div className="ema-stat-val">{t.n_trades}</div>
                    </div>
                    <div className="ema-stat" title="Percentage of trades that were profitable.">
                      <div className="ema-stat-lbl">Win rate</div>
                      <div className={`ema-stat-val ${winCls}`}>{t.win_rate_pct}%</div>
                    </div>
                    <div className="ema-stat" title="Total R earned across all trades. R = (exit - entry) / risk_per_share. The standard strategy-performance metric — independent of capital and position size. Positive total R = profitable strategy.">
                      <div className="ema-stat-lbl">Total R</div>
                      <div className={`ema-stat-val ${totalRCls}`}>
                        {t.total_R > 0 ? "+" : ""}{t.total_R}R
                      </div>
                    </div>
                    <div className="ema-stat" title="Expected R per trade. Above 0.10R per trade = positive edge. Above 0.30R = excellent. Negative means the strategy loses money over time.">
                      <div className="ema-stat-lbl">Expectancy</div>
                      <div className={`ema-stat-val ${expRCls}`}>
                        {t.expectancy_R > 0 ? "+" : ""}{t.expectancy_R}R
                      </div>
                    </div>
                    <div className="ema-stat" title="Average R on winning trades.">
                      <div className="ema-stat-lbl">Avg win</div>
                      <div className="ema-stat-val up">+{t.win_R_avg}R</div>
                    </div>
                    <div className="ema-stat" title="Average R on losing trades. Should be near -1R if stops are honored. More negative means slippage or gap-down losses beyond the stop.">
                      <div className="ema-stat-lbl">Avg loss</div>
                      <div className="ema-stat-val down">{t.loss_R_avg}R</div>
                    </div>
                    <div className="ema-stat" title="Profit factor: gross wins / gross losses. Above 1.5 = solid. Below 1.0 = losing.">
                      <div className="ema-stat-lbl">P factor</div>
                      <div className={`ema-stat-val ${pfCls}`}>{t.profit_factor}</div>
                    </div>
                    <div className="ema-stat" title="Account return at 1% risk per trade — what you would have actually earned on a real account sized to risk 1% on each trade. This is realistic position sizing, not 100%-bet compounding.">
                      <div className="ema-stat-lbl">Acct ret @ 1%</div>
                      <div className={`ema-stat-val ${retCls}`}>
                        {t.total_return_pct >= 0 ? "+" : ""}{t.total_return_pct}%
                      </div>
                    </div>
                  </div>
                  {t.n_trades === 0 && (
                    <div className="research-empty">
                      No qualifying setups in the last {t.lookback_days} days. The trend filter rejects choppy / range-bound stocks.
                    </div>
                  )}
                  {t.trades && t.trades.length > 0 && (
                    <div className="ema-trades">
                      <div className="ema-trades-title">Trade history (most recent first)</div>
                      <div className="ema-trades-table">
                        <div className="ema-trades-head">
                          <span>Entry</span><span>Exit</span><span>Entry $</span>
                          <span>Exit $</span><span>P&L %</span><span>R</span>
                          <span>Bars</span><span>Reason</span>
                        </div>
                        {[...t.trades].reverse().slice(0, 30).map((tr, i) => {
                          const pnlCls = tr.pnl_pct > 0 ? "up" : tr.pnl_pct < 0 ? "down" : "";
                          return (
                            <div key={i} className="ema-trades-row">
                              <span className="muted">{tr.entry_date}</span>
                              <span className="muted">{tr.exit_date}</span>
                              <span>${tr.entry_price.toFixed(2)}</span>
                              <span>${tr.exit_price.toFixed(2)}</span>
                              <span className={pnlCls}>{tr.pnl_pct > 0 ? "+" : ""}{tr.pnl_pct}%</span>
                              <span className={pnlCls}>{tr.r_multiple > 0 ? "+" : ""}{tr.r_multiple}R</span>
                              <span className="muted">{tr.bars_held}</span>
                              <span className="muted">{tr.exit_reason}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </>
              );
            })()}
            {emaBacktest && emaBacktest.error && (
              <div className="research-error">Error: {emaBacktest.error}</div>
            )}
          </div>

          {/* ── Scanner section ── */}
          <div className="ema-section" style={{marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--line)"}}>
            <div className="ema-section-title">
              Watchlist scanner · find {emaDirection} setups now · {emaFast} EMA
              <button className="research-run-btn"
                      style={{marginLeft: 12}}
                      disabled={emaScanRunning || !filteredWatchlistSymbols.length}
                      onClick={runEmaScan}
                      title="Score every watchlist ticker for current EMA pullback setup state. No options data — pure technicals.">
                {emaScanRunning
                  ? `Scanning. ${emaScanProgress.done}/${emaScanProgress.total}`
                  : (Object.keys(emaScan).length > 0 ? "Re-scan" : `Scan ${filteredWatchlistSymbols.length}`)}
              </button>
              {emaScanAt && (
                <span className="scan-stale" style={{marginLeft: 12}} title="When the last scan finished">
                  scanned {Math.round((Date.now() - emaScanAt) / 60000)} min ago
                </span>
              )}
            </div>
            {emaScanError && (
              <div className="research-error">Error: {emaScanError}</div>
            )}
            {!filteredWatchlistSymbols.length && (
              <div className="research-empty">No tickers in watchlist.</div>
            )}
            {filteredWatchlistSymbols.length > 0 && Object.keys(emaScan).length === 0 && !emaScanRunning && (
              <div className="research-empty">
                Click <b>Scan</b> to find which {filteredWatchlistSymbols.length} watchlist {filteredWatchlistSymbols.length === 1 ? "ticker is" : "tickers are"} currently in {emaDirection} setup.
              </div>
            )}
            {Object.keys(emaScan).length > 0 && (() => {
              // Group by state
              const rows = filteredWatchlistSymbols
                .map(sym => emaScan[sym] || {symbol: sym, state: "pending"})
                .filter(r => !r.error);
              const order = {"confirmed": 0, "pulled_back": 1, "in_trend": 2, "no_trend": 3, "no_data": 4, "pending": 5};
              const sorted = [...rows].sort((a, b) =>
                (order[a.state] ?? 99) - (order[b.state] ?? 99) ||
                a.symbol.localeCompare(b.symbol));
              const stateCounts = {};
              sorted.forEach(r => { stateCounts[r.state] = (stateCounts[r.state] || 0) + 1; });
              const stateLabel = (s) => ({
                "confirmed": "Confirmed (entry tomorrow)",
                "pulled_back": "Pulled back (awaiting confirmation)",
                "in_trend": "In trend, no recent pullback",
                "no_trend": "Not in trend",
                "no_data": "No data",
                "pending": "Pending",
              })[s] || s;
              const stateCls = (s) => ({
                "confirmed": "ema-state-confirmed",
                "pulled_back": "ema-state-pulled",
                "in_trend": "ema-state-trending",
                "no_trend": "ema-state-notrend",
                "no_data": "ema-state-nodata",
                "pending": "ema-state-nodata",
              })[s] || "ema-state-nodata";
              return (
                <>
                  <div className="ema-state-summary">
                    {["confirmed", "pulled_back", "in_trend", "no_trend"].map(s => (
                      stateCounts[s] > 0 && (
                        <span key={s} className={`ema-state-pill ${stateCls(s)}`}
                              title={stateLabel(s)}>
                          {stateCounts[s]} {s.replace("_", " ")}
                        </span>
                      )
                    ))}
                  </div>
                  <div className="ema-scan-table">
                    <div className="ema-scan-head">
                      <span>Ticker</span><span>State</span><span>Close</span>
                      <span>{emaFast} EMA</span><span>21 EMA</span><span>50 EMA</span>
                      <span>RSI</span><span>Stop</span>
                    </div>
                    {sorted
                      .filter(r => r.state === "confirmed" || r.state === "pulled_back" || r.state === "in_trend")
                      .map(r => {
                        const isActive = r.symbol === ticker;
                        return (
                          <div key={r.symbol}
                               className={`ema-scan-row ${stateCls(r.state)}${isActive ? " is-active" : ""}`}
                               onClick={() => { setTicker(r.symbol); setTickerInput(r.symbol); }}
                               title={`${stateLabel(r.state)} — click to switch dashboard to ${r.symbol}`}>
                            <span className="ticker-cell">{r.symbol}</span>
                            <span className="ema-state-text">{r.state.replace("_", " ")}</span>
                            <span>{r.close != null ? "$" + r.close.toFixed(2) : "—"}</span>
                            <span>{r.ema9 != null ? "$" + r.ema9.toFixed(2) : "—"}</span>
                            <span>{r.ema21 != null ? "$" + r.ema21.toFixed(2) : "—"}</span>
                            <span>{r.ema50 != null ? "$" + r.ema50.toFixed(2) : "—"}</span>
                            <span>{r.rsi14 != null ? r.rsi14.toFixed(1) : "—"}</span>
                            <span className="num-strong">{r.suggested_stop != null ? "$" + r.suggested_stop.toFixed(2) : "—"}</span>
                          </div>
                        );
                      })}
                  </div>
                </>
              );
            })()}
          </div>
        </div>
        </CardErrorBoundary>

        {/* Pullback scanner — ranks watchlist by historical open-to-low
            pullback. Helps identify which symbols are reliable
            short-the-open candidates vs gap-and-go traps. */}
        <CardErrorBoundary label="Pullback scanner">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Open-to-{pbScanDir === "short" ? "low pullback" : "high pop"} · 180d history</div>
              <div className="card-title">{pbScanDir === "short" ? "Short the open" : "Buy the open"} · pullback scanner</div>
            </div>
            <div className="research-controls">
              <div className="basing-toggle" title="Switch between short and long open-trade scanners">
                <button className={pbScanDir === "short" ? "active" : ""}
                        onClick={() => setPbScanDir("short")}
                        title="Rank for short-the-open setups">Short</button>
                <button className={pbScanDir === "long" ? "active" : ""}
                        onClick={() => setPbScanDir("long")}
                        title="Rank for buy-the-open setups">Long</button>
              </div>
              {pullbackScanAt && (
                <span className="scan-stale">scanned {Math.round((Date.now() - pullbackScanAt) / 60000)} min ago</span>
              )}
              <button className="research-run-btn"
                      disabled={pullbackScanRunning || !filteredWatchlistSymbols.length}
                      onClick={runPullbackScan}>
                {pullbackScanRunning
                  ? `Scanning. ${pullbackScanProgress.done}/${pullbackScanProgress.total}`
                  : (Object.keys(pullbackScan).length > 0 ? "Re-scan" : `Scan ${filteredWatchlistSymbols.length}`)}
              </button>
            </div>
          </div>
          {pullbackScanError && (
            <div className="research-error">Error: {pullbackScanError}</div>
          )}
          {!filteredWatchlistSymbols.length && (
            <div className="research-empty">
              {watchlistData.symbols.length === 0
                ? "No tickers in watchlist. Click Manage in the sidebar."
                : `No tickers match the "${watchlistTagFilter}" tag.`}
            </div>
          )}
          {filteredWatchlistSymbols.length > 0 && Object.keys(pullbackScan).length === 0 && !pullbackScanRunning && (
            <div className="research-empty">
              Click <b>Scan</b> to compute open-to-{pbScanDir === "short" ? "low pullback" : "high pop"} stats for {filteredWatchlistSymbols.length} {filteredWatchlistSymbols.length === 1 ? "ticker" : "tickers"}. Allow ~1-2 seconds per symbol.
            </div>
          )}
          {Object.keys(pullbackScan).length > 0 && (() => {
            const isShortScan = pbScanDir === "short";
            const rows = filteredWatchlistSymbols
              .map(sym => pullbackScan[sym] || {symbol: sym, pending: true})
              .filter(r => !r.error && !r.pending);
            // Direction-specific enrichment
            const enriched = rows.map(r => {
              if (isShortScan) {
                const gm = r.gap_up?.median_pullback ?? r.median_pullback ?? 0;
                const gg = r.gap_up?.gap_and_go_pct ?? r.open_eq_low_pct ?? 50;
                return {
                  ...r,
                  _score: gm - gg / 20,
                  _median: r.median_pullback,
                  _p75: r.p75_pullback,
                  _eqRate: r.open_eq_low_pct,
                  _gapMedian: r.gap_up?.median_pullback ?? null,
                  _gapEqRate: r.gap_up?.gap_and_go_pct ?? null,
                };
              } else {
                const gm = r.gap_down?.median_pop ?? r.median_pop ?? 0;
                const gh = r.gap_down?.open_eq_high_pct ?? r.open_eq_high_pct ?? 50;
                return {
                  ...r,
                  _score: gm - gh / 20,
                  _median: r.median_pop,
                  _p75: r.p75_pop,
                  _eqRate: r.open_eq_high_pct,
                  _gapMedian: r.gap_down?.median_pop ?? null,
                  _gapEqRate: r.gap_down?.open_eq_high_pct ?? null,
                };
              }
            });
            const sortable = [...enriched];
            const dir = pbSort.dir === "asc" ? 1 : -1;
            sortable.sort((a, b) => {
              const av = a[pbSort.key];
              const bv = b[pbSort.key];
              if (av == null && bv == null) return 0;
              if (av == null) return 1;
              if (bv == null) return -1;
              if (typeof av === "string") return av.localeCompare(bv) * dir;
              return (av - bv) * dir;
            });

            const SortHeader = ({label, k, tip}) => {
              const active = pbSort.key === k;
              const arrow = !active ? "" : pbSort.dir === "desc" ? " ▾" : " ▴";
              return (
                <span className={`pb-th${active ? " active" : ""}`}
                      title={tip}
                      onClick={() => {
                        if (pbSort.key === k) {
                          setPbSort({key: k, dir: pbSort.dir === "desc" ? "asc" : "desc"});
                        } else {
                          setPbSort({key: k, dir: "desc"});
                        }
                      }}>
                  {label}{arrow}
                </span>
              );
            };

            const eqLabel = isShortScan ? "Open=Low" : "Open=High";
            const gapMedianLabel = isShortScan ? "Gap median" : "Gap dn median";
            const gapEqLabel = isShortScan ? "Gap=Go" : "Gap=Top";
            const medianTip = isShortScan
              ? "Median open-to-low pullback across all days. Click to sort."
              : "Median open-to-high pop across all days. Click to sort.";
            const p75Tip = isShortScan
              ? "75th percentile open-to-low pullback. 25% of historical days exceeded this. Click to sort."
              : "75th percentile open-to-high pop. 25% of historical days exceeded this. Click to sort.";
            const eqTip = isShortScan
              ? "Frequency the open was the day's low (gap-and-go rate). Higher = more risk for shorts. Click to sort."
              : "Frequency the open was the day's high (no pop). Higher = more risk for longs. Click to sort.";
            const gapMedianTip = isShortScan
              ? "Median open-to-low pullback on gap-up days only (≥1%). Click to sort."
              : "Median open-to-high pop on gap-down days only (≤-1%). Click to sort.";
            const gapEqTip = isShortScan
              ? "Frequency of gap-and-go behavior on gap-up days. Higher = more risk. Click to sort."
              : "Frequency of open=high on gap-down days. Higher = more risk. Click to sort.";

            return (
              <div className="pullback-scan-table">
                <div className="pullback-scan-head">
                  <SortHeader label="Ticker" k="symbol" tip="Click to sort alphabetically by ticker symbol" />
                  <SortHeader label="Median" k="_median" tip={medianTip} />
                  <SortHeader label="p75" k="_p75" tip={p75Tip} />
                  <SortHeader label={eqLabel} k="_eqRate" tip={eqTip} />
                  <SortHeader label={gapMedianLabel} k="_gapMedian" tip={gapMedianTip} />
                  <SortHeader label={gapEqLabel} k="_gapEqRate" tip={gapEqTip} />
                  <SortHeader label="Days history" k="samples" tip="Number of historical trading days in the sample. Click to sort." />
                  <SortHeader label="Verdict" k="_score" tip="Open-trade verdict. Click to sort by attractiveness score." />
                </div>
                {sortable.map(r => {
                  const eqRate = r._gapEqRate ?? r._eqRate;
                  const med = r._gapMedian ?? r._median;
                  let verdict = "—";
                  let cls = "";
                  if (eqRate != null && eqRate >= 35) {
                    verdict = isShortScan ? "Gap & go risk" : "No-pop risk";
                    cls = "verdict-avoid";
                  } else if (med != null && med >= 0.6 && eqRate < 25) {
                    verdict = "Tradable";
                    cls = "verdict-sell";
                  } else if (r._median >= 0.5) {
                    verdict = "Mixed";
                    cls = "verdict-partial";
                  } else {
                    verdict = "Weak setup";
                    cls = "verdict-wait";
                  }
                  const isActive = r.symbol === ticker;
                  return (
                    <div key={r.symbol}
                         className={`pullback-scan-row${isActive ? " is-active" : ""}`}
                         title={isActive ? `${r.symbol} is the active ticker` : `Click to switch dashboard to ${r.symbol}`}
                         onClick={() => { setTicker(r.symbol); setTickerInput(r.symbol); }}>
                      <span className="ticker-cell">{r.symbol}</span>
                      <span>{r._median != null ? r._median.toFixed(2) + "%" : "—"}</span>
                      <span>{r._p75 != null ? r._p75.toFixed(2) + "%" : "—"}</span>
                      <span>{r._eqRate != null ? r._eqRate.toFixed(2) + "%" : "—"}</span>
                      <span>{r._gapMedian != null ? r._gapMedian.toFixed(2) + "%" : "—"}</span>
                      <span>{r._gapEqRate != null ? r._gapEqRate.toFixed(2) + "%" : "—"}</span>
                      <span className="muted">{r.samples}</span>
                      <span className={`pullback-scan-verdict ${cls}`}>{verdict}</span>
                    </div>
                  );
                })}
                {sortable.length === 0 && (
                  <div className="research-empty">No usable rows yet.</div>
                )}
              </div>
            );
          })()}
        </div>
        </CardErrorBoundary>

        {/* Intraday momentum scanner — UW + price action. Visible
            without UW, but flow signals only fire when UW connected. */}
        <CardErrorBoundary label="Momentum scanner">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Today's price action × Unusual Whales flow</div>
              <div className="card-title">Intraday momentum scanner</div>
            </div>
            <div className="research-controls">
              {momentumAt && (
                <span className="scan-stale" title="When the last scan finished">
                  scanned {Math.round((Date.now() - momentumAt) / 60000)} min ago
                </span>
              )}
              <button className="research-run-btn"
                      disabled={momentumRunning || !filteredWatchlistSymbols.length}
                      onClick={runMomentumScan}
                      title="Score every watchlist ticker by combining today's price action (gap, % from open, RVOL) with UW flow.">
                {momentumRunning
                  ? `Scanning. ${momentumProgress.done}/${momentumProgress.total}`
                  : (Object.keys(momentumScan).length > 0 ? "Re-scan" : `Scan ${filteredWatchlistSymbols.length}`)}
              </button>
            </div>
          </div>
          {momentumError && (
            <div className="research-error">Error: {momentumError}</div>
          )}
          {!filteredWatchlistSymbols.length && (
            <div className="research-empty">No tickers in watchlist.</div>
          )}
          {filteredWatchlistSymbols.length > 0 && Object.keys(momentumScan).length === 0 && !momentumRunning && (
            <div className="research-empty">
              Click <b>Scan</b> to rank {filteredWatchlistSymbols.length} {filteredWatchlistSymbols.length === 1 ? "ticker" : "tickers"} by intraday momentum + UW flow.
            </div>
          )}
          {Object.keys(momentumScan).length > 0 && (() => {
            const rows = filteredWatchlistSymbols
              .map(sym => momentumScan[sym] || {symbol: sym, pending: true})
              .filter(r => !r.error && !r.pending && r.data_available);
            const sortable = [...rows];
            const dir = momentumSort.dir === "asc" ? 1 : -1;
            sortable.sort((a, b) => {
              const av = (momentumSort.key === "symbol") ? a.symbol : (a[momentumSort.key] ?? a.stats?.[momentumSort.key]);
              const bv = (momentumSort.key === "symbol") ? b.symbol : (b[momentumSort.key] ?? b.stats?.[momentumSort.key]);
              if (av == null && bv == null) return 0;
              if (av == null) return 1;
              if (bv == null) return -1;
              if (typeof av === "string") return av.localeCompare(bv) * dir;
              return (av - bv) * dir;
            });
            const SortHeader = ({label, k, tip}) => {
              const active = momentumSort.key === k;
              const arrow = !active ? "" : momentumSort.dir === "desc" ? " ▾" : " ▴";
              return (
                <span className={`pb-th${active ? " active" : ""}`}
                      title={tip}
                      onClick={() => {
                        if (momentumSort.key === k) {
                          setMomentumSort({key: k, dir: momentumSort.dir === "desc" ? "asc" : "desc"});
                        } else {
                          setMomentumSort({key: k, dir: "desc"});
                        }
                      }}>
                  {label}{arrow}
                </span>
              );
            };
            return (
              <div className="momentum-table">
                <div className="momentum-head">
                  <SortHeader label="Ticker" k="symbol" tip="Click to sort alphabetically" />
                  <SortHeader label="Score" k="score" tip="Momentum score 0-100. Above 65 = bullish setup. Below 35 = bearish setup." />
                  <SortHeader label="Verdict" k="verdict" tip="Plain-English read combining price action and flow." />
                  <SortHeader label="Gap" k="gap_pct" tip="Today's open vs prior close." />
                  <SortHeader label="From open" k="from_open_pct" tip="Current price vs today's open. Holding gains = bullish, fading = bearish." />
                  <SortHeader label="RVOL" k="rvol" tip="Today's volume / 20-day average. ≥1.5x = catalyst-driven flow." />
                  <SortHeader label="Flow" k="flow_overall" tip="UW Flow Score (50 = neutral, >65 = bullish, <35 = bearish)." />
                </div>
                {sortable.map(r => {
                  const cls = r.score >= 70 ? "rich"
                    : r.score >= 60 ? "moderate"
                    : r.score <= 30 ? "thin"
                    : r.score <= 40 ? "bear-mod"
                    : "fair";
                  const isActive = r.symbol === ticker;
                  const fromOpenCls = r.stats?.from_open_pct == null ? ""
                    : r.stats.from_open_pct > 0 ? "up" : "down";
                  const gapCls = r.stats?.gap_pct == null ? ""
                    : r.stats.gap_pct > 0 ? "up" : "down";
                  const rvolCls = (r.stats?.rvol ?? 0) >= 1.5 ? "warn" : "";
                  return (
                    <div key={r.symbol}
                         className={`momentum-row${isActive ? " is-active" : ""}`}
                         title={isActive ? `${r.symbol} is the active ticker` : `Click to switch dashboard to ${r.symbol}`}
                         onClick={() => { setTicker(r.symbol); setTickerInput(r.symbol); }}>
                      <span className="ticker-cell">{r.symbol}</span>
                      <span className={`richness-score ${cls}`}>{r.score}</span>
                      <span className={`richness-verdict ${r.verdict_class || ""}`}>{r.verdict}</span>
                      <span className={gapCls}>{fmtPct(r.stats?.gap_pct)}</span>
                      <span className={fromOpenCls}>{fmtPct(r.stats?.from_open_pct)}</span>
                      <span className={rvolCls}>{r.stats?.rvol != null ? r.stats.rvol.toFixed(2) + "×" : "—"}</span>
                      <span>{r.stats?.flow_overall != null ? r.stats.flow_overall : "—"}</span>
                    </div>
                  );
                })}
                {sortable.length === 0 && (
                  <div className="research-empty">No usable rows. Markets may be closed or data unavailable.</div>
                )}
              </div>
            );
          })()}
        </div>
        </CardErrorBoundary>

        {/* Premium Richness scanner (UW) — manual run only. Hides
            entirely if UW not connected. Adds 1 UW call per ticker so
            a 30-ticker scan uses ~30 of your 120 req/min minute quota. */}
        {uwHealth?.connected && (
        <CardErrorBoundary label="Premium richness scanner">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Unusual Whales · today's premium attractiveness</div>
              <div className="card-title">Premium richness scanner</div>
            </div>
            <div className="research-controls">
              {richnessAt && (
                <span className="scan-stale" title="When the last scan finished">
                  scanned {Math.round((Date.now() - richnessAt) / 60000)} min ago
                </span>
              )}
              <button className="research-run-btn"
                      disabled={richnessRunning || !filteredWatchlistSymbols.length}
                      onClick={runRichnessScan}
                      title="Run premium richness check on every watchlist ticker. Each ticker uses 2 UW calls.">
                {richnessRunning
                  ? `Scanning. ${richnessProgress.done}/${richnessProgress.total}`
                  : (Object.keys(richnessScan).length > 0 ? "Re-scan" : `Scan ${filteredWatchlistSymbols.length}`)}
              </button>
            </div>
          </div>
          {richnessError && (
            <div className="research-error">Error: {richnessError}</div>
          )}
          {!filteredWatchlistSymbols.length && (
            <div className="research-empty">No tickers in watchlist. Click Manage in the sidebar.</div>
          )}
          {filteredWatchlistSymbols.length > 0 && Object.keys(richnessScan).length === 0 && !richnessRunning && (
            <div className="research-empty">
              Click <b>Scan</b> to check premium richness for {filteredWatchlistSymbols.length} {filteredWatchlistSymbols.length === 1 ? "ticker" : "tickers"}.
            </div>
          )}
          {Object.keys(richnessScan).length > 0 && (() => {
            const rows = filteredWatchlistSymbols
              .map(sym => richnessScan[sym] || {symbol: sym, pending: true})
              .filter(r => !r.error && !r.pending && r.data_available);
            const sortable = [...rows];
            const dir = richnessSort.dir === "asc" ? 1 : -1;
            sortable.sort((a, b) => {
              const av = (richnessSort.key === "symbol") ? a.symbol : (a[richnessSort.key] ?? a.stats?.[richnessSort.key]);
              const bv = (richnessSort.key === "symbol") ? b.symbol : (b[richnessSort.key] ?? b.stats?.[richnessSort.key]);
              if (av == null && bv == null) return 0;
              if (av == null) return 1;
              if (bv == null) return -1;
              if (typeof av === "string") return av.localeCompare(bv) * dir;
              return (av - bv) * dir;
            });
            const SortHeader = ({label, k, tip}) => {
              const active = richnessSort.key === k;
              const arrow = !active ? "" : richnessSort.dir === "desc" ? " ▾" : " ▴";
              return (
                <span className={`pb-th${active ? " active" : ""}`}
                      title={tip}
                      onClick={() => {
                        if (richnessSort.key === k) {
                          setRichnessSort({key: k, dir: richnessSort.dir === "desc" ? "asc" : "desc"});
                        } else {
                          setRichnessSort({key: k, dir: "desc"});
                        }
                      }}>
                  {label}{arrow}
                </span>
              );
            };
            const fmt = (v, suffix = "") => v == null ? "—" : v.toFixed(2) + suffix;
            return (
              <div className="richness-table">
                <div className="richness-head">
                  <SortHeader label="Ticker" k="symbol" tip="Click to sort alphabetically" />
                  <SortHeader label="Score" k="score" tip="Premium Richness Score 0-100. Higher = better conditions for selling premium. Click to sort." />
                  <SortHeader label="Verdict" k="verdict" tip="Plain-English read on whether premium is rich, fair, or thin." />
                  <SortHeader label="IV rank" k="iv_rank" tip="IV rank from UW (when available). High = premium rich vs the stock's own history." />
                  <SortHeader label="Volume" k="total_volume" tip="Today's total options volume from UW." />
                  <SortHeader label="vs Avg" k="rel_vol" tip="Today's volume divided by average. High relative volume usually means a catalyst is driving flow." />
                  <SortHeader label="P/C" k="put_call_ratio" tip="Put/call ratio. Extreme reads (≤0.5 or ≥2.0) signal one-sided positioning." />
                </div>
                {sortable.map(r => {
                  const cls = r.score >= 75 ? "rich" : r.score >= 60 ? "moderate" : r.score >= 40 ? "fair" : "thin";
                  const isActive = r.symbol === ticker;
                  const relVol = r.stats?.total_volume && r.stats?.avg_volume && r.stats.avg_volume > 0
                    ? r.stats.total_volume / r.stats.avg_volume : null;
                  return (
                    <div key={r.symbol}
                         className={`richness-row${isActive ? " is-active" : ""}`}
                         title={isActive ? `${r.symbol} is the active ticker` : `Click to switch dashboard to ${r.symbol}`}
                         onClick={() => { setTicker(r.symbol); setTickerInput(r.symbol); }}>
                      <span className="ticker-cell">{r.symbol}</span>
                      <span className={`richness-score ${cls}`}>{r.score}</span>
                      <span className={`richness-verdict ${r.verdict_class || ""}`}>{r.verdict}</span>
                      <span>{r.stats?.iv_rank != null ? fmtPct(r.stats.iv_rank) : (r.stats?.iv_percentile != null ? fmtPct(r.stats.iv_percentile) : "—")}</span>
                      <span>{fmtVol(r.stats?.total_volume)}</span>
                      <span className={relVol != null && relVol >= 1.5 ? "warn" : ""}>{relVol != null ? relVol.toFixed(2) + "×" : "—"}</span>
                      <span>{r.stats?.put_call_ratio != null ? fmt(r.stats.put_call_ratio) : "—"}</span>
                    </div>
                  );
                })}
                {sortable.length === 0 && (
                  <div className="research-empty">No tickers returned usable data. UW basic plan may not include all metrics for every symbol.</div>
                )}
              </div>
            );
          })()}
        </div>
        </CardErrorBoundary>
        )}
        {/* Watchlist scanner */}
        <CardErrorBoundary label="Watchlist scanner">
        {(() => {
          // Conviction score: 0-100 weighted blend reflecting how good
          // a premium-selling setup looks for the symbol *right now*.
          // Components and weights (sum to 1.0):
          //   IV richness (IV/HV)       — 30% — higher means options are
          //                                inflated relative to realized
          //                                vol; classic edge for sellers.
          //   IV rank                    — 25% — high IVR means current
          //                                IV is at the top of its 52w
          //                                range; further confirms rich.
          //   DTE quality                — 15% — sweet spot 30-45 DTE.
          //                                Falls off above 60 or below 14.
          //   Earnings proximity penalty — 15% — full points if >21d out.
          //                                Linear penalty inside 21d.
          //   Calm magnitude             — 15% — daily move <1.5% scores
          //                                full; falls off to 4%+.
          // Each component normalized to 0-1 then weighted-summed * 100.
          const convictionFor = (snap) => {
            if (!snap) return null;
            // 1) IV/HV richness
            const richness = snap.richness;
            let cRich = 0;
            if (richness != null) {
              if (richness >= 1.5) cRich = 1;
              else if (richness >= 0.9) cRich = (richness - 0.9) / 0.6;
              else cRich = 0;
            }
            // 2) IV rank — backend sends iv_rank as 0-100
            let cRank = 0;
            if (snap.iv_rank != null) cRank = Math.max(0, Math.min(1, snap.iv_rank / 100));
            // 3) DTE quality — peak at 35d, taper either side
            let cDte = 0;
            if (snap.dte_front != null) {
              const d = snap.dte_front;
              if (d >= 30 && d <= 45) cDte = 1;
              else if (d >= 14 && d < 30) cDte = (d - 14) / 16;
              else if (d > 45 && d <= 75) cDte = 1 - ((d - 45) / 30);
              else cDte = 0;
            }
            // 4) Earnings proximity — full points >21d, linear inside
            let cEarn = 1;
            if (snap.earnings_in_days != null) {
              const e = snap.earnings_in_days;
              if (e <= 0) cEarn = 0.3;       // post-earnings, IV crushed
              else if (e <= 21) cEarn = e / 21;
            }
            // 5) Calm — small daily move = stable. Use abs change_pct.
            let cCalm = 0;
            if (snap.change_pct != null) {
              const m = Math.abs(snap.change_pct);
              if (m <= 1.5) cCalm = 1;
              else if (m <= 4) cCalm = 1 - ((m - 1.5) / 2.5);
              else cCalm = 0;
            }
            const score = (cRich * 0.30) + (cRank * 0.25) + (cDte * 0.15)
                        + (cEarn * 0.15) + (cCalm * 0.15);
            return Math.round(score * 100);
          };
          const scored = filteredWatchlistSymbols.map(sym => {
            const snap = scanResults[sym];
            if (!snap) return { symbol: sym, snap: null, best: null, conviction: null };
            const best = scoreSnapshot(snap);
            const conviction = convictionFor(snap);
            return { symbol: sym, snap, best, conviction };
          });
          // Sort. Default: conviction desc with pending rows last.
          // User-overridden sort honors scanSort {key, dir}.
          const numKey = (row, key) => {
            if (!row.snap) return null;
            switch (key) {
              case "ticker":   return row.symbol;
              case "price":    return row.snap.price;
              case "change":   return row.snap.change_pct;
              case "iv30":     return row.snap.iv30_avg;
              case "hv20":     return row.snap.hv20;
              case "richness": return row.snap.richness;
              case "iv_rank":  return row.snap.iv_rank;
              case "earn":     return row.snap.earnings_in_days;
              case "strategy": return row.best?.label || "";
              case "conv":     return row.conviction;
              case "score":    return row.best?.score;
              default:         return null;
            }
          };
          const sorted = [...scored].sort((a, b) => {
            // Always pin pending rows to the bottom regardless of sort
            const aPending = !a.snap, bPending = !b.snap;
            if (aPending && !bPending) return 1;
            if (bPending && !aPending) return -1;
            if (aPending && bPending) return 0;
            if (scanSort) {
              const va = numKey(a, scanSort.key);
              const vb = numKey(b, scanSort.key);
              // null/undefined sink to bottom regardless of dir
              const aMissing = va == null, bMissing = vb == null;
              if (aMissing && bMissing) return 0;
              if (aMissing) return 1;
              if (bMissing) return -1;
              const cmp = typeof va === "string"
                ? va.localeCompare(vb)
                : (va - vb);
              return scanSort.dir === "desc" ? -cmp : cmp;
            }
            // Default: conviction descending
            const ca = a.conviction ?? -1, cb = b.conviction ?? -1;
            return cb - ca;
          });
          const haveAny = scored.some(r => r.snap);
          return (
            <div className="card" style={{marginBottom: "var(--row-gap)"}}>
              <div className="card-head">
                <div>
                  <div className="kicker">Multi-ticker · best setup right now per symbol</div>
                  <div className="card-title">Watchlist scanner</div>
                </div>
                <div className="scan-toolbar">
                  {/* Tag filter chip — clickable to clear */}
                  {watchlistTagFilter && (
                    <button className="scan-filter-chip"
                            onClick={() => setWatchlistTagFilter(null)}
                            title="Clear tag filter">
                      filter: {watchlistTagFilter} ✕
                    </button>
                  )}
                  {/* All-tags dropdown to apply a filter */}
                  {watchlistData.symbols.length > 0 && (() => {
                    const allTags = Array.from(new Set(
                      watchlistData.symbols.flatMap(s => s.tags || [])
                    )).sort();
                    if (allTags.length === 0) return null;
                    return (
                      <select
                        className="scan-tag-select"
                        value={watchlistTagFilter || ""}
                        onChange={e => setWatchlistTagFilter(e.target.value || null)}>
                        <option value="">All tags ({watchlistData.symbols.length})</option>
                        {allTags.map(t => {
                          const n = watchlistData.symbols.filter(s => (s.tags || []).includes(t)).length;
                          return <option key={t} value={t}>{t} ({n})</option>;
                        })}
                      </select>
                    );
                  })()}
                  {scanAt && (
                    <span className="scan-stale">scanned {Math.round((Date.now() - scanAt) / 60000)} min ago</span>
                  )}
                  <button className="scan-run-btn" disabled={scanRunning || !filteredWatchlistSymbols.length}
                          onClick={runScan}>
                    {scanRunning ? "Scanning." : haveAny ? "Re-scan" : "Run scan"}
                    {watchlistTagFilter && ` (${filteredWatchlistSymbols.length})`}
                  </button>
                </div>
              </div>
              {!filteredWatchlistSymbols.length && (
                <div className="scan-empty">
                  {watchlistData.symbols.length === 0
                    ? "No tickers in watchlist. Click Manage in the sidebar to add some."
                    : `No tickers match the "${watchlistTagFilter}" tag.`}
                </div>
              )}
              {filteredWatchlistSymbols.length > 0 && !haveAny && !scanRunning && (
                <div className="scan-empty">Click <b>Run scan</b> to score {filteredWatchlistSymbols.length} {filteredWatchlistSymbols.length === 1 ? "ticker" : "tickers"}. Allow ~1 second per symbol.</div>
              )}
              {haveAny && (
                <div className="scan-table-wrap">
                  <table className="scan-table">
                    <thead>
                      <tr>
                        <SortableTh label="Ticker" sortKey="ticker" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-sym" />
                        <SortableTh label="Price" sortKey="price" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-num" />
                        <SortableTh label="Δ Day" sortKey="change" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-num" />
                        <SortableTh label="IV30" sortKey="iv30" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-num" />
                        <SortableTh label="HV20" sortKey="hv20" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-num" />
                        <SortableTh label="IV/HV" sortKey="richness" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-num" />
                        <SortableTh label="IV Rk" sortKey="iv_rank" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-num" />
                        <SortableTh label="Earn" sortKey="earn" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-num" />
                        <SortableTh label="Best strategy" sortKey="strategy" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-strat" />
                        <SortableTh label="Conv" sortKey="conv" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-num" />
                        <SortableTh label="Score" sortKey="score" current={scanSort}
                                    onSort={k => setScanSort(prev => cycleSort(prev, k))}
                                    className="scan-th-num" />
                      </tr>
                    </thead>
                    <tbody>
                      {sorted.map(({symbol: sym, snap, best, conviction}) => {
                        if (!snap) {
                          return (
                            <tr key={sym} className="scan-row scan-row-pending"
                                onClick={() => { setTicker(sym); setTickerInput(sym); }}>
                              <td className="scan-sym">{sym}</td>
                              <td colSpan={10} className="scan-pending">click to load</td>
                            </tr>
                          );
                        }
                        if (snap.error) {
                          return (
                            <tr key={sym} className="scan-row scan-row-err"
                                onClick={() => { setTicker(sym); setTickerInput(sym); }}>
                              <td className="scan-sym">{sym}</td>
                              <td colSpan={10} className="scan-err">{snap.error}</td>
                            </tr>
                          );
                        }
                        const chg = snap.change_pct || 0;
                        const richness = snap.richness;
                        const richClass = richness == null ? "" : richness >= 1.2 ? "rich" : richness <= 0.95 ? "cheap" : "";
                        const ivRk = snap.iv_rank;
                        const ivRkDays = snap.iv_rank_days || 0;
                        const ivRkClass = ivRk == null ? "" : ivRk >= 70 ? "rich" : ivRk <= 30 ? "cheap" : "";
                        const ivRkTip = ivRk == null
                          ? (ivRkDays > 0
                              ? `Building IV history. ${ivRkDays} of 20 days collected before rank can be computed.`
                              : "No IV history yet. Rank is computed from local snapshots, growing as you scan.")
                          : `IV Rank from ${ivRkDays} days of local history. Current ATM IV30 is at the ${ivRk.toFixed(0)} percent mark of its observed range. IV Pct ${snap.iv_pct != null ? snap.iv_pct.toFixed(0) + "%" : "n/a"} of days had lower IV. Above 70 = rich, below 30 = cheap.`;
                        const earnD = snap.earnings_in_days;
                        const earnClass = earnD == null ? "" : earnD <= 7 ? "earn-urgent" : earnD <= 14 ? "earn-close" : "";
                        const scoreClass = !best ? "" : best.score >= 60 ? "score-hi" : best.score >= 45 ? "score-mid" : "score-lo";
                        const convClass = conviction == null ? "" : conviction >= 65 ? "score-hi" : conviction >= 45 ? "score-mid" : "score-lo";
                        return (
                          <tr key={sym} className={`scan-row ${sym === ticker ? "scan-row-active" : ""}`}
                              onClick={() => { setTicker(sym); setTickerInput(sym); }}
                              title={best ? best.reasons.join(" · ") : ""}>
                            <td className="scan-sym">{sym}</td>
                            <td className="scan-num">${(getLivePrice(sym) ?? snap.price).toFixed(2)}</td>
                            <td className={`scan-num ${chg >= 0 ? "up" : "down"}`}>{chg >= 0 ? "+" : ""}{chg.toFixed(2)}%</td>
                            <td className="scan-num">{snap.iv30_avg != null ? `${(snap.iv30_avg * 100).toFixed(0)}%` : "—"}</td>
                            <td className="scan-num">{snap.hv20 != null ? `${(snap.hv20 * 100).toFixed(0)}%` : "—"}</td>
                            <td className={`scan-num ${richClass}`}>{richness != null ? richness.toFixed(2) : "—"}</td>
                            <td className={`scan-num ${ivRkClass}`}
                                title={ivRkTip}>{ivRk != null ? `${ivRk.toFixed(0)}` : "—"}</td>
                            <td className={`scan-num ${earnClass}`}>{earnD != null ? `${earnD}d` : "—"}</td>
                            <td className="scan-strat">{best ? best.name : "—"}</td>
                            <td className={`scan-num scan-conv ${convClass}`}>{conviction != null ? conviction : "—"}</td>
                            <td className={`scan-num ${scoreClass}`}>{best ? best.score : "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {haveAny && (
                <div className="scan-disclaimer">
                  Click any row to switch the dashboard to that ticker. Score is a rough heuristic from snapshot data — load the full ticker for the real strategy ranking.
                </div>
              )}
            </div>
          );
        })()}
        </CardErrorBoundary>

        {/* Weekly Range Scanner — implied move + 0.20 delta strikes per
            symbol in the (filtered) watchlist. Helps pick OTM strikes
            for the week's call/put sales at a glance. */}
        <CardErrorBoundary label="Weekly range">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Implied move + 0.20 delta strikes · this week's expiration</div>
              <div className="card-title">Weekly range scanner</div>
            </div>
            <div className="research-controls">
              {weeklyRangeAt && (
                <span className="scan-stale">scanned {Math.round((Date.now() - weeklyRangeAt) / 60000)} min ago</span>
              )}
              <button className="research-run-btn"
                      disabled={weeklyRangeRunning || !filteredWatchlistSymbols.length}
                      onClick={runWeeklyRange}>
                {weeklyRangeRunning
                  ? `Scanning. ${weeklyRangeProgress.done}/${weeklyRangeProgress.total}`
                  : (Object.keys(weeklyRange).length > 0 ? "Re-scan" : `Scan ${filteredWatchlistSymbols.length}`)}
              </button>
            </div>
          </div>
          {weeklyRangeError && (
            <div className="research-error">Error: {weeklyRangeError}</div>
          )}
          {!filteredWatchlistSymbols.length && (
            <div className="research-empty">
              {watchlistData.symbols.length === 0
                ? "No tickers in watchlist. Click Manage in the sidebar."
                : `No tickers match the "${watchlistTagFilter}" tag.`}
            </div>
          )}
          {filteredWatchlistSymbols.length > 0 && Object.keys(weeklyRange).length === 0 && !weeklyRangeRunning && (
            <div className="research-empty">
              Click <b>Scan</b> to compute implied weekly range and 0.20 delta strikes for {filteredWatchlistSymbols.length} {filteredWatchlistSymbols.length === 1 ? "ticker" : "tickers"}. Allow ~1-3 seconds per symbol.
            </div>
          )}
          {Object.keys(weeklyRange).length > 0 && (() => {
            // Build sorted rows — symbols in current filter order, missing ones at bottom
            const baseRows = filteredWatchlistSymbols.map(sym => weeklyRange[sym] || {symbol: sym, pending: true});
            const wrKey = (row, key) => {
              if (row.pending || row.error) return null;
              switch (key) {
                case "ticker":      return row.symbol;
                case "spot":        return row.spot;
                case "move":        return row.implied_move_pct;
                case "low":         return row.implied_low;
                case "high":        return row.implied_high;
                case "put_strike":  return row.put_strike_20d;
                case "put_credit":  return row.put_credit_20d;
                case "call_strike": return row.call_strike_20d;
                case "call_credit": return row.call_credit_20d;
                case "total":       return row.total_credit_20d;
                default:            return null;
              }
            };
            const rows = [...baseRows].sort((a, b) => {
              // Pending and error rows sink to the bottom regardless of sort
              const aOut = a.pending || a.error;
              const bOut = b.pending || b.error;
              if (aOut && !bOut) return 1;
              if (bOut && !aOut) return -1;
              if (aOut && bOut) return 0;
              if (wrSort) {
                const va = wrKey(a, wrSort.key);
                const vb = wrKey(b, wrSort.key);
                const aMissing = va == null, bMissing = vb == null;
                if (aMissing && bMissing) return 0;
                if (aMissing) return 1;
                if (bMissing) return -1;
                const cmp = typeof va === "string"
                  ? va.localeCompare(vb)
                  : (va - vb);
                return wrSort.dir === "desc" ? -cmp : cmp;
              }
              return 0; // default = original watchlist order
            });
            return (
              <div className="wr-table-wrap">
                <table className="wr-table">
                  <thead>
                    <tr>
                      <SortableTh label="Ticker" sortKey="ticker" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))} />
                      <SortableTh label="Spot" sortKey="spot" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))}
                                  className="num" />
                      <SortableTh label="Implied move" sortKey="move" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))}
                                  className="num" />
                      <SortableTh label="Implied low" sortKey="low" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))}
                                  className="num" />
                      <SortableTh label="Implied high" sortKey="high" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))}
                                  className="num" />
                      <SortableTh label="Put strike (.20Δ)" sortKey="put_strike" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))}
                                  className="num" />
                      <SortableTh label="Put credit" sortKey="put_credit" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))}
                                  className="num" />
                      <SortableTh label="Call strike (.20Δ)" sortKey="call_strike" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))}
                                  className="num" />
                      <SortableTh label="Call credit" sortKey="call_credit" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))}
                                  className="num" />
                      <SortableTh label="Total credit" sortKey="total" current={wrSort}
                                  onSort={k => setWrSort(prev => cycleSort(prev, k))}
                                  className="num" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(row => {
                      if (row.pending) {
                        return (
                          <tr key={row.symbol} className="wr-row-pending">
                            <td><span className="wr-sym">{row.symbol}</span></td>
                            <td colSpan={9} className="wr-pending-cell">Queued.</td>
                          </tr>
                        );
                      }
                      if (row.error) {
                        return (
                          <tr key={row.symbol} className="wr-row-err">
                            <td>
                              <button className="wr-sym-btn"
                                      onClick={() => { setTicker(row.symbol); setTickerInput(row.symbol); }}>
                                {row.symbol}
                              </button>
                            </td>
                            <td colSpan={9} className="wr-err-cell">Error: {row.error}</td>
                          </tr>
                        );
                      }
                      return (
                        <tr key={row.symbol}>
                          <td>
                            <button className="wr-sym-btn"
                                    onClick={() => { setTicker(row.symbol); setTickerInput(row.symbol); }}
                                    title="Switch to this ticker">
                              {row.symbol}
                            </button>
                          </td>
                          <td className="num">${row.spot != null ? row.spot.toFixed(2) : "—"}</td>
                          <td className="num">{row.implied_move_pct != null ? `±${row.implied_move_pct.toFixed(2)}%` : "—"}</td>
                          <td className="num down">{row.implied_low != null ? `$${row.implied_low.toFixed(2)}` : "—"}</td>
                          <td className="num up">{row.implied_high != null ? `$${row.implied_high.toFixed(2)}` : "—"}</td>
                          <td className="num">{row.put_strike_20d != null ? `$${row.put_strike_20d.toFixed(0)}` : "—"}</td>
                          <td className="num">{row.put_credit_20d != null ? `$${row.put_credit_20d.toFixed(2)}` : "—"}</td>
                          <td className="num">{row.call_strike_20d != null ? `$${row.call_strike_20d.toFixed(0)}` : "—"}</td>
                          <td className="num">{row.call_credit_20d != null ? `$${row.call_credit_20d.toFixed(2)}` : "—"}</td>
                          <td className="num up">{row.total_credit_20d != null ? `$${row.total_credit_20d.toFixed(2)}` : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div className="research-disclaimer">
                  Implied move = ATM call + ATM put price (this week's expiration). High/low = spot ± straddle. Strike picks target 0.20 delta. Credits are mid prices, real fills 3-8% lower after spread.
                </div>
              </div>
            );
          })()}
        </div>
        </CardErrorBoundary>

        {/* Earnings IV crush ladder (#3) — for the past N earnings dates,
            compute synthetic implied move (HV20-based) vs realized move. */}
        <CardErrorBoundary label="Earnings ladder">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Past earnings · {ticker} · synthetic IV (HV20 proxy)</div>
              <div className="card-title">Earnings IV crush ladder</div>
            </div>
            <button className="research-run-btn"
                    disabled={earningsLoading}
                    onClick={async () => {
                      setEarningsLoading(true);
                      setEarningsError(null);
                      try {
                        const r = await apiFetch(`/api/earnings_ladder?symbol=${encodeURIComponent(ticker)}&n=8`);
                        if (!r.ok) throw new Error(`HTTP ${r.status}`);
                        const data = await r.json();
                        if (data.error) setEarningsError(data.error);
                        else setEarningsLadder(data);
                      } catch (e) {
                        setEarningsError(e.message || "Request failed");
                      } finally {
                        setEarningsLoading(false);
                      }
                    }}>
              {earningsLoading ? "Loading." : earningsLadder ? "Refresh" : "Run analysis"}
            </button>
          </div>
          {!earningsLadder && !earningsError && !earningsLoading && (
            <div className="research-empty">
              Click <b>Run analysis</b> to compute implied vs realized moves for the past 8 earnings reports on {ticker}.
            </div>
          )}
          {earningsError && (
            <div className="research-error">Error: {earningsError}</div>
          )}
          {earningsLadder && earningsLadder.events && earningsLadder.events.length > 0 && (
            <>
              <div className="ladder-summary">
                <div className="ladder-stat">
                  <div className="ladder-stat-lbl">Sellers won</div>
                  <div className="ladder-stat-val">
                    {earningsLadder.summary.sellers_pct}% <span className="ladder-stat-sub">of {earningsLadder.summary.n}</span>
                  </div>
                </div>
                <div className="ladder-stat">
                  <div className="ladder-stat-lbl">Avg implied move</div>
                  <div className="ladder-stat-val">{earningsLadder.summary.avg_implied}%</div>
                </div>
                <div className="ladder-stat">
                  <div className="ladder-stat-lbl">Avg realized move</div>
                  <div className="ladder-stat-val">{earningsLadder.summary.avg_realized}%</div>
                </div>
                <div className="ladder-stat">
                  <div className="ladder-stat-lbl">Avg edge (impl − real)</div>
                  <div className={`ladder-stat-val ${earningsLadder.summary.avg_edge >= 0 ? "up" : "down"}`}>
                    {earningsLadder.summary.avg_edge >= 0 ? "+" : ""}{earningsLadder.summary.avg_edge}%
                  </div>
                </div>
              </div>
              <table className="ladder-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th className="num">Spot</th>
                    <th className="num">IV proxy</th>
                    <th className="num">Implied move</th>
                    <th className="num">Realized move</th>
                    <th className="num">Edge</th>
                    <th>Winner</th>
                  </tr>
                </thead>
                <tbody>
                  {earningsLadder.events.map(e => (
                    <tr key={e.date} className={e.winner === "sellers" ? "ladder-row-sell" : "ladder-row-buy"}>
                      <td>{fmtUSDate(e.date)}</td>
                      <td className="num">${e.spot}</td>
                      <td className="num">{e.iv_proxy_pct}%</td>
                      <td className="num">{e.implied_move_pct}%</td>
                      <td className="num">{e.realized_move_pct}%</td>
                      <td className={`num ${e.edge_pct >= 0 ? "up" : "down"}`}>
                        {e.edge_pct >= 0 ? "+" : ""}{e.edge_pct}%
                      </td>
                      <td className="ladder-winner">
                        <span className={`ladder-pill ladder-pill-${e.winner}`}>
                          {e.winner === "sellers" ? "SELLERS" : "BUYERS"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="research-disclaimer">
                Synthetic IV uses HV20 as a proxy. Real IV typically runs 10-25% higher than HV (vol risk premium), so this analysis is conservatively biased — i.e. real-world sellers' edge is usually larger than shown here.
              </div>
            </>
          )}
          {earningsLadder && earningsLadder.events && earningsLadder.events.length === 0 && (
            <div className="research-empty">No past earnings events found in price history range.</div>
          )}
        </div>
        </CardErrorBoundary>

        {/* Walk-forward backtest (#5) — every Monday, "open" a strategy at
            synthetic strikes, hold to Friday, mark P/L. */}
        <CardErrorBoundary label="Backtest">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Walk-forward · {ticker} · weekly cycles · synthetic prices</div>
              <div className="card-title">Strategy backtest</div>
            </div>
            <div className="research-controls">
              <select className="research-select"
                      value={backtestStrategy}
                      onChange={e => setBacktestStrategy(e.target.value)}>
                <option value="covered_call">Covered Call</option>
                <option value="cash_secured_put">Cash-Secured Put</option>
                <option value="short_strangle">Short Strangle</option>
                <option value="iron_condor">Iron Condor</option>
                <option value="bull_put_spread">Bull Put Spread</option>
                <option value="jade_lizard">Jade Lizard</option>
                <option value="wheel">Wheel (CSP only)</option>
              </select>
              <select className="research-select"
                      value={backtestWeeks}
                      onChange={e => setBacktestWeeks(Number(e.target.value))}>
                <option value={26}>26 weeks</option>
                <option value={52}>52 weeks</option>
                <option value={104}>2 years</option>
              </select>
              <button className="research-run-btn"
                      disabled={backtestLoading}
                      onClick={async () => {
                        setBacktestLoading(true);
                        setBacktestError(null);
                        try {
                          const r = await apiFetch(`/api/backtest?symbol=${encodeURIComponent(ticker)}&strategy=${backtestStrategy}&weeks=${backtestWeeks}&delta=0.20`);
                          if (!r.ok) throw new Error(`HTTP ${r.status}`);
                          const data = await r.json();
                          if (data.error) setBacktestError(data.error);
                          else setBacktest(data);
                        } catch (e) {
                          setBacktestError(e.message || "Request failed");
                        } finally {
                          setBacktestLoading(false);
                        }
                      }}>
                {backtestLoading ? "Running." : backtest ? "Refresh" : "Run backtest"}
              </button>
            </div>
          </div>
          {!backtest && !backtestError && !backtestLoading && (
            <div className="research-empty">
              Pick a strategy and click <b>Run backtest</b>. Each Monday the system "opens" the strategy at 0.20 delta strikes, holds to Friday, and marks P/L from synthetic prices.
            </div>
          )}
          {backtestError && (
            <div className="research-error">Error: {backtestError}</div>
          )}
          {backtest && backtest.summary && backtest.trades && backtest.trades.length > 0 && (
            <>
              <div className="bt-summary">
                <div className="bt-stat">
                  <div className="bt-stat-lbl">Win rate</div>
                  <div className="bt-stat-val">
                    {backtest.summary.win_rate_pct}% <span className="bt-stat-sub">{backtest.summary.wins}/{backtest.summary.n_cycles}</span>
                  </div>
                </div>
                <div className="bt-stat">
                  <div className="bt-stat-lbl">Total P/L per share</div>
                  <div className={`bt-stat-val ${backtest.summary.total_pl >= 0 ? "up" : "down"}`}>
                    {backtest.summary.total_pl >= 0 ? "+" : ""}${backtest.summary.total_pl}
                  </div>
                </div>
                <div className="bt-stat">
                  <div className="bt-stat-lbl">Annualized return</div>
                  <div className={`bt-stat-val ${backtest.summary.annual_return_pct >= 0 ? "up" : "down"}`}>
                    {backtest.summary.annual_return_pct >= 0 ? "+" : ""}{backtest.summary.annual_return_pct}%
                  </div>
                </div>
                <div className="bt-stat">
                  <div className="bt-stat-lbl">Avg P/L per cycle</div>
                  <div className={`bt-stat-val ${backtest.summary.avg_pl >= 0 ? "up" : "down"}`}>
                    {backtest.summary.avg_pl >= 0 ? "+" : ""}${backtest.summary.avg_pl}
                  </div>
                </div>
                <div className="bt-stat">
                  <div className="bt-stat-lbl">Max drawdown</div>
                  <div className="bt-stat-val down">−${backtest.summary.max_drawdown}</div>
                </div>
                <div className="bt-stat">
                  <div className="bt-stat-lbl">Sharpe (approx)</div>
                  <div className="bt-stat-val">{backtest.summary.sharpe_approx}</div>
                </div>
              </div>
              {/* Cumulative P/L sparkline */}
              {(() => {
                const cum = [];
                let s = 0;
                for (const t of backtest.trades) { s += t.pl; cum.push(s); }
                if (cum.length < 2) return null;
                const W = 1100, H = 140, pL = 50, pR = 12, pT = 12, pB = 22;
                const innerW = W - pL - pR, innerH = H - pT - pB;
                const lo = Math.min(0, ...cum), hi = Math.max(0, ...cum);
                const span = (hi - lo) || 1;
                const xs = i => pL + (i / (cum.length - 1)) * innerW;
                const ys = v => pT + (1 - (v - lo) / span) * innerH;
                const zeroY = ys(0);
                const path = cum.map((v, i) => `${xs(i)},${ys(v)}`).join(" L ");
                const fillPath = `M ${xs(0)},${zeroY} L ${path} L ${xs(cum.length - 1)},${zeroY} Z`;
                const last = cum[cum.length - 1];
                return (
                  <div className="bt-chart-wrap">
                    <svg viewBox={`0 0 ${W} ${H}`} className="bt-chart">
                      <line x1={pL} x2={W - pR} y1={zeroY} y2={zeroY}
                            stroke={chartColors.fg3} strokeWidth="1" strokeDasharray="2 3" opacity="0.5" />
                      <path d={fillPath} fill={last >= 0 ? chartColors.up : chartColors.down} fillOpacity="0.18" />
                      <path d={`M ${path}`} fill="none" stroke={last >= 0 ? chartColors.up : chartColors.down} strokeWidth="2" />
                      <text x={pL - 6} y={ys(hi) + 4} fontSize="11" textAnchor="end"
                            fill={chartColors.fg3} fontFamily="ui-monospace, monospace">${hi.toFixed(0)}</text>
                      <text x={pL - 6} y={ys(lo) + 4} fontSize="11" textAnchor="end"
                            fill={chartColors.fg3} fontFamily="ui-monospace, monospace">${lo.toFixed(0)}</text>
                      <text x={pL - 6} y={zeroY + 4} fontSize="11" textAnchor="end"
                            fill={chartColors.fg3} fontFamily="ui-monospace, monospace">$0</text>
                      <text x={W - pR} y={H - pB + 14} fontSize="10" textAnchor="end"
                            fill={chartColors.fg3} fontFamily="ui-monospace, monospace">{backtest.trades[backtest.trades.length - 1].friday}</text>
                      <text x={pL} y={H - pB + 14} fontSize="10"
                            fill={chartColors.fg3} fontFamily="ui-monospace, monospace">{backtest.trades[0].monday}</text>
                    </svg>
                  </div>
                );
              })()}
              <details className="bt-trades-details">
                <summary>All {backtest.trades.length} weekly cycles</summary>
                <table className="bt-trades-table">
                  <thead>
                    <tr>
                      <th>Open</th>
                      <th>Close</th>
                      <th className="num">Spot open</th>
                      <th className="num">Spot close</th>
                      <th className="num">IV</th>
                      <th className="num">Credit</th>
                      <th className="num">Exp value</th>
                      <th className="num">P/L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {backtest.trades.map(t => (
                      <tr key={t.monday} className={t.win ? "bt-win" : "bt-loss"}>
                        <td>{t.monday}</td>
                        <td>{t.friday}</td>
                        <td className="num">${t.spot_open}</td>
                        <td className="num">${t.spot_close}</td>
                        <td className="num">{(t.iv * 100).toFixed(0)}%</td>
                        <td className={`num ${t.credit >= 0 ? "up" : "down"}`}>
                          {t.credit >= 0 ? "+" : ""}${t.credit}
                        </td>
                        <td className="num">${t.exp_value}</td>
                        <td className={`num ${t.pl >= 0 ? "up" : "down"}`}>
                          {t.pl >= 0 ? "+" : ""}${t.pl}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
              <div className="research-disclaimer">
                Backtest uses synthetic option prices (Black-Scholes from HV20) and assumes hold-to-expiration. Real-world P/L will be ~3-8% lower per cycle due to bid/ask spread + slippage. Use this for relative strategy comparison and methodology validation, not absolute P/L expectations.
              </div>
            </>
          )}
          {backtest && backtest.trades && backtest.trades.length === 0 && (
            <div className="research-empty">No valid weekly cycles in the selected window.</div>
          )}
        </div>
        </CardErrorBoundary>
        </TabPanel>

        {/* Dealer Gamma Exposure (GEX) */}
        <TabPanel tab="trade" active={activeTab}>
        <CardErrorBoundary label="Dealer Gamma Exposure">
        {(() => {
          if (!calls.length && !puts.length) return null;
          // Per-strike GEX in dollars-per-1%-move terms.
          // Convention used here (SqueezeMetrics-style): assume dealers
          // are SHORT calls and LONG puts (customers buy calls, sell puts).
          //   GEX(K)_call = +gamma * call_OI * 100 * spot * spot * 0.01
          //   GEX(K)_put  = -gamma * put_OI  * 100 * spot * spot * 0.01
          //   Total = sum of both
          // Positive total = dealers long gamma -> dampening / mean-reverting
          // Negative total = dealers short gamma -> amplifying / trending
          const allK = Array.from(new Set([...calls.map(c => c.strike), ...puts.map(p => p.strike)])).sort((a, b) => a - b);
          if (!allK.length) return null;
          const skey = s => (Math.round(s * 100) / 100).toFixed(2);
          const callMap = Object.fromEntries(calls.map(c => [skey(c.strike), c]));
          const putMap = Object.fromEntries(puts.map(p => [skey(p.strike), p]));
          const S = currentPrice;
          const gex = []; // {strike, value}
          let totalGex = 0;
          for (const K of allK) {
            const c = callMap[skey(K)];
            const p = putMap[skey(K)];
            const callContribution = c && c.gamma && c.openInterest
              ? c.gamma * c.openInterest * 100 * S * S * 0.01
              : 0;
            const putContribution = p && p.gamma && p.openInterest
              ? -p.gamma * p.openInterest * 100 * S * S * 0.01
              : 0;
            const v = callContribution + putContribution;
            gex.push({ strike: K, value: v });
            totalGex += v;
          }

          // Gamma flip — strike where cumulative GEX from low strikes
          // crosses zero. Below flip, dealers are net negative gamma
          // (vol-amplifying); above flip they're net positive (dampening).
          let cum = 0, flipStrike = null;
          for (let i = 0; i < gex.length; i++) {
            cum += gex[i].value;
            if (flipStrike == null && cum >= 0 && totalGex !== 0) {
              flipStrike = gex[i].strike;
              break;
            }
          }
          // If totalGex is positive but starts already positive, flip is below the chain.
          // If totalGex is negative, no flip in chain.

          // Top contributing strikes by abs magnitude.
          const topStrikes = [...gex].filter(g => g.value !== 0)
            .sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, 5);

          const fmtGex = v => {
            const abs = Math.abs(v);
            if (abs >= 1e9) return `${v >= 0 ? "+" : "-"}$${(abs / 1e9).toFixed(2)}B`;
            if (abs >= 1e6) return `${v >= 0 ? "+" : "-"}$${(abs / 1e6).toFixed(1)}M`;
            return `${v >= 0 ? "+" : "-"}$${(abs / 1e3).toFixed(0)}K`;
          };

          const aboveFlip = flipStrike != null && S >= flipStrike;
          const expDateLabel = activeExpDate.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"});
          const maxAbsGex = Math.max(...gex.map(g => Math.abs(g.value))) || 1;

          return (
            <div className="card" style={{marginBottom: "var(--row-gap)"}}>
              <div className="card-head">
                <div>
                  <div className="kicker">Dealer gamma exposure · for {expDateLabel}</div>
                  <div className="card-title">Where dealers hedge</div>
                </div>
                <div className="gex-controls">
                  <div className={`gex-total ${totalGex >= 0 ? "pos" : "neg"}`}>
                    <span className="gex-total-lbl">Net GEX</span>
                    <span className="gex-total-val">{fmtGex(totalGex)}</span>
                    <span className="gex-total-tag">/ 1% move</span>
                  </div>
                  <div className="gex-refresh">
                    <button className="gex-refresh-btn"
                            onClick={() => setDataVersion(v => v + 1)}
                            disabled={loading}
                            title="Refresh chain now">
                      {loading ? "…" : "↻"}
                    </button>
                    <select className="gex-auto-sel"
                            value={autoRefreshSec}
                            onChange={e => setAutoRefreshSec(+e.target.value)}
                            title="Auto-refresh during market hours">
                      <option value="0">manual</option>
                      <option value="60">auto 1m</option>
                      <option value="300">auto 5m</option>
                      <option value="900">auto 15m</option>
                    </select>
                    {lastFetched && (
                      <span className="gex-last">
                        {(() => {
                          const sec = Math.floor((Date.now() - lastFetched) / 1000);
                          if (sec < 60) return `${sec}s ago`;
                          if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
                          return `${Math.floor(sec / 3600)}h ago`;
                        })()}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="gex-grid">
                <div className="gex-col">
                  <div className="gex-col-head">Gamma flip</div>
                  {flipStrike != null ? (
                    <>
                      <div className="gex-flip-strike">${flipStrike.toFixed(2)}</div>
                      <div className={`gex-flip-side ${aboveFlip ? "above" : "below"}`}>
                        Spot ${S.toFixed(2)} is {aboveFlip ? "above" : "below"} flip
                      </div>
                      <div className="gex-flip-hint">
                        {aboveFlip
                          ? "Dealers long gamma here. Expect dampening / mean reversion."
                          : "Dealers short gamma here. Expect amplification / trending moves."}
                      </div>
                    </>
                  ) : (
                    <div className="gex-flip-side">No flip in this chain.</div>
                  )}
                </div>
                <div className="gex-col">
                  <div className="gex-col-head">Largest GEX strikes</div>
                  {topStrikes.map(g => {
                    const pct = (Math.abs(g.value) / maxAbsGex) * 100;
                    return (
                      <div key={`gx${g.strike}`} className="gex-row">
                        <span className="gex-strike">${g.strike.toFixed(2)}</span>
                        <span className="gex-bar-wrap">
                          <span className={`gex-bar ${g.value >= 0 ? "pos" : "neg"}`}
                                style={{width: `${pct}%`}}></span>
                        </span>
                        <span className={`gex-val ${g.value >= 0 ? "pos" : "neg"}`}>
                          {fmtGex(g.value)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="gex-disclaimer">
                Convention: dealers short calls, long puts (typical). GEX values are estimates.
              </div>
            </div>
          );
        })()}
        </CardErrorBoundary>

        {/* Volatility skew chart */}
        <CardErrorBoundary label="Volatility skew">
          <VolSkewCard
            calls={calls}
            puts={puts}
            currentPrice={currentPrice}
            ticker={ticker}
            sugCall={sugCall}
            sugPut={sugPut}
            activeExpDate={activeExpDate}
            chartColors={chartColors}
          />
        </CardErrorBoundary>

        {/* Net Greeks across selected strikes */}
        <CardErrorBoundary label="Net Greeks">
        {(() => {
          // Aggregate Greeks for the strikes Jerry currently has on screen
          // — assumes a short strangle on the primary call+put, plus long
          // wings if set via shift-click. Output is per contract pair so
          // multiplying by qty is straightforward downstream.
          const callPrimaryRow = callAtSug;
          const putPrimaryRow = putAtSug;
          const callWingRow = manualCallWing != null
            ? (calls.find(c => skey(c.strike) === skey(manualCallWing)) || null) : null;
          const putWingRow = manualPutWing != null
            ? (puts.find(p => skey(p.strike) === skey(manualPutWing)) || null) : null;

          const cd  = callPrimaryRow.delta || 0,  cg = callPrimaryRow.gamma || 0;
          const ct  = callPrimaryRow.theta || 0,  cv = callPrimaryRow.vega || 0;
          const pd  = putPrimaryRow.delta || 0,   pg = putPrimaryRow.gamma || 0;
          const pt  = putPrimaryRow.theta || 0,   pv = putPrimaryRow.vega || 0;
          // Short strangle: -1 contract on each side (per share basis)
          let netD = -cd - pd;
          let netG = -cg - pg;
          let netT = -ct - pt;
          let netV = -cv - pv;
          if (callWingRow) {
            netD += callWingRow.delta || 0;
            netG += callWingRow.gamma || 0;
            netT += callWingRow.theta || 0;
            netV += callWingRow.vega  || 0;
          }
          if (putWingRow) {
            netD += putWingRow.delta || 0;
            netG += putWingRow.gamma || 0;
            netT += putWingRow.theta || 0;
            netV += putWingRow.vega  || 0;
          }
          let layout;
          // Auto-detect what kind of structure the user has built.
          // Order matters — most specific first.
          const sameStrike = skey(sugCall) === skey(sugPut);
          if (callWingRow && putWingRow) {
            // Iron butterfly — body is a straddle (same-strike short).
            // Iron condor — body is a strangle (different short strikes).
            const callWidth = Math.abs(manualCallWing - sugCall);
            const putWidth = Math.abs(sugPut - manualPutWing);
            const symmetric = Math.abs(callWidth - putWidth) < 0.5;
            if (sameStrike) {
              layout = symmetric ? "Iron Butterfly" : "Iron Butterfly (asym)";
            } else {
              layout = symmetric ? "Iron Condor" : "Iron Condor (asym)";
            }
          } else if (callWingRow && !putWingRow) {
            // Long wing above short call — bear call credit spread.
            // Long wing below short call — bear call DEBIT spread (rare here).
            layout = manualCallWing > sugCall ? "Bear Call Spread" : "Bull Call Spread";
          } else if (!callWingRow && putWingRow) {
            // Long wing below short put — bull put credit spread.
            layout = manualPutWing < sugPut ? "Bull Put Spread" : "Bear Put Spread";
          } else if (sameStrike) {
            layout = "Short Straddle";
          } else {
            layout = "Short Strangle";
          }
          // Per-contract scaling (×100 shares per contract)
          const D100 = netD * 100, G100 = netG * 100, T100 = netT * 100, V100 = netV * 100;

          // Premium math for the setup
          const callMidPx = (callPrimaryRow.bid + callPrimaryRow.ask) / 2 || callPrimaryRow.last || 0;
          const putMidPx  = (putPrimaryRow.bid + putPrimaryRow.ask) / 2  || putPrimaryRow.last  || 0;
          let netCredit = (callMidPx + putMidPx);
          if (callWingRow) {
            const wm = (callWingRow.bid + callWingRow.ask) / 2 || callWingRow.last || 0;
            netCredit -= wm;
          }
          if (putWingRow) {
            const wm = (putWingRow.bid + putWingRow.ask) / 2 || putWingRow.last || 0;
            netCredit -= wm;
          }
          const netCreditDollars = netCredit * 100;

          return (
            <div className="card" style={{marginBottom: "var(--row-gap)"}}>
              <div className="card-head">
                <div>
                  <div className="kicker">Net Greeks · {layout}</div>
                  <div className="card-title">If you sell the selected strikes</div>
                </div>
                <div className="ng-credit">
                  <span className="ng-credit-lbl">Net credit</span>
                  <span className={`ng-credit-val ${netCredit >= 0 ? "up" : "down"}`}>
                    {netCredit >= 0 ? "+" : ""}${netCredit.toFixed(2)}
                  </span>
                  <span className="ng-credit-tag">×100 = ${netCreditDollars.toFixed(0)}/contract pair</span>
                </div>
              </div>
              <div className="ng-grid">
                <div className="ng-leg">
                  <div className="ng-leg-head"><span className="up">SHORT CALL</span></div>
                  <div className="ng-leg-line">${sugCall.toFixed(2)} · Δ {(callPrimaryRow.delta||0).toFixed(2)} · Θ {(callPrimaryRow.theta||0).toFixed(3)}</div>
                  {callWingRow && (
                    <>
                      <div className="ng-leg-head"><span className="up">+ LONG CALL WING</span></div>
                      <div className="ng-leg-line">${manualCallWing.toFixed(2)} · Δ {(callWingRow.delta||0).toFixed(2)} · Θ {(callWingRow.theta||0).toFixed(3)}</div>
                    </>
                  )}
                </div>
                <div className="ng-leg">
                  <div className="ng-leg-head"><span className="down">SHORT PUT</span></div>
                  <div className="ng-leg-line">${sugPut.toFixed(2)} · Δ {(putPrimaryRow.delta||0).toFixed(2)} · Θ {(putPrimaryRow.theta||0).toFixed(3)}</div>
                  {putWingRow && (
                    <>
                      <div className="ng-leg-head"><span className="down">+ LONG PUT WING</span></div>
                      <div className="ng-leg-line">${manualPutWing.toFixed(2)} · Δ {(putWingRow.delta||0).toFixed(2)} · Θ {(putWingRow.theta||0).toFixed(3)}</div>
                    </>
                  )}
                </div>
                <div className="ng-totals">
                  <div className="ng-totals-head">Per contract pair</div>
                  <div className="ng-tot-row">
                    <span className="ng-tot-lbl"><Term k="delta">Δ Delta</Term></span>
                    <span className={`ng-tot-val ${D100 >= 0 ? "up" : "down"}`}>{D100 >= 0 ? "+" : ""}{D100.toFixed(2)}</span>
                  </div>
                  <div className="ng-tot-row">
                    <span className="ng-tot-lbl"><Term k="gamma">Γ Gamma</Term></span>
                    <span className={`ng-tot-val ${G100 >= 0 ? "up" : "down"}`}>{G100 >= 0 ? "+" : ""}{G100.toFixed(3)}</span>
                  </div>
                  <div className="ng-tot-row">
                    <span className="ng-tot-lbl"><Term k="theta">Θ Theta</Term></span>
                    <span className={`ng-tot-val ${T100 >= 0 ? "up" : "down"}`}>{T100 >= 0 ? "+" : ""}{T100.toFixed(2)}/day</span>
                  </div>
                  <div className="ng-tot-row">
                    <span className="ng-tot-lbl"><Term k="vega">V Vega</Term></span>
                    <span className={`ng-tot-val ${V100 >= 0 ? "up" : "down"}`}>{V100 >= 0 ? "+" : ""}{V100.toFixed(2)}</span>
                  </div>
                </div>
              </div>
              <div className="ng-disclaimer">
                Convention: short = negative qty. Greeks per share scaled ×100 for contract pair.
                {(!callWingRow && !putWingRow) && " Shift+click a strike in the chain below to add a wing."}
              </div>
            </div>
          );
        })()}
        </CardErrorBoundary>

        {/* Theta vs gamma timing */}
        <CardErrorBoundary label="Theta vs gamma">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Theta vs gamma timing</div>
              <div className="card-title">When to sell this week</div>
            </div>
            <div className="seg">
              {[["call", "Call"], ["put", "Put"]].map(([v, l]) => (
                <button key={v} className={thetaSide === v ? "active" : ""}
                        onClick={() => setThetaSide(v)}>{l}</button>
              ))}
            </div>
          </div>
          <ThetaPanel
            rows={rows}
            sugCall={sugCall}
            sugPut={sugPut}
            callIv={callAtSug.iv}
            putIv={putAtSug.iv}
            side={thetaSide}
            currentPrice={currentPrice}
            baselinePrice={baselinePrice}
            expDate={activeExpDate}
            FRONT_DTE={FRONT_DTE}
            typicalHighDay={typicalHighDay}
            typicalLowDay={typicalLowDay}
            medianHigh={medianHigh}
            medianLow={medianLow}
            colors={chartColors}
          />
        </div>
        </CardErrorBoundary>

        {/* Roll suggestions */}
        <CardErrorBoundary label="Roll suggestions">
        {window.OptionStrats && window.OptionStrats.bsPrice && (() => {
          const bsPrice = window.OptionStrats.bsPrice;
          // Build a candidate next expiration ~7 trading days out from the
          // active expiration. We don't have a chain for it, so we estimate
          // premiums via Black-Scholes using current ATM IV as proxy.
          const nextExpDate = new Date(activeExpDate);
          nextExpDate.setDate(nextExpDate.getDate() + 7);
          const nextDte = Math.max(1, Math.round((nextExpDate - Date.now()) / 86400000));
          const nextExpLabel = nextExpDate.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"});
          const T = nextDte / 365.0;

          // Step size for "up" / "down" rolls. Use the average gap between
          // chain strikes as a sensible step.
          const stepCalls = (() => {
            const ks = calls.map(c => c.strike).sort((a, b) => a - b);
            if (ks.length < 2) return Math.max(0.5, currentPrice * 0.01);
            const gaps = [];
            for (let i = 1; i < ks.length; i++) gaps.push(ks[i] - ks[i - 1]);
            return Math.max(0.5, gaps.sort((a, b) => a - b)[Math.floor(gaps.length / 2)] || 1);
          })();

          const buildCallRolls = (currentStrike, currentIv) => {
            // Buy-back current short (debit), sell new short next week (credit)
            const buybackPrem = bsPrice(currentPrice, currentStrike, Math.max(0.5, FRONT_DTE) / 365, currentIv || 0.30, true);
            const make = (newK, label) => {
              const newPrem = bsPrice(currentPrice, newK, T, currentIv || 0.30, true);
              const credit = newPrem - buybackPrem;
              // Approximate new delta
              return {
                label, strike: newK,
                credit, newPrem,
                breakEven: newK + newPrem,
              };
            };
            return [
              make(currentStrike, "Roll out (same strike)"),
              make(currentStrike + stepCalls, "Roll out + up 1 step"),
              make(currentStrike - stepCalls, "Roll out + down 1 step"),
            ];
          };

          const buildPutRolls = (currentStrike, currentIv) => {
            const buybackPrem = bsPrice(currentPrice, currentStrike, Math.max(0.5, FRONT_DTE) / 365, currentIv || 0.30, false);
            const make = (newK, label) => {
              const newPrem = bsPrice(currentPrice, newK, T, currentIv || 0.30, false);
              const credit = newPrem - buybackPrem;
              return {
                label, strike: newK,
                credit, newPrem,
                breakEven: newK - newPrem,
              };
            };
            return [
              make(currentStrike, "Roll out (same strike)"),
              make(currentStrike - stepCalls, "Roll out + down 1 step"),
              make(currentStrike + stepCalls, "Roll out + up 1 step"),
            ];
          };

          const callRolls = buildCallRolls(sugCall, callAtSug.iv);
          const putRolls = buildPutRolls(sugPut, putAtSug.iv);
          const fmtCredit = v => v >= 0
            ? <span className="up">+${v.toFixed(2)} credit</span>
            : <span className="down">${v.toFixed(2)} debit</span>;

          return (
            <div className="card" style={{marginBottom: "var(--row-gap)"}}>
              <div className="card-head">
                <div>
                  <div className="kicker">Roll candidates · if you're already short the suggested strike</div>
                  <div className="card-title">Roll to {nextExpLabel} ({nextDte}d)</div>
                </div>
                <div className="card-sub">Estimated via Black-Scholes. Verify against the next expiration's actual chain.</div>
              </div>
              <div className="roll-grid">
                <div className="roll-side">
                  <div className="roll-side-head"><span className="up">SHORT CALL</span> rolls</div>
                  <div className="roll-side-now">
                    Currently short ${sugCall.toFixed(2)} call · IV {((callAtSug.iv || 0) * 100).toFixed(0)}%
                  </div>
                  {callRolls.map((r, i) => (
                    <div key={`cr${i}`} className="roll-row">
                      <div className="roll-label">{r.label}</div>
                      <div className="roll-meta">
                        <span className="roll-k">${r.strike.toFixed(2)}</span>
                        <span className="roll-prem">premium ${r.newPrem.toFixed(2)}</span>
                        <span className="roll-be">B/E ${r.breakEven.toFixed(2)}</span>
                      </div>
                      <div className="roll-credit">{fmtCredit(r.credit)}</div>
                    </div>
                  ))}
                </div>
                <div className="roll-side">
                  <div className="roll-side-head"><span className="down">SHORT PUT</span> rolls</div>
                  <div className="roll-side-now">
                    Currently short ${sugPut.toFixed(2)} put · IV {((putAtSug.iv || 0) * 100).toFixed(0)}%
                  </div>
                  {putRolls.map((r, i) => (
                    <div key={`pr${i}`} className="roll-row">
                      <div className="roll-label">{r.label}</div>
                      <div className="roll-meta">
                        <span className="roll-k">${r.strike.toFixed(2)}</span>
                        <span className="roll-prem">premium ${r.newPrem.toFixed(2)}</span>
                        <span className="roll-be">B/E ${r.breakEven.toFixed(2)}</span>
                      </div>
                      <div className="roll-credit">{fmtCredit(r.credit)}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          );
        })()}
        </CardErrorBoundary>

        {/* Position sizing — quick contract count calculator. Reads
            account size + max risk % from sizing config; reads max loss
            per contract from the currently selected strategy. */}
        <CardErrorBoundary label="Position sizing">
        {(() => {
          const acct = Number(sizingConfig.accountSize) || 0;
          const riskPct = Number(sizingConfig.maxRiskPct) || 0;
          const maxRiskDollars = acct * (riskPct / 100);
          // Compute per-share max loss from the active strategy's legs
          // by sweeping the P/L curve. If pnlBounds returns -Infinity
          // (undefined-risk strategy like naked strangle), we can't size
          // it — flag and skip the calculation.
          const O = window.OptionStrats;
          let perShareLoss = null;
          if (O && activeStrat?.legs && activeStrat.legs.length) {
            const lo = Math.max(0.5, currentPrice * 0.5);
            const hi = currentPrice * 1.5;
            const curve = O.pnlCurve(activeStrat.legs, lo, hi, 240);
            const b = O.pnlBounds(curve);
            if (Number.isFinite(b.min)) perShareLoss = b.min;
          }
          const perContractLoss = perShareLoss != null
            ? Math.abs(perShareLoss) * 100
            : null;
          const contracts = perContractLoss && perContractLoss > 0
            ? Math.floor(maxRiskDollars / perContractLoss)
            : null;
          const totalRiskAtCount = contracts ? contracts * perContractLoss : null;
          return (
            <div className="card sizing-card" style={{marginBottom: "var(--row-gap)"}}>
              <div className="card-head">
                <div>
                  <div className="kicker">Position sizing · {activeStrat?.name || "no strategy"}</div>
                  <div className="card-title">How many contracts to buy</div>
                </div>
                <div className="card-sub">Stays within your max risk per trade based on the strategy's defined loss.</div>
              </div>
              <div className="sizing-grid">
                <div className="sizing-input">
                  <label className="sizing-label">Account size</label>
                  <div className="sizing-input-wrap">
                    <span className="sizing-prefix">$</span>
                    <input type="number" min="0" step="1000"
                           value={sizingConfig.accountSize}
                           onChange={e => setSizingConfig(prev => ({...prev, accountSize: Number(e.target.value) || 0}))} />
                  </div>
                </div>
                <div className="sizing-input">
                  <label className="sizing-label">Max risk per trade</label>
                  <div className="sizing-input-wrap">
                    <input type="number" min="0.1" max="100" step="0.1"
                           value={sizingConfig.maxRiskPct}
                           onChange={e => setSizingConfig(prev => ({...prev, maxRiskPct: Number(e.target.value) || 0}))} />
                    <span className="sizing-suffix">%</span>
                  </div>
                </div>
                <div className="sizing-stat">
                  <div className="sizing-stat-lbl">Max risk dollars</div>
                  <div className="sizing-stat-val">${maxRiskDollars.toFixed(0)}</div>
                </div>
                <div className="sizing-stat">
                  <div className="sizing-stat-lbl">Risk per contract</div>
                  <div className="sizing-stat-val">
                    {perContractLoss != null
                      ? `$${perContractLoss.toFixed(0)}`
                      : <span className="sizing-undef">undefined</span>}
                  </div>
                </div>
                <div className="sizing-stat sizing-result">
                  <div className="sizing-stat-lbl">Suggested contracts</div>
                  <div className="sizing-stat-val sizing-result-val">
                    {contracts != null
                      ? (contracts > 0 ? contracts : "0 (risk too low)")
                      : "—"}
                  </div>
                </div>
                <div className="sizing-stat">
                  <div className="sizing-stat-lbl">Actual risk at this size</div>
                  <div className="sizing-stat-val">
                    {totalRiskAtCount != null && contracts > 0
                      ? `$${totalRiskAtCount.toFixed(0)} (${(totalRiskAtCount / acct * 100).toFixed(2)}%)`
                      : "—"}
                  </div>
                </div>
              </div>
              {perContractLoss == null && (
                <div className="sizing-warn">
                  This strategy ({activeStrat?.name}) has undefined max loss. Sizing requires a defined-risk strategy.
                </div>
              )}
            </div>
          );
        })()}
        </CardErrorBoundary>
        </TabPanel>

        {/* Open positions tracker. Shows every position Jerry has saved
            via "Open as position" in the builder tray, with current P/L
            (if the underlying is the active ticker, we use live price;
            otherwise we show entry-time snapshot). DTE remaining and an
            assignment-risk flag for short legs near the money. */}
        <TabPanel tab="manage" active={activeTab}>
        <CardErrorBoundary label="Open positions">
        {(() => {
          const O = window.OptionStrats;
          const open = positions.filter(p => p.status === "open");
          const closed = positions.filter(p => p.status === "closed");
          if (positions.length === 0) {
            return (
              <div className="card" style={{marginBottom: "var(--row-gap)"}}>
                <div className="card-head">
                  <div>
                    <div className="kicker">Tracked positions · 0 open</div>
                    <div className="card-title">Open positions</div>
                  </div>
                  <div className="card-sub">Build a strategy in the chain, click Open as position, and it shows up here.</div>
                </div>
                <div className="pos-empty">No positions yet.</div>
              </div>
            );
          }
          // Today's date for DTE calc.
          const today = new Date(); today.setHours(0, 0, 0, 0);
          const renderRow = (p) => {
            const expDate = p.expDate ? new Date(p.expDate) : null;
            const dte = expDate ? Math.max(0, Math.round((expDate - today) / (24 * 3600 * 1000))) : null;
            // Live P/L only meaningful if this position's ticker matches
            // the active dashboard ticker, since otherwise we don't have
            // current price for that symbol. Use live quote first (works
            // for any ticker, not just the active one), fall back to the
            // active-ticker payload price.
            let livePl = null;
            let livePrice = null;
            const liveForTicker = getLivePrice(p.ticker);
            if (O && p.legs?.length) {
              if (liveForTicker != null) {
                livePrice = liveForTicker;
              } else if (p.ticker === ticker) {
                livePrice = currentPrice;
              }
              if (livePrice != null) {
                livePl = O.pnlAt(p.legs, livePrice) * p.contracts;
              }
            }
            // Net credit/debit from entry — same regardless of current
            // price, since these are the cash flows at trade open.
            const netCredit = O ? O.netCredit(p.legs) * p.contracts : 0;
            // Assignment risk: any short leg whose strike is within
            // ~2% of current price (and ITM relative to short side).
            let assignFlag = false;
            if (livePrice) {
              for (const leg of p.legs) {
                if (leg.qty >= 0) continue;  // only short legs
                const itm = (leg.type === "call" && livePrice > leg.strike)
                         || (leg.type === "put"  && livePrice < leg.strike);
                const close = Math.abs(livePrice - leg.strike) / leg.strike < 0.02;
                if (itm || close) { assignFlag = true; break; }
              }
            }
            return (
              <div key={p.id} className={`pos-row ${p.status === "closed" ? "pos-closed" : ""}`}>
                <div className="pos-row-head">
                  <span className="pos-ticker">{p.ticker}</span>
                  <span className="pos-meta">
                    Opened {new Date(p.openedAt).toLocaleDateString("en-US", {month: "short", day: "numeric"})}
                    {p.contracts > 1 ? ` · ×${p.contracts}` : ""}
                  </span>
                  {dte != null && p.status === "open" && (
                    <span className={`pos-dte ${dte <= 7 ? "urgent" : dte <= 14 ? "close" : ""}`}>
                      {dte}d to exp
                    </span>
                  )}
                  {assignFlag && (
                    <span className="pos-flag-assign">⚠ Assignment risk</span>
                  )}
                  {p.status === "closed" && (
                    <span className="pos-status-closed">CLOSED</span>
                  )}
                </div>
                <div className="pos-legs">
                  {p.legs.map((leg, i) => {
                    const isLong = leg.qty > 0;
                    return (
                      <span key={i} className={`pos-leg ${isLong ? "long" : "short"}`}>
                        {isLong ? "+" : "−"}{leg.type[0].toUpperCase()} ${leg.strike.toFixed(2)} @ ${leg.premium.toFixed(2)}
                      </span>
                    );
                  })}
                </div>
                <div className="pos-stats">
                  <div className="pos-stat">
                    <div className="pos-stat-lbl">Entry credit/debit</div>
                    <div className={`pos-stat-val ${netCredit >= 0 ? "up" : "down"}`}>
                      {netCredit >= 0 ? "+" : ""}${netCredit.toFixed(2)}
                    </div>
                  </div>
                  <div className="pos-stat">
                    <div className="pos-stat-lbl">Entry price</div>
                    <div className="pos-stat-val">
                      {p.entryPrice != null ? `$${p.entryPrice.toFixed(2)}` : "—"}
                    </div>
                  </div>
                  {p.status === "open" ? (
                    <>
                      <div className="pos-stat">
                        <div className="pos-stat-lbl">Current price</div>
                        <div className="pos-stat-val">
                          {livePrice != null ? `$${livePrice.toFixed(2)}` : <span className="pos-note">switch ticker</span>}
                        </div>
                      </div>
                      <div className="pos-stat">
                        <div className="pos-stat-lbl">P/L at exp (current)</div>
                        <div className={`pos-stat-val ${livePl == null ? "" : livePl >= 0 ? "up" : "down"}`}>
                          {livePl != null
                            ? `${livePl >= 0 ? "+" : ""}$${livePl.toFixed(2)}`
                            : "—"}
                        </div>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="pos-stat">
                        <div className="pos-stat-lbl">Exit price</div>
                        <div className="pos-stat-val">
                          {p.exitPrice != null ? `$${p.exitPrice.toFixed(2)}` : "—"}
                        </div>
                      </div>
                      <div className="pos-stat">
                        <div className="pos-stat-lbl">Closed</div>
                        <div className="pos-stat-val">
                          {p.closedAt ? new Date(p.closedAt).toLocaleDateString("en-US", {month: "short", day: "numeric"}) : "—"}
                        </div>
                      </div>
                    </>
                  )}
                </div>
                <div className="pos-actions">
                  {p.ticker !== ticker && (
                    <button className="pos-btn pos-btn-ghost"
                            onClick={() => { setTicker(p.ticker); setTickerInput(p.ticker); }}>
                      Switch to {p.ticker}
                    </button>
                  )}
                  {p.status === "open" && (
                    <button className="pos-btn pos-btn-close"
                            onClick={() => {
                              const exitP = p.ticker === ticker ? currentPrice : null;
                              if (window.confirm(`Close this ${p.ticker} position?`)) {
                                closePosition(p.id, exitP);
                              }
                            }}>
                      Close position
                    </button>
                  )}
                  <button className="pos-btn pos-btn-del"
                          onClick={() => {
                            if (window.confirm("Delete this position permanently?")) {
                              deletePosition(p.id);
                            }
                          }}
                          title="Delete from history">
                    Delete
                  </button>
                </div>
              </div>
            );
          };
          return (
            <div className="card" style={{marginBottom: "var(--row-gap)"}}>
              <div className="card-head">
                <div>
                  <div className="kicker">Tracked positions · {open.length} open · {closed.length} closed</div>
                  <div className="card-title">Open positions</div>
                </div>
                <div className="card-sub">Live P/L shown when the active ticker matches.</div>
              </div>
              {open.length > 0 && (
                <div className="pos-list">
                  {open.map(renderRow)}
                </div>
              )}
              {closed.length > 0 && (
                <details className="pos-closed-block">
                  <summary>Closed positions ({closed.length})</summary>
                  <div className="pos-list">
                    {closed.map(renderRow)}
                  </div>
                </details>
              )}
            </div>
          );
        })()}
        </CardErrorBoundary>
        </TabPanel>

        {/* Strategies */}
        <TabPanel tab="trade" active={activeTab}>
        <CardErrorBoundary label="Strategies">
        <div className="card" style={{marginBottom: "var(--row-gap)"}}>
          <div className="card-head">
            <div>
              <div className="kicker">Strategy menu · {strategies.length} ways to play this setup</div>
              <div className="card-title">Suggested options strategies</div>
            </div>
            <div className="card-sub">All strikes derived from the suggested call and put. Premiums are mid market estimates.</div>
          </div>
          <div className="strat-grid">
            {strategies.map(s => (
              <StrategyCard
                key={s.key}
                rank={s.rank}
                score={s.score}
                reason={s.reason}
                tag={s.tag}
                name={s.name}
                termKey={s.key}
                structure={s.structure}
                stats={s.stats}
                note={s.note}
                tone={s.tone}
                legs={s.legs}
                frontExpLabel={expFrontLabel}
                backExpLabel={expBackLabel}
                frontDte={FRONT_DTE}
                selected={s.key === activeStrat?.key}
                onSelect={() => setSelectedStrategy(s.key)}
                Term={Term}
              />
            ))}
          </div>
        </div>

        {/* P/L diagram for the selected strategy */}
        {activeStrat && (
          <div className="card" style={{marginBottom: "var(--row-gap)"}}>
            <div className="card-head">
              <div>
                <div className="kicker">P/L at expiration · per share</div>
                <div className="card-title">{activeStrat.name} profile</div>
              </div>
              <div className="card-sub">
                {plNetCredit >= 0
                  ? <span><Term k="net_credit">Net credit</Term>: <b className="mono" style={{color: "var(--fg)"}}>${plNetCredit.toFixed(2)}/sh</b></span>
                  : <span><Term k="net_debit">Net debit</Term>: <b className="mono" style={{color: "var(--fg)"}}>${Math.abs(plNetCredit).toFixed(2)}/sh</b></span>}
              </div>
            </div>
            <PLChart
              legs={plLegs}
              currentPrice={currentPrice}
              expectedMove={expectedDollarMove}
              colors={chartColors}
              strategyName={activeStrat.name}
            />
            <div className="row two" style={{marginTop: 12, marginBottom: 0}}>
              <div className="spec-list">
                <span className="k"><Term k="max_profit">Max profit</Term></span>
                <span className="v" style={{color: "var(--up)"}}>
                  {Number.isFinite(plBounds.max) ? `${fmt$(plBounds.max)} / sh` : "unlimited"}
                </span>
                <span className="k"><Term k="max_loss">Max loss</Term></span>
                <span className="v" style={{color: "var(--down)"}}>
                  {activeStrat.definedRisk && Number.isFinite(plBounds.min) ? `${fmt$(plBounds.min)} / sh` : "undefined"}
                </span>
                {plBreakEvens.length > 0 && (
                  <>
                    <span className="k"><Term k="break_even">{plBreakEvens.length > 1 ? "Break evens" : "Break even"}</Term></span>
                    <span className="v">{plBreakEvens.map(b => `$${b.toFixed(2)}`).join(" · ")}</span>
                  </>
                )}
                {plBreakEvens.length >= 2 && (
                  <>
                    <span className="k"><Term k="profit_zone">Profit zone</Term></span>
                    <span className="v">${plBreakEvens[0].toFixed(2)} to ${plBreakEvens[plBreakEvens.length - 1].toFixed(2)}</span>
                  </>
                )}
              </div>
              <div className="muted" style={{fontSize: 12, lineHeight: 1.5}}>
                {activeStrat.note}
                {isBackMonthStrat && " Back month value is a Black Scholes estimate at front month expiry."}
              </div>
            </div>

            {/* Net Greeks for the strategy as a whole */}
            {netGreeks && (
              <div className="net-greeks">
                <div className="net-greeks-head">
                  <div className="net-greeks-title">Net position Greeks</div>
                  {netGreeks.partial && (
                    <div className="net-greeks-partial">
                      front leg only · back leg Greeks not loaded
                    </div>
                  )}
                </div>
                <div className="net-greeks-grid">
                  <div className="ng-cell">
                    <div className="ng-lbl"><Term k="delta">Net Delta</Term></div>
                    <div className={`ng-val ${netGreeks.delta >= 0 ? "up" : "down"}`}>
                      {netGreeks.delta >= 0 ? "+" : ""}{netGreeks.delta.toFixed(2)}
                    </div>
                    <div className="ng-sub">
                      {Math.abs(netGreeks.delta) < 0.10 ? "delta neutral"
                        : netGreeks.delta > 0 ? "long bias" : "short bias"}
                    </div>
                  </div>
                  <div className="ng-cell">
                    <div className="ng-lbl"><Term k="gamma">Net Gamma</Term></div>
                    <div className={`ng-val ${netGreeks.gamma >= 0 ? "up" : "down"}`}>
                      {netGreeks.gamma >= 0 ? "+" : ""}{netGreeks.gamma.toFixed(3)}
                    </div>
                    <div className="ng-sub">
                      {netGreeks.gamma >= 0 ? "long convexity" : "short convexity"}
                    </div>
                  </div>
                  <div className="ng-cell">
                    <div className="ng-lbl"><Term k="theta">Net Theta</Term></div>
                    <div className={`ng-val ${netGreeks.theta >= 0 ? "up" : "down"}`}>
                      {netGreeks.theta >= 0 ? "+" : ""}${netGreeks.theta.toFixed(2)}
                      <span className="ng-unit">/ day</span>
                    </div>
                    <div className="ng-sub">
                      {netGreeks.theta > 0 ? "collecting decay" : "paying decay"}
                    </div>
                  </div>
                  <div className="ng-cell">
                    <div className="ng-lbl"><Term k="vega">Net Vega</Term></div>
                    <div className={`ng-val ${netGreeks.vega >= 0 ? "up" : "down"}`}>
                      {netGreeks.vega >= 0 ? "+" : ""}${netGreeks.vega.toFixed(2)}
                      <span className="ng-unit">/ vol pt</span>
                    </div>
                    <div className="ng-sub">
                      {netGreeks.vega >= 0 ? "long vol" : "short vol"}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Options chain table — strikes around current price for the watched expiration */}
            {(calls.length > 0 || puts.length > 0) && (() => {
              // Build a unified strike grid centered on current price.
              const allStrikes = Array.from(new Set([...calls.map(c => c.strike), ...puts.map(p => p.strike)])).sort((a, b) => a - b);
              if (!allStrikes.length) return null;
              const atmIdx = allStrikes.reduce((bi, s, i) =>
                Math.abs(s - currentPrice) < Math.abs(allStrikes[bi] - currentPrice) ? i : bi, 0);
              const radius = 12;
              const lo = Math.max(0, atmIdx - radius);
              const hi = Math.min(allStrikes.length, atmIdx + radius + 1);
              const visible = allStrikes.slice(lo, hi);
              const skey = s => (Math.round(s * 100) / 100).toFixed(2);
              const callMap = Object.fromEntries(calls.map(c => [skey(c.strike), c]));
              const putMap = Object.fromEntries(puts.map(p => [skey(p.strike), p]));
              const fmtN = n => !n ? "—" : n >= 10000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString();
              const fmtDelta = d => (d == null || isNaN(d)) ? "—" : (d >= 0 ? "+" : "") + d.toFixed(2);
              const fmtTheta = t => (t == null || isNaN(t)) ? "—" : t.toFixed(2);
              const expDateLabel = activeExpDate.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"});
              return (
                <div className="oc-wrap">
                  <div className="oc-head">
                    <div className="oc-title">Options chain · {expDateLabel}</div>
                    <div className="oc-sub">{visible.length} strikes around ${currentPrice.toFixed(2)}</div>
                  </div>
                  {/* Custom strategy builder tray — shows current legs,
                      net debit/credit, and payoff visualization toggle. */}
                  <div className={`builder-tray ${customLegs.length ? "has-legs" : ""}`}>
                    <div className="builder-head">
                      <span className="builder-title">Strategy builder</span>
                      <span className="builder-sub">
                        {customLegs.length
                          ? `${customLegs.length} leg${customLegs.length === 1 ? "" : "s"} selected — click +/− buttons in the chain to add legs`
                          : "Click +C/−C/+P/−P next to any strike to start building"}
                      </span>
                    </div>
                    {customLegs.length > 0 && (
                      <>
                        <div className="builder-legs">
                          {customLegs.map((leg, i) => {
                            const isLong = leg.qty > 0;
                            const totalCost = (isLong ? -1 : 1) * Math.abs(leg.qty / 100) * leg.premium;
                            return (
                              <div key={i} className={`builder-leg ${isLong ? "long" : "short"}`}>
                                <span className="builder-leg-tag">{isLong ? "LONG" : "SHORT"}</span>
                                <span className="builder-leg-side">{leg.type.toUpperCase()}</span>
                                <span className="builder-leg-strike">${leg.strike.toFixed(2)}</span>
                                <span className="builder-leg-qty">×{Math.abs(leg.qty / 100)}</span>
                                <span className="builder-leg-prem">@ ${leg.premium.toFixed(2)}</span>
                                <span className={`builder-leg-cost ${totalCost >= 0 ? "credit" : "debit"}`}>
                                  {totalCost >= 0 ? "+" : ""}${totalCost.toFixed(2)}
                                </span>
                                <button className="builder-leg-flip"
                                        onClick={() => setCustomLegs(prev => prev.map((l, j) =>
                                          j === i ? { ...l, qty: -l.qty } : l))}
                                        title="Flip long/short">⇅</button>
                                <button className="builder-leg-rm"
                                        onClick={() => setCustomLegs(prev => prev.filter((_, j) => j !== i))}
                                        title="Remove leg">×</button>
                              </div>
                            );
                          })}
                        </div>
                        <div className="builder-actions">
                          {(() => {
                            const totalCost = customLegs.reduce((sum, l) =>
                              sum + ((l.qty > 0 ? -1 : 1) * Math.abs(l.qty / 100) * l.premium), 0);
                            return (
                              <span className={`builder-total ${totalCost >= 0 ? "credit" : "debit"}`}>
                                {totalCost >= 0 ? "Net credit: +" : "Net debit: "}${Math.abs(totalCost).toFixed(2)}
                              </span>
                            );
                          })()}
                          <div className="builder-btn-row">
                            <button className="builder-btn builder-btn-viz"
                                    onClick={() => setShowPayoff(v => !v)}>
                              {showPayoff ? "Hide P/L chart" : "Visualize P/L at expiration"}
                            </button>
                            <button className="builder-btn builder-btn-track"
                                    onClick={() => {
                                      openCurrentAsPosition(ticker, currentPrice, expFront, 1);
                                      setCustomLegs([]);
                                      setShowPayoff(false);
                                    }}
                                    title="Save these legs as a tracked open position">
                              Open as position
                            </button>
                            <button className="builder-btn builder-btn-clear"
                                    onClick={() => { setCustomLegs([]); setShowPayoff(false); }}>
                              Clear all
                            </button>
                          </div>
                        </div>
                        {showPayoff && (() => {
                          // Reuse the same PLChart component that renders for
                          // suggested strategies, plus the same stat layout
                          // underneath, so the builder visualization is
                          // visually identical to the top-of-page strategy
                          // P/L card. Compute net credit and break-evens
                          // off the user's leg list directly.
                          const O = window.OptionStrats;
                          if (!O) return null;
                          const lower = Math.max(0.5, currentPrice * 0.6);
                          const upper = currentPrice * 1.4;
                          const curve = O.pnlCurve(customLegs, lower, upper, 240);
                          const bounds = O.pnlBounds(curve);
                          const bes = O.breakEvens(curve);
                          const netCredit = O.netCredit(customLegs);
                          // Detect strategy name by best match to STRATEGIES
                          // entries — purely cosmetic for the title.
                          const expDollarMove = (callAtSug.iv && currentPrice && FRONT_DTE)
                            ? currentPrice * callAtSug.iv * Math.sqrt(FRONT_DTE / 365)
                            : null;
                          return (
                            <div className="card" style={{marginTop: 12, marginBottom: 0}}>
                              <div className="card-head">
                                <div>
                                  <div className="kicker">P/L at expiration · per share · custom build</div>
                                  <div className="card-title">Custom strategy profile</div>
                                </div>
                                <div className="card-sub">
                                  {netCredit >= 0
                                    ? <span><Term k="net_credit">Net credit</Term>: <b className="mono" style={{color: "var(--fg)"}}>${netCredit.toFixed(2)}/sh</b></span>
                                    : <span><Term k="net_debit">Net debit</Term>: <b className="mono" style={{color: "var(--fg)"}}>${Math.abs(netCredit).toFixed(2)}/sh</b></span>}
                                </div>
                              </div>
                              <PLChart
                                legs={customLegs}
                                currentPrice={currentPrice}
                                expectedMove={expDollarMove}
                                colors={chartColors}
                                strategyName="Custom"
                              />
                              <div className="row two" style={{marginTop: 12, marginBottom: 0}}>
                                <div className="spec-list">
                                  <span className="k"><Term k="max_profit">Max profit</Term></span>
                                  <span className="v" style={{color: "var(--up)"}}>
                                    {Number.isFinite(bounds.max) ? `$${bounds.max.toFixed(2)} / sh` : "unlimited"}
                                  </span>
                                  <span className="k"><Term k="max_loss">Max loss</Term></span>
                                  <span className="v" style={{color: "var(--down)"}}>
                                    {Number.isFinite(bounds.min) ? `$${bounds.min.toFixed(2)} / sh` : "undefined"}
                                  </span>
                                  {bes.length > 0 && (
                                    <>
                                      <span className="k"><Term k="break_even">{bes.length > 1 ? "Break evens" : "Break even"}</Term></span>
                                      <span className="v">{bes.map(b => `$${b.toFixed(2)}`).join(" · ")}</span>
                                    </>
                                  )}
                                  {bes.length >= 2 && (
                                    <>
                                      <span className="k"><Term k="profit_zone">Profit zone</Term></span>
                                      <span className="v">${bes[0].toFixed(2)} to ${bes[bes.length - 1].toFixed(2)}</span>
                                    </>
                                  )}
                                </div>
                                <div className="muted" style={{fontSize: 12, lineHeight: 1.5}}>
                                  P/L at expiration assumes all legs held to expiration. Pre-expiration P/L will differ due to extrinsic value remaining on the options.
                                </div>
                              </div>
                            </div>
                          );
                        })()}
                      </>
                    )}
                  </div>
                  <div className="oc-table-scroll">
                    <table className="oc-table">
                      <thead>
                        <tr>
                          <th className="oc-side oc-side-call">+/−</th>
                          <th colSpan="7" className="oc-side oc-side-call">CALLS</th>
                          <th className="oc-strike-h">STRIKE</th>
                          <th colSpan="7" className="oc-side oc-side-put">PUTS</th>
                          <th className="oc-side oc-side-put">+/−</th>
                        </tr>
                        <tr className="oc-sub-head">
                          <th></th>
                          <th>Bid</th><th>Ask</th><th>IV</th><th>Delta</th><th>Theta</th><th>Vol</th><th>OI</th>
                          <th></th>
                          <th>Bid</th><th>Ask</th><th>IV</th><th>Delta</th><th>Theta</th><th>Vol</th><th>OI</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {visible.map(strike => {
                          const k = skey(strike);
                          const c = callMap[k] || {};
                          const p = putMap[k] || {};
                          const isAtm = strike === allStrikes[atmIdx];
                          const itmCall = strike < currentPrice;
                          const itmPut = strike > currentPrice;
                          // FP-safe comparison via shared key — strikes
                          // can come back from yfinance with tiny floating
                          // point drift on either side of `===`.
                          const isCallSel = k === skey(sugCall);
                          const isPutSel = k === skey(sugPut);
                          const isCallWing = manualCallWing != null && k === skey(manualCallWing);
                          const isPutWing = manualPutWing != null && k === skey(manualPutWing);
                          // During drag, every strike between drag.start
                          // and drag.end (inclusive) gets a hover highlight.
                          const inDragRange = drag && (
                            (strike >= Math.min(drag.start, drag.end) && strike <= Math.max(drag.start, drag.end))
                          );
                          const isCallDrag = drag && drag.side === "call" && inDragRange;
                          const isPutDrag = drag && drag.side === "put" && inDragRange;
                          const startCallDrag = (e) => {
                            e.preventDefault();
                            setDrag({ side: "call", start: strike, end: strike });
                          };
                          const startPutDrag = (e) => {
                            e.preventDefault();
                            setDrag({ side: "put", start: strike, end: strike });
                          };
                          const moveCallDrag = () => {
                            if (drag && drag.side === "call" && drag.end !== strike) {
                              setDrag(d => ({ ...d, end: strike }));
                            }
                          };
                          const movePutDrag = () => {
                            if (drag && drag.side === "put" && drag.end !== strike) {
                              setDrag(d => ({ ...d, end: strike }));
                            }
                          };
                          const pickCall = (e) => {
                            // preventDefault blocks browser's text-selection
                            // extension on shift+click which can throw odd
                            // mouse events that confuse React's batching.
                            e.preventDefault();
                            e.stopPropagation();
                            if (e.shiftKey) setManualCallWing(prev => prev === strike ? null : strike);
                            else setManualCallStrike(strike);
                          };
                          const pickPut = (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            if (e.shiftKey) setManualPutWing(prev => prev === strike ? null : strike);
                            else setManualPutStrike(strike);
                          };
                          const callClass = () =>
                            `oc-cell oc-call-cell ${itmCall ? "itm" : ""} ${isCallSel ? "oc-sel-call" : ""} ${isCallWing ? "oc-wing-call" : ""} ${isCallDrag ? "oc-drag-call" : ""}`.trim();
                          const putClass = () =>
                            `oc-cell oc-put-cell ${itmPut ? "itm" : ""} ${isPutSel ? "oc-sel-put" : ""} ${isPutWing ? "oc-wing-put" : ""} ${isPutDrag ? "oc-drag-put" : ""}`.trim();
                          // Add legs to the custom builder. midOf gets
                          // the entry premium from current bid/ask. dir
                          // is +1 for long, -1 for short.
                          const midOfCell = (q) => {
                            if (!q) return 0;
                            if (q.bid > 0 && q.ask > 0) return (q.bid + q.ask) / 2;
                            return q.last || q.bid || q.ask || 0;
                          };
                          const addLeg = (side, dir) => {
                            const row = side === "call" ? c : p;
                            const premium = midOfCell(row);
                            if (!premium) return;
                            setCustomLegs(prev => [...prev, {
                              type: side, strike, qty: dir * 100, premium,
                            }]);
                          };
                          // Highlight if this strike already has a leg in the builder
                          const hasLegHere = customLegs.some(l => skey(l.strike) === k);
                          return (
                            <tr key={strike} className={`${isAtm ? "oc-atm" : ""} ${hasLegHere ? "oc-has-leg" : ""}`}>
                              <td className="oc-build-cell oc-build-call">
                                <button className="oc-build-btn oc-build-long"
                                        onClick={(e) => { e.stopPropagation(); addLeg("call", 1); }}
                                        title="Add long call to builder">+C</button>
                                <button className="oc-build-btn oc-build-short"
                                        onClick={(e) => { e.stopPropagation(); addLeg("call", -1); }}
                                        title="Add short call to builder">−C</button>
                              </td>
                              <td className={callClass()} onClick={pickCall} onMouseDown={startCallDrag} onMouseEnter={moveCallDrag} title="Click strike · drag to set spread">{c.bid != null ? `$${c.bid.toFixed(2)}` : "—"}</td>
                              <td className={callClass()} onClick={pickCall} onMouseDown={startCallDrag} onMouseEnter={moveCallDrag} title="Click strike · drag to set spread">{c.ask != null ? `$${c.ask.toFixed(2)}` : "—"}</td>
                              <td className={callClass()} onClick={pickCall} onMouseDown={startCallDrag} onMouseEnter={moveCallDrag} title="Click strike · drag to set spread">{c.iv ? `${(c.iv * 100).toFixed(0)}%` : "—"}</td>
                              <td className={callClass()} onClick={pickCall} onMouseDown={startCallDrag} onMouseEnter={moveCallDrag} title="Click strike · drag to set spread">{fmtDelta(c.delta)}</td>
                              <td className={callClass()} onClick={pickCall} onMouseDown={startCallDrag} onMouseEnter={moveCallDrag} title="Click strike · drag to set spread">{fmtTheta(c.theta)}</td>
                              <td className={callClass()} onClick={pickCall} onMouseDown={startCallDrag} onMouseEnter={moveCallDrag} title="Click strike · drag to set spread">{fmtN(c.volume)}</td>
                              <td className={callClass()} onClick={pickCall} onMouseDown={startCallDrag} onMouseEnter={moveCallDrag} title="Click strike · drag to set spread">{fmtN(c.openInterest)}</td>
                              <td className="oc-strike">${strike.toFixed(2)}</td>
                              <td className={putClass()} onClick={pickPut} onMouseDown={startPutDrag} onMouseEnter={movePutDrag} title="Click strike · drag to set spread">{p.bid != null ? `$${p.bid.toFixed(2)}` : "—"}</td>
                              <td className={putClass()} onClick={pickPut} onMouseDown={startPutDrag} onMouseEnter={movePutDrag} title="Click strike · drag to set spread">{p.ask != null ? `$${p.ask.toFixed(2)}` : "—"}</td>
                              <td className={putClass()} onClick={pickPut} onMouseDown={startPutDrag} onMouseEnter={movePutDrag} title="Click strike · drag to set spread">{p.iv ? `${(p.iv * 100).toFixed(0)}%` : "—"}</td>
                              <td className={putClass()} onClick={pickPut} onMouseDown={startPutDrag} onMouseEnter={movePutDrag} title="Click strike · drag to set spread">{fmtDelta(p.delta)}</td>
                              <td className={putClass()} onClick={pickPut} onMouseDown={startPutDrag} onMouseEnter={movePutDrag} title="Click strike · drag to set spread">{fmtTheta(p.theta)}</td>
                              <td className={putClass()} onClick={pickPut} onMouseDown={startPutDrag} onMouseEnter={movePutDrag} title="Click strike · drag to set spread">{fmtN(p.volume)}</td>
                              <td className={putClass()} onClick={pickPut} onMouseDown={startPutDrag} onMouseEnter={movePutDrag} title="Click strike · drag to set spread">{fmtN(p.openInterest)}</td>
                              <td className="oc-build-cell oc-build-put">
                                <button className="oc-build-btn oc-build-long"
                                        onClick={(e) => { e.stopPropagation(); addLeg("put", 1); }}
                                        title="Add long put to builder">+P</button>
                                <button className="oc-build-btn oc-build-short"
                                        onClick={(e) => { e.stopPropagation(); addLeg("put", -1); }}
                                        title="Add short put to builder">−P</button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="oc-legend">
                    <span className="oc-leg-item"><span className="oc-leg-swatch oc-leg-itm"></span> ITM shaded</span>
                    <span className="oc-leg-item"><span className="oc-leg-swatch oc-leg-atm"></span> ATM row highlighted</span>
                    <span className="oc-leg-item">Theta shown per calendar day</span>
                    <span className="oc-leg-item">Click to set strike · Shift+click for spread wing</span>
                    {(manualCallStrike != null || manualPutStrike != null || manualCallWing != null || manualPutWing != null) && (
                      <button className="oc-reset-btn"
                              onClick={() => {
                                setManualCallStrike(null); setManualPutStrike(null);
                                setManualCallWing(null); setManualPutWing(null);
                              }}>
                        Reset to auto
                      </button>
                    )}
                  </div>
                </div>
              );
            })()}
          </div>
        )}
        </CardErrorBoundary>
        </TabPanel>

        {/* Tweaks panel */}
        {Tweaks && (
          <Tweaks title="Tweaks">
            <TweakSection label="Color">
              <TweakSelect label="Accent" value={tweaks.values.accent} onChange={v => tweaks.setValue("accent", v)}
                options={Object.entries(ACCENT_PRESETS).map(([k, v]) => ({value: k, label: v.name}))} />
            </TweakSection>
            <TweakSection label="Typography">
              <TweakRadio label="Pairing" value={tweaks.values.typeface} onChange={v => tweaks.setValue("typeface", v)}
                options={[{value:"sans",label:"Sans"},{value:"serif",label:"Serif"},{value:"grotesk",label:"Grotesk"},{value:"mono-display",label:"Mono"}]} />
            </TweakSection>
            <TweakSection label="Density">
              <TweakRadio label="Spacing" value={tweaks.values.density} onChange={v => tweaks.setValue("density", v)}
                options={[{value:"compact",label:"Compact"},{value:"comfortable",label:"Standard"},{value:"full",label:"Full"}]} />
            </TweakSection>
            <TweakSection label="Charts">
              <TweakRadio label="Style" value={tweaks.values.chartStyle} onChange={v => tweaks.setValue("chartStyle", v)}
                options={[{value:"candles",label:"Candles"},{value:"area",label:"Area"},{value:"ohlc",label:"OHLC"}]} />
            </TweakSection>
            <TweakSection label="Layout">
              <TweakRadio label="Returns row" value={tweaks.values.layout} onChange={v => tweaks.setValue("layout", v)}
                options={[{value:"default",label:"Default"},{value:"swapped",label:"Swapped"}]} />
            </TweakSection>
          </Tweaks>
        )}
      </main>

      {/* Mobile bottom action bar — thumb-reachable status + quick nav. */}
      <nav className="mobile-bottombar" aria-label="Quick actions">
        <button className="mbb-btn" onClick={() => setNavOpen(true)} aria-label="Menu">
          <span className="mbb-ico">☰</span><span className="mbb-lbl">Menu</span>
        </button>
        <button className="mbb-status" onClick={() => setNavOpen(true)} aria-label="Switch ticker">
          <span className="mbb-sym">{ticker} <span className="mh-search-ico" aria-hidden="true">⌕</span></span>
          {!loadError && currentPrice != null && (
            <span className={`mbb-chg ${_mhChg >= 0 ? "up" : "down"}`}>
              ${Number(currentPrice).toFixed(2)} · {_mhChg >= 0 ? "+" : ""}{_mhChg.toFixed(2)}%
            </span>
          )}
        </button>
        <button className="mbb-btn" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} aria-label="Back to top">
          <span className="mbb-ico">↑</span><span className="mbb-lbl">Top</span>
        </button>
      </nav>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <RootErrorBoundary><App /></RootErrorBoundary>
);
