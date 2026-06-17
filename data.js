// Mock data generator for AAPL covered-call dashboard
// Produces realistic-looking weekly history + an options chain snapshot.

(function () {
  const PRESETS = {
    AAPL: { name: "Apple Inc.", current: 234.18, baseline: 229.40, vol: 0.018, drift: 0.0015, earningsThisWeek: false, sector: "Technology" },
    TSLA: { name: "Tesla, Inc.", current: 318.92, baseline: 310.05, vol: 0.045, drift: 0.0008, earningsThisWeek: true, sector: "Automotive" },
    NVDA: { name: "NVIDIA Corp.", current: 142.71, baseline: 138.04, vol: 0.034, drift: 0.0028, earningsThisWeek: false, sector: "Semiconductors" },
    SPY:  { name: "S&P 500 ETF",  current: 587.45, baseline: 583.10, vol: 0.011, drift: 0.0010, earningsThisWeek: false, sector: "Index ETF" },
    MSFT: { name: "Microsoft Corp.", current: 432.10, baseline: 428.55, vol: 0.016, drift: 0.0012, earningsThisWeek: false, sector: "Technology" },
    META: { name: "Meta Platforms", current: 612.30, baseline: 605.80, vol: 0.025, drift: 0.0019, earningsThisWeek: false, sector: "Technology" },
  };

  // seedable PRNG so the "mock" data is deterministic per ticker
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hashCode(s) { let h = 0; for (const c of s) h = ((h << 5) - h + c.charCodeAt(0)) | 0; return h >>> 0; }

  // Box-Muller normal
  function normal(rng) {
    let u = 0, v = 0;
    while (u === 0) u = rng();
    while (v === 0) v = rng();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  function buildWeekly(symbol, weeks) {
    const p = PRESETS[symbol] || PRESETS.AAPL;
    const rng = mulberry32(hashCode(symbol));
    const today = new Date();
    // most recent monday (this week's start)
    const dayIdx = today.getDay(); // 0=Sun..6=Sat
    const offset = (dayIdx + 6) % 7; // days since Monday
    const thisMon = new Date(today); thisMon.setDate(today.getDate() - offset); thisMon.setHours(0,0,0,0);

    // build N completed past weeks, oldest first
    const rows = [];
    let base = p.baseline / Math.pow(1 + p.drift, weeks); // walk price backwards then forward
    for (let i = weeks; i >= 1; i--) {
      const monday = new Date(thisMon); monday.setDate(thisMon.getDate() - i * 7);
      // simulate a week of intraday-ish moves
      const open = base;
      const dailyReturns = Array.from({ length: 5 }, () => normal(rng) * p.vol + p.drift / 5);
      let close = open, high = open, low = open;
      const dayHighs = [];
      const dayLows = [];
      const dayPriorCloses = [];
      // Mon's prior close is the prior week's Friday close. For week 0 we
      // use a synthetic gap of `open / (1 + small_drift)` so the math doesn't
      // explode. For subsequent weeks we'd track this across iterations, but
      // since each week is independent in the mock we approximate Mon's
      // prior close as `open` itself (no gap) — the mock is for UI sanity,
      // not statistical accuracy.
      let priorClose = open;
      for (const r of dailyReturns) {
        const intraVol = Math.abs(normal(rng)) * p.vol * 0.6;
        const dayOpen = close;
        const dayClose = dayOpen * (1 + r);
        const dayHigh = Math.max(dayOpen, dayClose) * (1 + intraVol);
        const dayLow = Math.min(dayOpen, dayClose) * (1 - Math.abs(normal(rng)) * p.vol * 0.6);
        dayHighs.push(dayHigh);
        dayLows.push(dayLow);
        dayPriorCloses.push(priorClose);
        if (dayHigh > high) high = dayHigh;
        if (dayLow < low) low = dayLow;
        close = dayClose;
        priorClose = dayClose;
      }
      const baseline = open;
      const high_return = (high / baseline - 1) * 100;
      const low_return = (low / baseline - 1) * 100;
      const close_return = (close / baseline - 1) * 100;
      const high_day = dayHighs.indexOf(Math.max(...dayHighs));
      const low_day = dayLows.indexOf(Math.min(...dayLows));
      const high_day_name = ["Mon","Tue","Wed","Thu","Fri"][high_day];
      const low_day_name = ["Mon","Tue","Wed","Thu","Fri"][low_day];
      const dayNames = ["Mon","Tue","Wed","Thu","Fri"];
      const day_breakdown = {};
      for (let di = 0; di < 5; di++) {
        const pc = dayPriorCloses[di];
        if (pc > 0) {
          day_breakdown[dayNames[di]] = {
            high: (dayHighs[di] / pc - 1) * 100,
            low: (dayLows[di] / pc - 1) * 100,
          };
        }
      }
      rows.push({
        week_start: monday,
        baseline, monday_open: open, friday_close: close,
        week_high: high, week_low: low,
        high_return, low_return, close_return,
        high_day, high_day_name, low_day, low_day_name,
        day_breakdown,
      });
      // next week base drifts up overall
      base = close * (1 + p.drift * 5 + normal(rng) * p.vol * 0.3);
    }

    // current week: partial — 3 days completed so far, current price applied
    const curMon = thisMon;
    const curBaseline = p.baseline;

    return { rows, current: { baseline: curBaseline, current: p.current, week_start: curMon, earnings: p.earningsThisWeek, name: p.name, sector: p.sector } };
  }

  function buildDaily(symbol, days = 90) {
    const p = PRESETS[symbol] || PRESETS.AAPL;
    const rng = mulberry32(hashCode(symbol + "_daily"));
    const today = new Date(); today.setHours(0,0,0,0);
    const out = [];
    // walk forward from days ago to today, ending at p.current
    let price = p.current / Math.pow(1 + p.drift, days);
    for (let i = days; i >= 0; i--) {
      const d = new Date(today); d.setDate(today.getDate() - i);
      // skip weekends
      const dow = d.getDay();
      if (dow === 0 || dow === 6) continue;
      const r = normal(rng) * p.vol + p.drift;
      const open = price;
      const close = open * (1 + r);
      const high = Math.max(open, close) * (1 + Math.abs(normal(rng)) * p.vol * 0.5);
      const low = Math.min(open, close) * (1 - Math.abs(normal(rng)) * p.vol * 0.5);
      out.push({ date: d, open, high, low, close });
      price = close;
    }
    // force last close to equal p.current for consistency
    if (out.length) {
      const last = out[out.length - 1];
      const adj = p.current / last.close;
      last.close = p.current;
      last.high = Math.max(last.high * adj, p.current);
      last.low = Math.min(last.low * adj, p.current);
    }
    return out;
  }

  function buildOptionChain(symbol, currentPrice) {
    const p = PRESETS[symbol] || PRESETS.AAPL;
    // strike increment
    const inc = currentPrice < 25 ? 0.5 : currentPrice < 200 ? 1.0 : 5.0;
    const atm = Math.round(currentPrice / inc) * inc;
    const calls = [];
    const puts = [];
    for (let k = -8; k <= 8; k++) {
      const strike = atm + k * inc;
      // simple BS-ish premium proxy: ATM≈stock*vol*sqrt(T), decay with distance
      const T = 5 / 252; // 1 trading week
      const sigma = p.vol * Math.sqrt(252) * 0.85; // annualised IV proxy
      const moneyness = Math.abs(strike - currentPrice) / currentPrice;
      const baseATM = currentPrice * sigma * Math.sqrt(T) * 0.4;
      const decay = Math.exp(-moneyness / (sigma * Math.sqrt(T) * 0.9));
      const callIntrinsic = Math.max(currentPrice - strike, 0);
      const putIntrinsic = Math.max(strike - currentPrice, 0);
      const callPrem = Math.max(callIntrinsic + baseATM * decay * (strike >= currentPrice ? 1 : 0.6), 0.02);
      const putPrem = Math.max(putIntrinsic + baseATM * decay * (strike <= currentPrice ? 1 : 0.6), 0.02);
      const ivCall = sigma * (1 + (strike - currentPrice) / currentPrice * 0.4);
      const ivPut = sigma * (1 - (strike - currentPrice) / currentPrice * 0.4);
      calls.push({
        strike,
        bid: callPrem * 0.97, ask: callPrem * 1.03, last: callPrem,
        volume: Math.round(2000 * decay + 50),
        openInterest: Math.round(8000 * decay + 200),
        iv: ivCall,
        delta: 0.5 + (currentPrice - strike) / (currentPrice * sigma * Math.sqrt(T) * 2.5),
      });
      puts.push({
        strike,
        bid: putPrem * 0.97, ask: putPrem * 1.03, last: putPrem,
        volume: Math.round(2000 * decay + 50),
        openInterest: Math.round(8000 * decay + 200),
        iv: ivPut,
        delta: -0.5 + (currentPrice - strike) / (currentPrice * sigma * Math.sqrt(T) * 2.5),
      });
    }
    return { calls, puts, atm };
  }

  function nextFriday(from = new Date()) {
    const d = new Date(from);
    const dow = d.getDay();
    const offset = ((5 - dow) + 7) % 7 || 7;
    d.setDate(d.getDate() + offset);
    d.setHours(16, 0, 0, 0);
    return d;
  }

  function median(arr) {
    if (!arr.length) return 0;
    const s = [...arr].sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function mode(arr) {
    const c = new Map();
    for (const v of arr) c.set(v, (c.get(v) || 0) + 1);
    let best = null, bc = -1;
    for (const [k, n] of c) if (n > bc) { best = k; bc = n; }
    return best;
  }

  function roundStrike(px) {
    if (px <= 0) return 0;
    const inc = px < 25 ? 0.5 : px < 200 ? 1.0 : 5.0;
    return Math.round(px / inc) * inc;
  }

  window.MockData = {
    PRESETS,
    buildWeekly,
    buildDaily,
    buildOptionChain,
    nextFriday,
    median,
    mode,
    roundStrike,
  };

  // ───────────────────────────────────────────────────────────────────────
  //  Live bootstrap — replaces mock data with a real Yahoo payload from the
  //  /api/ticker endpoint served by `python3 options_dashboard.py --serve`.
  //  When the page is opened directly (no Python), this no-ops and the mock
  //  presets above are used instead.
  // ───────────────────────────────────────────────────────────────────────
  const LIVE_CACHE = {};

  function hydrateRows(rows) {
    return rows.map(r => Object.assign({}, r, { week_start: new Date(r.week_start) }));
  }
  function hydrateDaily(daily) {
    return daily.map(d => Object.assign({}, d, { date: new Date(d.date) }));
  }

  function bootstrapLive(payload) {
    const sym = payload.ticker;
    const rows = hydrateRows(payload.rows);
    const daily = hydrateDaily(payload.daily);
    const cur = Object.assign({}, payload.current, {
      week_start: new Date(payload.current.week_start),
    });
    LIVE_CACHE[sym] = {
      rows, daily, current: cur,
      chain: payload.chain,
      expiration: payload.expiration,
      expirations: Array.isArray(payload.expirations) ? payload.expirations.slice() : [],
      earningsHistory: payload.earningsHistory && typeof payload.earningsHistory === "object"
        ? { past: Array.isArray(payload.earningsHistory.past) ? payload.earningsHistory.past.slice() : [],
            next: payload.earningsHistory.next || null }
        : { past: [], next: null },
    };

    // Override mock presets with the live preset for this ticker.
    PRESETS[sym] = {
      name: cur.name || sym,
      current: cur.current,
      baseline: cur.baseline,
      vol: 0.02,
      drift: 0.001,
      earningsThisWeek: !!cur.earnings,
      sector: cur.sector || "",
    };

    // Replace builders so the rest of the app reads live data for this ticker.
    const origWeekly = window.MockData.buildWeekly;
    const origDaily  = window.MockData.buildDaily;
    const origChain  = window.MockData.buildOptionChain;
    const origNext   = window.MockData.nextFriday;

    window.MockData.buildWeekly = function (symbol, weeks) {
      const e = LIVE_CACHE[symbol];
      if (e) {
        // Defensive slice. Server already returns head(weeks), but if weeks
        // shrinks (slider) and a refetch is in flight, slicing keeps math
        // and labels in sync until the new payload lands.
        const r = (weeks && e.rows.length > weeks) ? e.rows.slice(0, weeks) : e.rows;
        return { rows: r, current: e.current };
      }
      return origWeekly(symbol, weeks);
    };
    window.MockData.buildDaily = function (symbol, days) {
      const e = LIVE_CACHE[symbol];
      if (e) return e.daily;
      return origDaily(symbol, days);
    };
    window.MockData.buildOptionChain = function (symbol, currentPrice) {
      const e = LIVE_CACHE[symbol];
      if (e) return e.chain;
      return origChain(symbol, currentPrice);
    };
    window.MockData.nextFriday = function () {
      const e = LIVE_CACHE[(window.__LIVE && window.__LIVE.ticker) || Object.keys(LIVE_CACHE)[0]];
      if (e && e.expiration) return new Date(e.expiration + "T16:00:00");
      return origNext();
    };

    window.__LIVE = payload;
    const banner = document.getElementById("__live_banner");
    if (banner) {
      banner.textContent = "● LIVE · " + sym + " · " + payload.fetchedAt + " · baseline: " + payload.baselineMode;
      banner.style.display = "block";
    }
  }

  window.__bootstrapLive = bootstrapLive;
  window.__installLive = bootstrapLive;
  window.MockData.getLiveExpirations = function (sym) {
    const e = LIVE_CACHE[sym];
    return e && Array.isArray(e.expirations) ? e.expirations : [];
  };
  window.MockData.getLiveEarnings = function (sym) {
    const e = LIVE_CACHE[sym];
    return e && e.earningsHistory ? e.earningsHistory : { past: [], next: null };
  };
})();
