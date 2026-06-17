(function () {
// charts.jsx
// SVG charts. PriceChart, ReturnsChart (bars), DayBarChart, PLChart (generic legs).

const {
  useMemo,
  useState,
  useRef,
  useEffect
} = React;

// ── helpers ────────────────────────────────────────────────────────────────
function fmt$(v, d = 2) {
  return "$" + (v >= 0 ? v.toFixed(d) : "-" + (-v).toFixed(d));
}
function fmtPct(v, d = 2) {
  return (v >= 0 ? "+" : "") + v.toFixed(d) + "%";
}
function fmtDate(d, opts = {
  month: "short",
  day: "numeric"
}) {
  return d.toLocaleDateString("en-US", opts);
}
function niceTicks(min, max, target = 6) {
  const range = max - min;
  if (range <= 0) return [min];
  const rough = range / target;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const start = Math.ceil(min / step) * step;
  const out = [];
  for (let v = start; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

// ── Candlestick / Area / OHLC ──────────────────────────────────────────────
function PriceChart({
  daily,
  expHigh,
  expLow,
  callStrike,
  putStrike,
  currentPrice,
  chartStyle = "candles",
  colors,
  earnings,
  showMA50 = false,
  showMA200 = false,
  showEMA21 = false,
  showRSI = false,
  showProbCone = false,
  ivAnnualized = null,
  dteToExp = null,
  fullDailyLength = null,
  visibleStart = 0,
  onViewRangeChange = null
}) {
  const W = 1200;
  // RSI pane is conditional — grow the SVG when active instead of
  // compressing the price pane.
  const rsiH = showRSI ? 90 : 0;
  const rsiGap = showRSI ? 8 : 0;
  const H = 593 + rsiH + rsiGap;
  const padL = 56,
    padR = 16,
    padT = 28,
    padB = 36;
  const innerW = W - padL - padR,
    innerH = H - padT - padB;
  const gapH = 8;
  const macdH = 163;
  const priceH = innerH - macdH - gapH - rsiH - rsiGap;
  const macdTop = padT + priceH + gapH;
  const rsiTop = macdTop + macdH + rsiGap;
  const [hover, setHover] = useState(null);
  // Clear stale hover whenever the visible window changes. After a
  // zoom-in or pan, the previous hover index may no longer point to a
  // valid bar, which would crash on daily[hover].close. Reset to null
  // so the user has to re-hover.
  useEffect(() => {
    setHover(null);
  }, [daily.length, daily[0]?.date]);
  const svgRef = useRef(null);
  const [pan, setPan] = useState(null); // {startClientX, startStart, startEnd}

  // Convert a screen pixel X to a fractional bar index in the visible
  // window. The SVG uses viewBox="0 0 W H" so we scale pixels back to
  // viewBox space, subtract the left padding, then divide by the bar
  // pitch.
  const pixelToFrac = clientX => {
    if (!svgRef.current || !daily.length) return 0;
    const rect = svgRef.current.getBoundingClientRect();
    const vbX = (clientX - rect.left) / rect.width * W;
    const frac = (vbX - padL) / innerW;
    return Math.max(0, Math.min(1, frac));
  };

  // Wheel handler — zoom anchored on cursor. Scrolling up zooms in,
  // scrolling down zooms out. We compute the absolute index in the full
  // daily series under the cursor, then resize the window so that index
  // stays at the same screen position after zoom.
  // Note: React attaches wheel listeners as passive by default, which
  // means preventDefault inside the handler is ignored and the page
  // still scrolls. We register the wheel listener manually with
  // {passive: false} so we can actually block page scroll while zooming.
  const onWheelImpl = e => {
    if (!onViewRangeChange || !fullDailyLength || !daily.length) return;
    e.preventDefault();
    const cursorFrac = pixelToFrac(e.clientX);
    const visSize = daily.length;
    const absIdxAtCursor = visibleStart + cursorFrac * visSize;
    const stepRaw = Math.exp(-e.deltaY * 0.0015);
    const factor = Math.max(0.5, Math.min(2.0, stepRaw));
    let newSize = visSize * factor;
    newSize = Math.max(10, Math.min(fullDailyLength, newSize));
    let newStart = absIdxAtCursor - cursorFrac * newSize;
    let newEnd = newStart + newSize;
    if (newStart < 0) {
      newEnd -= newStart;
      newStart = 0;
    }
    if (newEnd > fullDailyLength) {
      newStart -= newEnd - fullDailyLength;
      newEnd = fullDailyLength;
    }
    newStart = Math.max(0, Math.round(newStart));
    newEnd = Math.min(fullDailyLength, Math.round(newEnd));
    if (newEnd - newStart < 10) return;
    onViewRangeChange([newStart, newEnd]);
  };
  useEffect(() => {
    const node = svgRef.current;
    if (!node) return;
    node.addEventListener("wheel", onWheelImpl, {
      passive: false
    });
    return () => node.removeEventListener("wheel", onWheelImpl);
  });

  // Pan: mousedown begins drag, global mousemove/mouseup track and
  // commit. We capture the starting visible range so deltas are
  // computed against that anchor. Shift+click is preserved for chain
  // wing selection so we explicitly opt out when shift is held.
  const onChartMouseDown = e => {
    if (!onViewRangeChange || !fullDailyLength || !daily.length) return;
    if (e.shiftKey) return;
    if (e.button !== 0) return;
    e.preventDefault();
    setPan({
      startClientX: e.clientX,
      startStart: visibleStart,
      startEnd: visibleStart + daily.length
    });
  };
  useEffect(() => {
    if (!pan) return;
    const onMove = e => {
      if (!svgRef.current) return;
      const rect = svgRef.current.getBoundingClientRect();
      const deltaPx = e.clientX - pan.startClientX;
      // Convert pixel delta to bar delta. Drag right = scroll back in
      // time (older bars come into view), so we negate.
      const pxPerBar = rect.width * (innerW / W) / Math.max(1, pan.startEnd - pan.startStart);
      const deltaBars = -deltaPx / pxPerBar;
      let newStart = pan.startStart + deltaBars;
      let newEnd = pan.startEnd + deltaBars;
      if (newStart < 0) {
        newEnd -= newStart;
        newStart = 0;
      }
      if (newEnd > fullDailyLength) {
        newStart -= newEnd - fullDailyLength;
        newEnd = fullDailyLength;
      }
      onViewRangeChange([Math.round(Math.max(0, newStart)), Math.round(Math.min(fullDailyLength, newEnd))]);
    };
    const onUp = () => setPan(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [pan, fullDailyLength, onViewRangeChange]);

  // Reserve right-side projection space when the prob cone is on so the
  // ±1σ/±2σ fan has somewhere to actually fan into. Without this reserve,
  // xScale(lastIdx) lands at the chart's right edge and the cone has
  // zero width — it draws as a single vertical line and looks invisible.
  const projW = showProbCone && ivAnnualized && dteToExp > 0 ? innerW * 0.18 : 0;
  const dataW = innerW - projW;
  const {
    xScale,
    yScale,
    yMin,
    yMax
  } = useMemo(() => {
    if (!daily.length) return {
      xScale: () => 0,
      yScale: () => 0,
      yMin: 0,
      yMax: 0
    };
    const allHi = daily.map(d => d.high),
      allLo = daily.map(d => d.low);
    let yMin = Math.min(...allLo, putStrike || Infinity, expLow || Infinity);
    let yMax = Math.max(...allHi, callStrike || 0, expHigh || 0);
    // Extend y-axis to include ±2σ cone bounds at full DTE so the cone
    // doesn't get clipped above or below the visible range.
    if (showProbCone && ivAnnualized && dteToExp > 0 && currentPrice > 0) {
      const dailySigma = ivAnnualized / Math.sqrt(252);
      const sigmaT = dailySigma * Math.sqrt(dteToExp);
      yMax = Math.max(yMax, currentPrice * Math.exp(2 * sigmaT));
      yMin = Math.min(yMin, currentPrice * Math.exp(-2 * sigmaT));
    }
    const pad = (yMax - yMin) * 0.08;
    yMin -= pad;
    yMax += pad;
    const xScale = i => padL + i / Math.max(1, daily.length - 1) * dataW;
    const yScale = v => padT + (1 - (v - yMin) / (yMax - yMin)) * priceH;
    return {
      xScale,
      yScale,
      yMin,
      yMax
    };
  }, [daily, expHigh, expLow, callStrike, putStrike, priceH, dataW, showProbCone, ivAnnualized, dteToExp, currentPrice]);

  // MACD: use server-side values if present (preferred — already warmed up
  // across the full visible window). Fall back to client compute otherwise.
  const macdData = useMemo(() => {
    if (!daily.length) return null;
    if (daily.some(d => d.macd != null)) {
      return {
        macd: daily.map(d => d.macd ?? null),
        signal: daily.map(d => d.signal ?? null),
        hist: daily.map(d => d.hist ?? null)
      };
    }
    if (daily.length < 26) return null;
    const closes = daily.map(d => d.close);
    const ema = (vals, period) => {
      const out = new Array(vals.length).fill(null);
      if (vals.length < period) return out;
      let sum = 0;
      for (let i = 0; i < period; i++) sum += vals[i];
      out[period - 1] = sum / period;
      const k = 2 / (period + 1);
      for (let i = period; i < vals.length; i++) {
        out[i] = vals[i] * k + out[i - 1] * (1 - k);
      }
      return out;
    };
    const e12 = ema(closes, 12);
    const e26 = ema(closes, 26);
    const macdLine = e12.map((v, i) => v == null || e26[i] == null ? null : v - e26[i]);
    const firstValid = macdLine.findIndex(v => v != null);
    const signal = new Array(closes.length).fill(null);
    if (firstValid !== -1 && firstValid + 9 <= closes.length) {
      let sum = 0;
      for (let i = firstValid; i < firstValid + 9; i++) sum += macdLine[i];
      signal[firstValid + 8] = sum / 9;
      const k = 2 / 10;
      for (let i = firstValid + 9; i < closes.length; i++) {
        signal[i] = macdLine[i] * k + signal[i - 1] * (1 - k);
      }
    }
    const hist = macdLine.map((v, i) => v == null || signal[i] == null ? null : v - signal[i]);
    return {
      macd: macdLine,
      signal,
      hist
    };
  }, [daily]);
  const macdScale = useMemo(() => {
    if (!macdData) return null;
    const all = [];
    [macdData.macd, macdData.signal, macdData.hist].forEach(arr => {
      arr.forEach(v => {
        if (v != null) all.push(v);
      });
    });
    if (!all.length) return null;
    const absMax = Math.max(...all.map(Math.abs)) * 1.15 || 1;
    const mMin = -absMax,
      mMax = absMax;
    const yScaleM = v => macdTop + (1 - (v - mMin) / (mMax - mMin)) * macdH;
    return {
      mMin,
      mMax,
      yScaleM
    };
  }, [macdData, macdTop, macdH]);

  // RSI14 series — Wilder's smoothing, standard 14-period.
  // Returns null entries until period+1 closes have accumulated.
  const rsiData = useMemo(() => {
    if (!daily.length) return null;
    const closes = daily.map(d => d.close ?? null);
    const period = 14;
    const out = new Array(closes.length).fill(null);
    if (closes.length < period + 1) return {
      values: out
    };
    let gains = 0,
      losses = 0;
    for (let i = 1; i <= period; i++) {
      const ch = closes[i] - closes[i - 1];
      if (ch >= 0) gains += ch;else losses -= ch;
    }
    let avgGain = gains / period;
    let avgLoss = losses / period;
    out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
    for (let i = period + 1; i < closes.length; i++) {
      const ch = closes[i] - closes[i - 1];
      const g = ch > 0 ? ch : 0;
      const l = ch < 0 ? -ch : 0;
      avgGain = (avgGain * (period - 1) + g) / period;
      avgLoss = (avgLoss * (period - 1) + l) / period;
      out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
    }
    return {
      values: out
    };
  }, [daily]);
  const rsiScale = useMemo(() => {
    if (!showRSI || !rsiData) return null;
    // Fixed 0-100 scale for RSI
    const yScaleR = v => rsiTop + (1 - v / 100) * rsiH;
    return {
      yScaleR
    };
  }, [showRSI, rsiData, rsiTop, rsiH]);
  const ticks = useMemo(() => niceTicks(yMin, yMax, 6), [yMin, yMax]);
  const xTicks = useMemo(() => {
    if (!daily.length) return [];
    const n = 6,
      out = [];
    for (let i = 0; i <= n; i++) {
      const idx = Math.round(i / n * (daily.length - 1));
      if (daily[idx]) out.push({
        idx,
        date: daily[idx].date
      });
    }
    return out;
  }, [daily]);
  const candleW = Math.max(2, innerW / daily.length * 0.65);

  // Map past earnings dates onto the closest daily bar index. Earnings often
  // print after-hours so the marker lands on the next session bar; that
  // matches how traders read the chart.
  const earningsMarkers = useMemo(() => {
    if (!earnings || !Array.isArray(earnings.past) || !daily.length) return [];
    const dailyTimes = daily.map(d => +new Date(d.date));
    const out = [];
    earnings.past.forEach(s => {
      const t = +new Date(s + "T16:00:00");
      if (Number.isNaN(t)) return;
      // require the earnings date to be within the chart window
      if (t < dailyTimes[0] - 86400000 || t > dailyTimes[dailyTimes.length - 1] + 86400000) return;
      let bestI = 0,
        bestD = Infinity;
      for (let i = 0; i < dailyTimes.length; i++) {
        const d = Math.abs(dailyTimes[i] - t);
        if (d < bestD) {
          bestD = d;
          bestI = i;
        }
      }
      out.push({
        idx: bestI,
        date: s
      });
    });
    return out;
  }, [earnings, daily]);

  // Render-empty fallback. After all hooks have been called (rules of
  // hooks compliance), if there's nothing to draw we render a minimal
  // placeholder rather than letting downstream JSX index into empty
  // arrays.
  if (!daily.length) {
    return /*#__PURE__*/React.createElement("div", {
      className: "chart-wrap"
    }, /*#__PURE__*/React.createElement("svg", {
      viewBox: `0 0 ${W} ${H}`,
      className: "chart-svg"
    }, /*#__PURE__*/React.createElement("text", {
      x: W / 2,
      y: H / 2,
      textAnchor: "middle",
      fill: colors?.fg3 || "#888",
      fontSize: "12",
      fontFamily: "ui-monospace, monospace"
    }, "No price data available")));
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "chart-wrap",
    onMouseLeave: () => setHover(null)
  }, /*#__PURE__*/React.createElement("svg", {
    ref: svgRef,
    viewBox: `0 0 ${W} ${H}`,
    className: `chart-svg ${pan ? "is-panning" : ""}`,
    onMouseDown: onChartMouseDown
  }, ticks.map((t, i) => /*#__PURE__*/React.createElement("g", {
    key: i
  }, /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(t),
    y2: yScale(t),
    className: "grid"
  }), /*#__PURE__*/React.createElement("text", {
    x: padL - 8,
    y: yScale(t) + 4,
    className: "axis-text",
    textAnchor: "end"
  }, "$", t.toFixed(2)))), xTicks.map((t, i) => /*#__PURE__*/React.createElement("text", {
    key: i,
    x: xScale(t.idx),
    y: H - 10,
    className: "axis-text",
    textAnchor: "middle"
  }, fmtDate(t.date))), expHigh && expLow && /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("rect", {
    x: padL,
    y: yScale(expHigh),
    width: innerW,
    height: yScale(expLow) - yScale(expHigh),
    fill: colors.band,
    opacity: "0.18"
  }), /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(expHigh),
    y2: yScale(expHigh),
    stroke: colors.band,
    strokeDasharray: "3 3",
    strokeWidth: "1",
    opacity: "0.6"
  }), /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(expLow),
    y2: yScale(expLow),
    stroke: colors.band,
    strokeDasharray: "3 3",
    strokeWidth: "1",
    opacity: "0.6"
  })), chartStyle === "area" && (() => {
    const path = daily.map((d, i) => `${i === 0 ? "M" : "L"} ${xScale(i)} ${yScale(d.close)}`).join(" ");
    const fill = `${path} L ${xScale(daily.length - 1)} ${padT + priceH} L ${xScale(0)} ${padT + priceH} Z`;
    return /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("path", {
      d: fill,
      fill: colors.up,
      opacity: "0.12"
    }), /*#__PURE__*/React.createElement("path", {
      d: path,
      fill: "none",
      stroke: colors.up,
      strokeWidth: "1.8"
    }));
  })(), chartStyle === "candles" && daily.map((d, i) => {
    const up = d.close >= d.open;
    const x = xScale(i);
    const yO = yScale(d.open),
      yC = yScale(d.close);
    return /*#__PURE__*/React.createElement("g", {
      key: i
    }, /*#__PURE__*/React.createElement("line", {
      x1: x,
      x2: x,
      y1: yScale(d.high),
      y2: yScale(d.low),
      stroke: up ? colors.up : colors.down,
      strokeWidth: "1"
    }), /*#__PURE__*/React.createElement("rect", {
      x: x - candleW / 2,
      y: Math.min(yO, yC),
      width: candleW,
      height: Math.max(1, Math.abs(yC - yO)),
      fill: up ? colors.up : colors.down
    }));
  }), chartStyle === "ohlc" && daily.map((d, i) => {
    const up = d.close >= d.open;
    const x = xScale(i);
    const yO = yScale(d.open),
      yC = yScale(d.close);
    const w = candleW * 0.6;
    return /*#__PURE__*/React.createElement("g", {
      key: i,
      stroke: up ? colors.up : colors.down,
      strokeWidth: "1.2"
    }, /*#__PURE__*/React.createElement("line", {
      x1: x,
      x2: x,
      y1: yScale(d.high),
      y2: yScale(d.low)
    }), /*#__PURE__*/React.createElement("line", {
      x1: x - w,
      x2: x,
      y1: yO,
      y2: yO
    }), /*#__PURE__*/React.createElement("line", {
      x1: x,
      x2: x + w,
      y1: yC,
      y2: yC
    }));
  }), (showEMA21 || showMA50 || showMA200) && (() => {
    const buildPath = key => {
      let cur = "";
      const segs = [];
      for (let i = 0; i < daily.length; i++) {
        const v = daily[i][key];
        if (v == null) {
          if (cur) segs.push(cur);
          cur = "";
          continue;
        }
        cur += `${cur === "" ? "M" : "L"} ${xScale(i)} ${yScale(v)} `;
      }
      if (cur) segs.push(cur);
      return segs.join(" ");
    };
    // Detect MA50 / MA200 crossovers within visible window. A cross
    // is recorded only when both averages are valid on both the prior
    // and current bar AND the relative position flips. Returns an
    // array of {idx, type: "golden"|"death", price}.
    const crosses = [];
    if (showMA50 && showMA200) {
      for (let i = 1; i < daily.length; i++) {
        const a50 = daily[i - 1].ma50,
          a200 = daily[i - 1].ma200;
        const b50 = daily[i].ma50,
          b200 = daily[i].ma200;
        if (a50 == null || a200 == null || b50 == null || b200 == null) continue;
        const wasBelow = a50 < a200,
          nowAbove = b50 > b200;
        const wasAbove = a50 > a200,
          nowBelow = b50 < b200;
        if (wasBelow && nowAbove) crosses.push({
          idx: i,
          type: "golden",
          price: daily[i].close
        });else if (wasAbove && nowBelow) crosses.push({
          idx: i,
          type: "death",
          price: daily[i].close
        });
      }
    }
    return /*#__PURE__*/React.createElement("g", null, showEMA21 && /*#__PURE__*/React.createElement("path", {
      d: buildPath("ema21"),
      fill: "none",
      stroke: "rgb(80, 160, 255)",
      strokeWidth: "1.5",
      opacity: "0.9"
    }), showMA50 && /*#__PURE__*/React.createElement("path", {
      d: buildPath("ma50"),
      fill: "none",
      stroke: colors.warn,
      strokeWidth: "1.6",
      opacity: "0.85"
    }), showMA200 && /*#__PURE__*/React.createElement("path", {
      d: buildPath("ma200"),
      fill: "none",
      stroke: colors.accent,
      strokeWidth: "1.8",
      opacity: "0.85"
    }), crosses.map((c, k) => {
      const cx = xScale(c.idx);
      const cy = yScale(c.price);
      const fill = c.type === "golden" ? colors.up : colors.down;
      const label = c.type === "golden" ? "GOLDEN" : "DEATH";
      return /*#__PURE__*/React.createElement("g", {
        key: `mx${k}`
      }, /*#__PURE__*/React.createElement("circle", {
        cx: cx,
        cy: cy,
        r: "6",
        fill: fill,
        stroke: "white",
        strokeWidth: "1.5",
        opacity: "0.95"
      }), /*#__PURE__*/React.createElement("rect", {
        x: cx + 8,
        y: cy - 14,
        width: "58",
        height: "14",
        rx: "3",
        fill: fill,
        opacity: "0.18",
        stroke: fill,
        strokeOpacity: "0.55"
      }), /*#__PURE__*/React.createElement("text", {
        x: cx + 12,
        y: cy - 4,
        fontSize: "9",
        fontWeight: "700",
        fill: fill,
        fontFamily: "ui-monospace, monospace",
        letterSpacing: "0.08em"
      }, label));
    }));
  })(), showProbCone && ivAnnualized && dteToExp > 0 && currentPrice > 0 && daily.length > 0 && (() => {
    const lastIdx = daily.length - 1;
    const xStart = xScale(lastIdx);
    const xEnd = padL + innerW; // full right edge — projection space
    if (xEnd <= xStart) return null;
    // Build smooth fan with ~24 sample points across the projection.
    const N = 24;
    const T_total = dteToExp;
    // Annualized vol → daily stdev assuming 252 trading days
    const dailySigma = ivAnnualized / Math.sqrt(252);
    const sample = sigmaMult => {
      const upper = [];
      const lower = [];
      for (let i = 0; i <= N; i++) {
        const tDays = i / N * T_total;
        const sigmaT = dailySigma * Math.sqrt(tDays);
        const upPrice = currentPrice * Math.exp(sigmaMult * sigmaT);
        const dnPrice = currentPrice * Math.exp(-sigmaMult * sigmaT);
        const x = xStart + (xEnd - xStart) * i / N;
        upper.push([x, yScale(upPrice)]);
        lower.push([x, yScale(dnPrice)]);
      }
      const path = `M ${upper[0][0]} ${upper[0][1]} ` + upper.slice(1).map(p => `L ${p[0]} ${p[1]}`).join(" ") + " " + lower.slice().reverse().map(p => `L ${p[0]} ${p[1]}`).join(" ") + " Z";
      // Stroke paths so the cone is visible even when fill is subtle.
      const upperLine = `M ${upper[0][0]} ${upper[0][1]} ` + upper.slice(1).map(p => `L ${p[0]} ${p[1]}`).join(" ");
      const lowerLine = `M ${lower[0][0]} ${lower[0][1]} ` + lower.slice(1).map(p => `L ${p[0]} ${p[1]}`).join(" ");
      return {
        fill: path,
        upperLine,
        lowerLine,
        lastUpper: upper[upper.length - 1],
        lastLower: lower[lower.length - 1]
      };
    };
    const s2 = sample(2);
    const s1 = sample(1);
    // Vertical separator marking the projection start
    return /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("line", {
      x1: xStart,
      y1: padT,
      x2: xStart,
      y2: padT + priceH,
      stroke: colors.accent,
      strokeWidth: "1",
      strokeDasharray: "2 4",
      opacity: "0.3"
    }), /*#__PURE__*/React.createElement("path", {
      d: s2.fill,
      fill: colors.accent,
      fillOpacity: "0.07",
      stroke: "none"
    }), /*#__PURE__*/React.createElement("path", {
      d: s1.fill,
      fill: colors.accent,
      fillOpacity: "0.13",
      stroke: "none"
    }), /*#__PURE__*/React.createElement("path", {
      d: s2.upperLine,
      fill: "none",
      stroke: colors.accent,
      strokeWidth: "1",
      strokeDasharray: "3 3",
      opacity: "0.55"
    }), /*#__PURE__*/React.createElement("path", {
      d: s2.lowerLine,
      fill: "none",
      stroke: colors.accent,
      strokeWidth: "1",
      strokeDasharray: "3 3",
      opacity: "0.55"
    }), /*#__PURE__*/React.createElement("path", {
      d: s1.upperLine,
      fill: "none",
      stroke: colors.accent,
      strokeWidth: "1.4",
      opacity: "0.85"
    }), /*#__PURE__*/React.createElement("path", {
      d: s1.lowerLine,
      fill: "none",
      stroke: colors.accent,
      strokeWidth: "1.4",
      opacity: "0.85"
    }), /*#__PURE__*/React.createElement("line", {
      x1: xStart,
      y1: yScale(currentPrice),
      x2: xEnd,
      y2: yScale(currentPrice),
      stroke: colors.accent,
      strokeWidth: "1",
      strokeDasharray: "2 3",
      opacity: "0.4"
    }), /*#__PURE__*/React.createElement("text", {
      x: s2.lastUpper[0] - 4,
      y: s2.lastUpper[1] - 3,
      fontSize: "10",
      textAnchor: "end",
      fill: colors.accent,
      fontFamily: "ui-monospace, monospace",
      opacity: "0.7"
    }, "+2\u03C3"), /*#__PURE__*/React.createElement("text", {
      x: s1.lastUpper[0] - 4,
      y: s1.lastUpper[1] - 3,
      fontSize: "10",
      textAnchor: "end",
      fill: colors.accent,
      fontFamily: "ui-monospace, monospace",
      opacity: "0.85"
    }, "+1\u03C3"), /*#__PURE__*/React.createElement("text", {
      x: s1.lastLower[0] - 4,
      y: s1.lastLower[1] + 11,
      fontSize: "10",
      textAnchor: "end",
      fill: colors.accent,
      fontFamily: "ui-monospace, monospace",
      opacity: "0.85"
    }, "-1\u03C3"), /*#__PURE__*/React.createElement("text", {
      x: s2.lastLower[0] - 4,
      y: s2.lastLower[1] + 11,
      fontSize: "10",
      textAnchor: "end",
      fill: colors.accent,
      fontFamily: "ui-monospace, monospace",
      opacity: "0.7"
    }, "-2\u03C3"));
  })(), callStrike > 0 && /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(callStrike),
    y2: yScale(callStrike),
    stroke: colors.up,
    strokeWidth: "1.5",
    strokeDasharray: "6 4",
    opacity: "0.85"
  }), /*#__PURE__*/React.createElement("text", {
    x: W - padR - 6,
    y: yScale(callStrike) - 6,
    className: "strike-label",
    textAnchor: "end",
    fill: colors.up
  }, "CALL $", callStrike.toFixed(2))), putStrike > 0 && /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(putStrike),
    y2: yScale(putStrike),
    stroke: colors.down,
    strokeWidth: "1.5",
    strokeDasharray: "6 4",
    opacity: "0.85"
  }), /*#__PURE__*/React.createElement("text", {
    x: W - padR - 6,
    y: yScale(putStrike) + 14,
    className: "strike-label",
    textAnchor: "end",
    fill: colors.down
  }, "PUT $", putStrike.toFixed(2))), currentPrice && /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(currentPrice),
    y2: yScale(currentPrice),
    stroke: colors.accent,
    strokeWidth: "1",
    opacity: "0.5"
  }), /*#__PURE__*/React.createElement("rect", {
    x: W - padR - 60,
    y: yScale(currentPrice) - 9,
    width: "58",
    height: "18",
    fill: colors.accent,
    rx: "3"
  }), /*#__PURE__*/React.createElement("text", {
    x: W - padR - 31,
    y: yScale(currentPrice) + 4,
    className: "strike-label",
    textAnchor: "middle",
    fill: colors.accentText
  }, "$", currentPrice.toFixed(2))), earningsMarkers.map((m, i) => /*#__PURE__*/React.createElement("g", {
    key: `erm${i}`
  }, /*#__PURE__*/React.createElement("line", {
    x1: xScale(m.idx),
    x2: xScale(m.idx),
    y1: padT,
    y2: padT + innerH,
    stroke: colors.warn,
    strokeOpacity: "0.35",
    strokeDasharray: "2 4",
    strokeWidth: "1"
  }), /*#__PURE__*/React.createElement("g", {
    transform: `translate(${xScale(m.idx)}, ${padT + 4})`
  }, /*#__PURE__*/React.createElement("circle", {
    r: "9",
    fill: colors.warn,
    opacity: "0.95"
  }), /*#__PURE__*/React.createElement("text", {
    x: "0",
    y: "4",
    textAnchor: "middle",
    fontSize: "11",
    fontWeight: "700",
    fill: "white",
    fontFamily: "ui-sans-serif, system-ui"
  }, "E")))), (() => {
    if (!earnings || !earnings.next || !daily.length) return null;
    const nextDate = new Date(earnings.next + "T16:00:00");
    if (Number.isNaN(nextDate.getTime())) return null;
    const lastBarTime = +new Date(daily[daily.length - 1].date);
    const daysAway = (nextDate.getTime() - lastBarTime) / 86400000;
    if (daysAway < 0) return null; // already happened
    // Use 21 trading-ish days as the projection scale — most weekly
    // setups are inside that window. Earnings further out get
    // pinned at the right edge with a "30+d" label.
    const projDays = 21;
    const xLast = xScale(daily.length - 1);
    const xRight = padL + innerW;
    const projW = Math.max(0, xRight - xLast);
    const frac = Math.min(1, daysAway / projDays);
    const xE = xLast + frac * projW;
    const dteLabel = daysAway >= 30 ? `${Math.round(daysAway)}d` : `${Math.ceil(daysAway)}d`;
    const isClose = daysAway <= 14;
    return /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("line", {
      x1: xE,
      x2: xE,
      y1: padT,
      y2: padT + innerH,
      stroke: colors.warn,
      strokeWidth: "1.4",
      strokeDasharray: isClose ? "4 3" : "2 4",
      opacity: isClose ? 0.85 : 0.55
    }), /*#__PURE__*/React.createElement("g", {
      transform: `translate(${xE}, ${padT + 4})`
    }, /*#__PURE__*/React.createElement("circle", {
      r: "11",
      fill: colors.warn,
      stroke: "white",
      strokeWidth: "1.5",
      opacity: "0.95"
    }), /*#__PURE__*/React.createElement("text", {
      x: "0",
      y: "4",
      textAnchor: "middle",
      fontSize: "11",
      fontWeight: "800",
      fill: "white",
      fontFamily: "ui-sans-serif, system-ui"
    }, "E")), /*#__PURE__*/React.createElement("g", {
      transform: `translate(${xE}, ${padT + innerH - 12})`
    }, /*#__PURE__*/React.createElement("rect", {
      x: "-22",
      y: "-9",
      width: "44",
      height: "18",
      rx: "3",
      fill: colors.warn,
      opacity: "0.9"
    }), /*#__PURE__*/React.createElement("text", {
      x: "0",
      y: "4",
      textAnchor: "middle",
      fontSize: "11",
      fontWeight: "700",
      fill: "white",
      fontFamily: "ui-monospace, monospace"
    }, dteLabel)));
  })(), /*#__PURE__*/React.createElement("rect", {
    x: padL,
    y: padT,
    width: innerW,
    height: innerH,
    fill: "transparent",
    onMouseMove: e => {
      // Convert pixel coords to viewBox coords. SVG has
      // viewBox 0 0 W H but is rendered at arbitrary CSS
      // size, so we scale through the bounding rect of the
      // outer SVG. Without this the crosshair drifts off
      // because the rect we attached the handler to is
      // already in viewBox units inside the SVG.
      if (!svgRef.current) return;
      const svgRect = svgRef.current.getBoundingClientRect();
      const vbX = (e.clientX - svgRect.left) / svgRect.width * W;
      const vbY = (e.clientY - svgRect.top) / svgRect.height * H;
      // Snap idx for date label + bar dot (still useful) but
      // store the raw pixel position so crosshair lines
      // follow the mouse exactly.
      const px = vbX - padL;
      const idx = Math.max(0, Math.min(daily.length - 1, Math.round(px / innerW * (daily.length - 1))));
      setHover({
        idx,
        x: vbX,
        y: vbY
      });
    }
  }), hover != null && daily[hover.idx] && (() => {
    // Clamp the crosshair lines to the chart's plot area so they
    // don't draw over the y-axis labels or above the title.
    const cx = Math.max(padL, Math.min(padL + innerW, hover.x));
    const cy = Math.max(padT, Math.min(padT + priceH, hover.y));
    // Invert yScale: y = padT + (1 - (v - yMin)/(yMax-yMin)) * priceH
    // so v = yMin + (1 - (y - padT)/priceH) * (yMax - yMin)
    const priceAtY = yMin + (1 - (cy - padT) / priceH) * (yMax - yMin);
    return /*#__PURE__*/React.createElement("g", {
      pointerEvents: "none"
    }, /*#__PURE__*/React.createElement("line", {
      x1: cx,
      x2: cx,
      y1: padT,
      y2: padT + priceH,
      stroke: "currentColor",
      opacity: "0.35",
      strokeDasharray: "2 3"
    }), /*#__PURE__*/React.createElement("line", {
      x1: padL,
      x2: padL + innerW,
      y1: cy,
      y2: cy,
      stroke: "currentColor",
      opacity: "0.35",
      strokeDasharray: "2 3"
    }), /*#__PURE__*/React.createElement("g", {
      transform: `translate(${padL - 1}, ${cy})`
    }, /*#__PURE__*/React.createElement("rect", {
      x: "-50",
      y: "-9",
      width: "49",
      height: "18",
      rx: "3",
      fill: colors.accent,
      opacity: "0.95"
    }), /*#__PURE__*/React.createElement("text", {
      x: "-25",
      y: "4",
      textAnchor: "middle",
      fontSize: "11",
      fontWeight: "700",
      fill: colors.accentText,
      fontFamily: "ui-monospace, monospace"
    }, "$", priceAtY.toFixed(2))), /*#__PURE__*/React.createElement("g", {
      transform: `translate(${cx}, ${padT + priceH + 1})`
    }, /*#__PURE__*/React.createElement("rect", {
      x: "-36",
      y: "0",
      width: "72",
      height: "16",
      rx: "3",
      fill: colors.fg2 || "#666",
      opacity: "0.95"
    }), /*#__PURE__*/React.createElement("text", {
      x: "0",
      y: "11",
      textAnchor: "middle",
      fontSize: "10",
      fontWeight: "700",
      fill: "white",
      fontFamily: "ui-monospace, monospace"
    }, fmtDate(daily[hover.idx].date, {
      month: "short",
      day: "numeric"
    }))), /*#__PURE__*/React.createElement("circle", {
      cx: xScale(hover.idx),
      cy: yScale(daily[hover.idx].close),
      r: "4",
      fill: colors.accent,
      stroke: "white",
      strokeWidth: "1.5"
    }));
  })(), macdData && macdScale && (() => {
    const {
      mMin,
      mMax,
      yScaleM
    } = macdScale;
    const zeroY = yScaleM(0);
    const barW = Math.max(1.5, innerW / daily.length * 0.55);
    // y-axis labels for MACD: -absMax/1.15, 0, +absMax/1.15
    const macdLabel = mMax / 1.15;
    // Build MACD and signal polylines, breaking on null gaps.
    const buildPath = arr => {
      const segs = [];
      let cur = "";
      for (let i = 0; i < arr.length; i++) {
        const v = arr[i];
        if (v == null) {
          if (cur) segs.push(cur);
          cur = "";
          continue;
        }
        cur += `${cur === "" ? "M" : "L"} ${xScale(i)} ${yScaleM(v)} `;
      }
      if (cur) segs.push(cur);
      return segs.join(" ");
    };
    const macdPath = buildPath(macdData.macd);
    const signalPath = buildPath(macdData.signal);
    return /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("line", {
      x1: padL,
      x2: W - padR,
      y1: macdTop - gapH / 2,
      y2: macdTop - gapH / 2,
      stroke: "currentColor",
      opacity: "0.08"
    }), /*#__PURE__*/React.createElement("text", {
      x: padL - 8,
      y: yScaleM(macdLabel) + 4,
      className: "axis-text",
      textAnchor: "end"
    }, macdLabel >= 1 ? `+${macdLabel.toFixed(1)}` : `+${macdLabel.toFixed(2)}`), /*#__PURE__*/React.createElement("text", {
      x: padL - 8,
      y: zeroY + 4,
      className: "axis-text",
      textAnchor: "end"
    }, "0"), /*#__PURE__*/React.createElement("text", {
      x: padL - 8,
      y: yScaleM(-macdLabel) + 4,
      className: "axis-text",
      textAnchor: "end"
    }, macdLabel >= 1 ? `-${macdLabel.toFixed(1)}` : `-${macdLabel.toFixed(2)}`), /*#__PURE__*/React.createElement("line", {
      x1: padL,
      x2: W - padR,
      y1: zeroY,
      y2: zeroY,
      stroke: "currentColor",
      opacity: "0.25",
      strokeWidth: "1"
    }), macdData.hist.map((h, i) => {
      if (h == null) return null;
      const prev = i > 0 ? macdData.hist[i - 1] : null;
      const rising = prev != null && Math.abs(h) > Math.abs(prev);
      const positive = h >= 0;
      // Bright green when positive AND building, muted green when fading.
      // Bright red when negative AND building, muted red when fading.
      const fillC = positive ? colors.up : colors.down;
      const opacity = rising ? 0.85 : 0.35;
      const x = xScale(i) - barW / 2;
      const y = positive ? yScaleM(h) : zeroY;
      const height = Math.max(1, Math.abs(yScaleM(h) - zeroY));
      return /*#__PURE__*/React.createElement("rect", {
        key: `h${i}`,
        x: x,
        y: y,
        width: barW,
        height: height,
        fill: fillC,
        opacity: opacity
      });
    }), /*#__PURE__*/React.createElement("path", {
      d: macdPath,
      fill: "none",
      stroke: colors.accent,
      strokeWidth: "1.5",
      opacity: "0.9"
    }), /*#__PURE__*/React.createElement("path", {
      d: signalPath,
      fill: "none",
      stroke: colors.warn,
      strokeWidth: "1.5",
      strokeDasharray: "4 3",
      opacity: "0.9"
    }), /*#__PURE__*/React.createElement("text", {
      x: padL + 6,
      y: macdTop + 12,
      className: "axis-text",
      fontFamily: "ui-monospace, monospace",
      fontSize: "11",
      style: {
        fontWeight: 600,
        opacity: 0.7
      }
    }, "MACD (12, 26, 9)"));
  })(), showRSI && rsiData && rsiScale && (() => {
    const {
      yScaleR
    } = rsiScale;
    const vals = rsiData.values;
    // Build path skipping null entries
    let d = "";
    let started = false;
    for (let i = 0; i < vals.length; i++) {
      if (vals[i] == null) {
        started = false;
        continue;
      }
      const x = xScale(i);
      const y = yScaleR(vals[i]);
      if (!started) {
        d += `M${x},${y}`;
        started = true;
      } else {
        d += `L${x},${y}`;
      }
    }
    const lastVal = vals[vals.length - 1];
    // Color line by zone — red overbought, green oversold, neutral default
    const lineColor = lastVal == null ? colors.fg2 : lastVal >= 70 ? colors.down : lastVal <= 30 ? colors.up : "rgb(150, 130, 220)";
    return /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("rect", {
      x: padL,
      y: rsiTop,
      width: innerW,
      height: rsiH,
      fill: "none",
      stroke: colors.line,
      strokeOpacity: "0.3"
    }), /*#__PURE__*/React.createElement("rect", {
      x: padL,
      y: rsiTop,
      width: innerW,
      height: yScaleR(70) - rsiTop,
      fill: colors.down,
      fillOpacity: "0.06"
    }), /*#__PURE__*/React.createElement("rect", {
      x: padL,
      y: yScaleR(30),
      width: innerW,
      height: rsiTop + rsiH - yScaleR(30),
      fill: colors.up,
      fillOpacity: "0.06"
    }), [70, 50, 30].map(level => /*#__PURE__*/React.createElement("line", {
      key: level,
      x1: padL,
      x2: padL + innerW,
      y1: yScaleR(level),
      y2: yScaleR(level),
      stroke: level === 50 ? colors.fg2 : level === 70 ? colors.down : colors.up,
      strokeWidth: "1",
      strokeDasharray: level === 50 ? "2,3" : "3,3",
      strokeOpacity: level === 50 ? "0.4" : "0.55"
    })), [70, 30].map(level => /*#__PURE__*/React.createElement("text", {
      key: level,
      x: padL - 6,
      y: yScaleR(level),
      textAnchor: "end",
      dominantBaseline: "middle",
      fontFamily: "ui-monospace, monospace",
      fontSize: "10",
      fill: level === 70 ? colors.down : colors.up,
      opacity: "0.85"
    }, level)), /*#__PURE__*/React.createElement("path", {
      d: d,
      fill: "none",
      stroke: lineColor,
      strokeWidth: "1.6",
      opacity: "0.92"
    }), lastVal != null && (() => {
      const x = padL + innerW + 4;
      const y = yScaleR(lastVal);
      return /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("rect", {
        x: x,
        y: y - 8,
        width: 32,
        height: 16,
        rx: "3",
        fill: lineColor,
        opacity: "0.92"
      }), /*#__PURE__*/React.createElement("text", {
        x: x + 16,
        y: y + 1,
        textAnchor: "middle",
        dominantBaseline: "middle",
        fontFamily: "ui-monospace, monospace",
        fontSize: "10",
        fill: "white",
        fontWeight: "700"
      }, lastVal.toFixed(0)));
    })(), /*#__PURE__*/React.createElement("text", {
      x: padL + 6,
      y: rsiTop + 14,
      fill: colors.fg2,
      fontFamily: "ui-monospace, monospace",
      fontSize: "11",
      style: {
        fontWeight: 600,
        opacity: 0.7
      }
    }, "RSI 14"));
  })()), hover != null && daily[hover.idx] && /*#__PURE__*/React.createElement("div", {
    className: "chart-tooltip",
    style: {
      left: `${xScale(hover.idx) / W * 100}%`
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "tt-date"
  }, fmtDate(daily[hover.idx].date, {
    weekday: "short",
    month: "short",
    day: "numeric"
  })), /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "Open"), /*#__PURE__*/React.createElement("b", null, fmt$(daily[hover.idx].open))), /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "High"), /*#__PURE__*/React.createElement("b", null, fmt$(daily[hover.idx].high))), /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "Low"), /*#__PURE__*/React.createElement("b", null, fmt$(daily[hover.idx].low))), /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "Close"), /*#__PURE__*/React.createElement("b", null, fmt$(daily[hover.idx].close)))));
}

// ── Returns history bars ───────────────────────────────────────────────────
// One bar per week: vertical range from low_return to high_return.
// Close shown as a dot. Median high / median low / median close drawn as dashed lines.
// Current week marker at the right edge with a separate styling.
function ReturnsChart({
  rows,
  medianHigh,
  medianLow,
  medianClose,
  currentReturn,
  colors,
  earnings
}) {
  const W = 720,
    H = 300;
  const padL = 48,
    padR = 16,
    padT = 18,
    padB = 30;
  const innerW = W - padL - padR,
    innerH = H - padT - padB;
  const data = useMemo(() => [...rows].sort((a, b) => a.week_start - b.week_start), [rows]);

  // Tag each week (Mon..Sun span starting at week_start) with whether it
  // contained an earnings announcement. We compare on the date level so the
  // user sees a marker on the bar covering that week.
  const earningsByWeek = useMemo(() => {
    if (!earnings || !Array.isArray(earnings.past) || !data.length) return {};
    const earnTimes = earnings.past.map(s => +new Date(s + "T16:00:00")).filter(t => !Number.isNaN(t));
    const out = {};
    data.forEach((d, i) => {
      const start = +new Date(d.week_start);
      const end = start + 7 * 86400000;
      if (earnTimes.some(t => t >= start && t < end)) out[i] = true;
    });
    return out;
  }, [earnings, data]);
  const allY = useMemo(() => {
    const ys = data.flatMap(d => [d.high_return, d.low_return, d.close_return]);
    if (medianHigh != null) ys.push(medianHigh);
    if (medianLow != null) ys.push(medianLow);
    if (medianClose != null) ys.push(medianClose);
    if (currentReturn != null) ys.push(currentReturn);
    ys.push(0);
    return ys;
  }, [data, medianHigh, medianLow, medianClose, currentReturn]);
  let yMin = Math.min(...allY),
    yMax = Math.max(...allY);
  const padY = Math.max((yMax - yMin) * 0.12, 0.5);
  yMin -= padY;
  yMax += padY;
  const N = data.length + 1;
  const slot = innerW / N;
  const barW = Math.max(4, slot * 0.55);
  const xCenter = i => padL + slot * (i + 0.5);
  const yScale = v => padT + (1 - (v - yMin) / (yMax - yMin)) * innerH;
  const ticks = useMemo(() => niceTicks(yMin, yMax, 5), [yMin, yMax]);
  const [hover, setHover] = useState(null);
  // Same defensive reset as PriceChart — when the rows change we drop
  // any stale hover index that might point past the new array.
  useEffect(() => {
    setHover(null);
  }, [data.length]);
  if (!data.length) {
    return /*#__PURE__*/React.createElement("div", {
      className: "chart-wrap",
      style: {
        padding: 40,
        textAlign: "center",
        color: "var(--fg-3)"
      }
    }, "No history yet.");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "chart-wrap",
    onMouseLeave: () => setHover(null)
  }, /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    className: "chart-svg"
  }, ticks.map((t, i) => /*#__PURE__*/React.createElement("g", {
    key: i
  }, /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(t),
    y2: yScale(t),
    className: "grid"
  }), /*#__PURE__*/React.createElement("text", {
    x: padL - 6,
    y: yScale(t) + 4,
    className: "axis-text",
    textAnchor: "end"
  }, t.toFixed(0), "%"))), /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(0),
    y2: yScale(0),
    stroke: "currentColor",
    opacity: "0.35",
    strokeWidth: "1"
  }), /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(medianHigh),
    y2: yScale(medianHigh),
    stroke: colors.up,
    strokeDasharray: "4 3",
    strokeWidth: "1",
    opacity: "0.65"
  }), /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(medianLow),
    y2: yScale(medianLow),
    stroke: colors.down,
    strokeDasharray: "4 3",
    strokeWidth: "1",
    opacity: "0.65"
  }), /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: yScale(medianClose),
    y2: yScale(medianClose),
    stroke: "var(--fg-3)",
    strokeDasharray: "2 4",
    strokeWidth: "1",
    opacity: "0.7"
  }), data.map((d, i) => {
    const x = xCenter(i);
    const yHi = yScale(d.high_return),
      yLo = yScale(d.low_return);
    const yCl = yScale(d.close_return);
    // Open tick — short horizontal mark on the LEFT of the bar so
    // it doesn't collide with the Close circle in the center.
    // open_return may be missing on older payloads; render only
    // when present.
    const yOp = d.open_return != null ? yScale(d.open_return) : null;
    const positive = d.close_return >= 0;
    const fill = positive ? colors.up : colors.down;
    return /*#__PURE__*/React.createElement("g", {
      key: i,
      onMouseEnter: () => setHover({
        kind: "row",
        i
      })
    }, /*#__PURE__*/React.createElement("rect", {
      x: x - slot / 2,
      y: padT,
      width: slot,
      height: innerH,
      fill: earningsByWeek[i] ? colors.warn : "transparent",
      opacity: earningsByWeek[i] ? 0.10 : 1
    }), /*#__PURE__*/React.createElement("line", {
      x1: x,
      x2: x,
      y1: yHi,
      y2: yLo,
      stroke: fill,
      strokeWidth: "1.5",
      opacity: "0.45"
    }), /*#__PURE__*/React.createElement("line", {
      x1: x - barW / 2,
      x2: x + barW / 2,
      y1: yHi,
      y2: yHi,
      stroke: colors.up,
      strokeWidth: "2"
    }), /*#__PURE__*/React.createElement("line", {
      x1: x - barW / 2,
      x2: x + barW / 2,
      y1: yLo,
      y2: yLo,
      stroke: colors.down,
      strokeWidth: "2"
    }), yOp != null && /*#__PURE__*/React.createElement("line", {
      x1: x - barW / 2,
      x2: x,
      y1: yOp,
      y2: yOp,
      stroke: "var(--fg-2)",
      strokeWidth: "2"
    }), /*#__PURE__*/React.createElement("circle", {
      cx: x,
      cy: yCl,
      r: "3.6",
      fill: "var(--bg-2)",
      stroke: "var(--fg)",
      strokeWidth: "2"
    }), earningsByWeek[i] && /*#__PURE__*/React.createElement("g", {
      transform: `translate(${x}, ${padT - 2})`
    }, /*#__PURE__*/React.createElement("circle", {
      r: "6.5",
      fill: colors.warn
    }), /*#__PURE__*/React.createElement("text", {
      x: "0",
      y: "3",
      textAnchor: "middle",
      fontSize: "9",
      fontWeight: "700",
      fill: "white",
      fontFamily: "ui-sans-serif, system-ui"
    }, "E")));
  }), currentReturn != null && (() => {
    const x = xCenter(data.length);
    const yC = yScale(currentReturn);
    return /*#__PURE__*/React.createElement("g", {
      onMouseEnter: () => setHover({
        kind: "now"
      })
    }, /*#__PURE__*/React.createElement("rect", {
      x: x - slot / 2,
      y: padT,
      width: slot,
      height: innerH,
      fill: colors.warn,
      opacity: "0.05"
    }), /*#__PURE__*/React.createElement("line", {
      x1: x,
      x2: x,
      y1: padT,
      y2: padT + innerH,
      stroke: colors.warn,
      strokeWidth: "1",
      strokeDasharray: "2 3",
      opacity: "0.5"
    }), /*#__PURE__*/React.createElement("circle", {
      cx: x,
      cy: yC,
      r: "5",
      fill: colors.warn,
      stroke: "var(--bg-2)",
      strokeWidth: "2"
    }), /*#__PURE__*/React.createElement("text", {
      x: x,
      y: padT - 4,
      className: "axis-text",
      textAnchor: "middle",
      fill: colors.warn,
      style: {
        fontWeight: 600
      }
    }, "NOW"));
  })(), data.map((d, i) => {
    const stride = Math.max(1, Math.ceil(data.length / 6));
    if (i % stride !== 0) return null;
    return /*#__PURE__*/React.createElement("text", {
      key: i,
      x: xCenter(i),
      y: H - 10,
      className: "axis-text",
      textAnchor: "middle"
    }, fmtDate(d.week_start, {
      month: "short",
      day: "numeric"
    }));
  }), /*#__PURE__*/React.createElement("text", {
    x: xCenter(data.length),
    y: H - 10,
    className: "axis-text",
    textAnchor: "middle",
    fill: colors.warn,
    style: {
      fontWeight: 600
    }
  }, "now")), hover && hover.kind === "row" && data[hover.i] && /*#__PURE__*/React.createElement("div", {
    className: "chart-tooltip small",
    style: {
      left: `${xCenter(hover.i) / W * 100}%`
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "tt-date"
  }, fmtDate(data[hover.i].week_start, {
    month: "short",
    day: "numeric"
  })), /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "High"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: colors.up
    }
  }, fmtPct(data[hover.i].high_return, 2))), /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "Low"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: colors.down
    }
  }, fmtPct(data[hover.i].low_return, 2))), data[hover.i].open_return != null && /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "Open"), /*#__PURE__*/React.createElement("b", null, fmtPct(data[hover.i].open_return, 2))), /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "Close"), /*#__PURE__*/React.createElement("b", null, fmtPct(data[hover.i].close_return, 2))), earningsByWeek[hover.i] && /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "Earnings"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: colors.warn
    }
  }, "this week"))), hover && hover.kind === "now" && currentReturn != null && /*#__PURE__*/React.createElement("div", {
    className: "chart-tooltip small",
    style: {
      left: `${xCenter(data.length) / W * 100}%`
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "tt-date",
    style: {
      color: colors.warn
    }
  }, "This week so far"), /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "vs baseline"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: currentReturn >= 0 ? colors.up : colors.down
    }
  }, fmtPct(currentReturn, 2)))));
}

// ── Day of week bars ───────────────────────────────────────────────────────
// Bar height = number of weeks where that day produced the week's overall
// peak (HIGH chart) or trough (LOW chart). Same visual as the original
// chart so it's easy to scan.
//
// % label = average day-over-day excursion for that weekday. Computed as
// (day_high / prior_close - 1) * 100 averaged across every week in the
// lookback, NOT just weeks where the day was the peak. So Wednesday's
// label is the typical Wed daily high vs Tue close, even on weeks where
// the actual week-high landed Friday. Same logic for the LOW chart with
// day_low / prior_close.
function DayBarChart({
  rows,
  colors,
  mode = "high"
}) {
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  const peakKey = mode === "low" ? "low_day_name" : "high_day_name";
  const breakdownKey = mode === "low" ? "low" : "high";
  const fillColor = mode === "low" ? colors.down : colors.up;
  const counts = days.map(d => rows.filter(r => r[peakKey] === d).length);
  const max = Math.max(...counts, 1);
  const avgPcts = days.map(d => {
    const vals = [];
    for (const r of rows) {
      const v = r.day_breakdown && r.day_breakdown[d] && r.day_breakdown[d][breakdownKey];
      if (typeof v === "number" && !Number.isNaN(v)) vals.push(v);
    }
    if (!vals.length) return null;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  });
  const W = 600,
    H = 220;
  const padL = 14,
    padR = 14,
    padT = 24,
    padB = 50;
  const innerW = W - padL - padR,
    innerH = H - padT - padB;
  const bw = innerW / days.length * 0.62;
  const gap = innerW / days.length;
  return /*#__PURE__*/React.createElement("div", {
    className: "chart-wrap"
  }, /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    className: "chart-svg"
  }, [0, 0.25, 0.5, 0.75, 1].map((p, i) => /*#__PURE__*/React.createElement("line", {
    key: i,
    x1: padL,
    x2: W - padR,
    y1: padT + innerH * (1 - p),
    y2: padT + innerH * (1 - p),
    className: "grid"
  })), days.map((d, i) => {
    const x = padL + i * gap + (gap - bw) / 2;
    const h = counts[i] / max * innerH;
    const y = padT + innerH - h;
    const top = Math.max(...counts) === counts[i] && counts[i] > 0;
    const avg = avgPcts[i];
    const avgText = avg == null ? "—" : (avg >= 0 ? "+" : "") + avg.toFixed(2) + "%";
    const expectedSign = mode === "low" ? -1 : 1;
    const inDirection = avg != null && Math.sign(avg) === expectedSign;
    const labelStyle = avg == null ? {
      fill: "var(--fg-3)",
      fontWeight: 500
    } : inDirection ? {
      fill: fillColor,
      opacity: top ? 1 : 0.85,
      fontWeight: top ? 700 : 600
    } : {
      fill: "var(--fg-3)",
      fontWeight: 500
    };
    return /*#__PURE__*/React.createElement("g", {
      key: d
    }, /*#__PURE__*/React.createElement("rect", {
      x: x,
      y: y,
      width: bw,
      height: h,
      rx: "4",
      fill: top ? fillColor : colors.bandSolid,
      opacity: top ? 1 : 0.55
    }), /*#__PURE__*/React.createElement("text", {
      x: x + bw / 2,
      y: y - 6,
      textAnchor: "middle",
      fontSize: "15",
      fontFamily: "ui-monospace, monospace",
      style: {
        fontWeight: top ? 700 : 500,
        fill: top ? fillColor : "var(--fg-2)"
      }
    }, counts[i]), /*#__PURE__*/React.createElement("text", {
      x: x + bw / 2,
      y: H - 26,
      textAnchor: "middle",
      fontSize: "15",
      style: {
        fontWeight: top ? 700 : 500,
        fill: top ? fillColor : "var(--fg-2)"
      }
    }, d), /*#__PURE__*/React.createElement("text", {
      x: x + bw / 2,
      y: H - 8,
      textAnchor: "middle",
      fontFamily: "ui-monospace, monospace",
      fontSize: "15",
      style: labelStyle
    }, avgText));
  })));
}

// ── Generic legs-based P/L diagram ─────────────────────────────────────────
function PLChart({
  legs,
  currentPrice,
  expectedMove,
  colors,
  strategyName = ""
}) {
  const W = 1000,
    H = 400;
  const padL = 64,
    padR = 24,
    padT = 56,
    padB = 64;
  const innerW = W - padL - padR,
    innerH = H - padT - padB;
  const uid = useMemo(() => "pl" + Math.floor(Math.random() * 1e6), []);
  const [hoverX, setHoverX] = useState(null);
  const optionStrikes = legs.filter(l => l.type !== "stock").map(l => l.strike);
  const minStrike = optionStrikes.length ? Math.min(...optionStrikes) : currentPrice * 0.9;
  const maxStrike = optionStrikes.length ? Math.max(...optionStrikes) : currentPrice * 1.1;
  const halfWidth = Math.max(maxStrike - currentPrice, currentPrice - minStrike, currentPrice * 0.10) * 1.7;
  const lower = Math.max(0.5, currentPrice - halfWidth);
  const upper = currentPrice + halfWidth;
  const curve = useMemo(() => window.OptionStrats.pnlCurve(legs, lower, upper, 240), [legs, lower, upper]);
  const {
    min: plMin,
    max: plMax
  } = useMemo(() => window.OptionStrats.pnlBounds(curve), [curve]);
  const headroom = Math.max(Math.abs(plMin), Math.abs(plMax)) * 0.25;
  const yMin = Math.min(plMin - headroom, -Math.abs(plMax) * 0.3);
  const yMax = Math.max(plMax + headroom, Math.abs(plMin) * 0.3);
  const xScale = v => padL + (v - lower) / (upper - lower) * innerW;
  const yScale = v => padT + (1 - (v - yMin) / (yMax - yMin)) * innerH;
  const zeroY = yScale(0);
  const ticks = useMemo(() => niceTicks(yMin, yMax, 7), [yMin, yMax]);
  const xTicks = useMemo(() => niceTicks(lower, upper, 8), [lower, upper]);
  const bes = useMemo(() => window.OptionStrats.breakEvens(curve), [curve]);
  const sigma = Math.max(expectedMove || currentPrice * 0.06, currentPrice * 0.01);
  const probPath = useMemo(() => {
    const pts = [];
    const peakY = (yMax - 0) * 0.75;
    for (let i = 0; i <= 120; i++) {
      const p = lower + i / 120 * (upper - lower);
      const z = (p - currentPrice) / sigma;
      const y = peakY * Math.exp(-0.5 * z * z);
      pts.push([xScale(p), yScale(y)]);
    }
    return pts;
  }, [lower, upper, sigma, currentPrice, yMin, yMax]);
  const hoverIdx = hoverX == null ? null : Math.max(0, Math.min(curve.length - 1, Math.round((hoverX - padL) / innerW * (curve.length - 1))));
  const hoverPt = hoverIdx != null ? curve[hoverIdx] : null;

  // Split curve into sign-uniform segments with zero-cross interpolation
  const pathSegments = useMemo(() => {
    const segs = [];
    if (curve.length === 0) return segs;
    let current = {
      sign: curve[0].pl >= 0,
      pts: [[curve[0].s, curve[0].pl]]
    };
    for (let i = 1; i < curve.length; i++) {
      const prev = curve[i - 1],
        pt = curve[i];
      const sign = pt.pl >= 0;
      if (sign !== current.sign && prev.pl !== pt.pl) {
        const t = -prev.pl / (pt.pl - prev.pl);
        const sCross = prev.s + t * (pt.s - prev.s);
        current.pts.push([sCross, 0]);
        segs.push(current);
        current = {
          sign,
          pts: [[sCross, 0], [pt.s, pt.pl]]
        };
      } else {
        current.pts.push([pt.s, pt.pl]);
      }
    }
    segs.push(current);
    return segs;
  }, [curve]);
  return /*#__PURE__*/React.createElement("div", {
    className: "chart-wrap pl-3d"
  }, /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    className: "chart-svg",
    onMouseLeave: () => setHoverX(null),
    onMouseMove: e => {
      const rect = e.currentTarget.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width * W;
      if (x >= padL && x <= W - padR) setHoverX(x);else setHoverX(null);
    }
  }, /*#__PURE__*/React.createElement("defs", null, /*#__PURE__*/React.createElement("linearGradient", {
    id: `${uid}-bg`,
    x1: "0",
    y1: "0",
    x2: "0",
    y2: "1"
  }, /*#__PURE__*/React.createElement("stop", {
    offset: "0%",
    stopColor: "#0d1530"
  }), /*#__PURE__*/React.createElement("stop", {
    offset: "100%",
    stopColor: "#060a1c"
  })), /*#__PURE__*/React.createElement("radialGradient", {
    id: `${uid}-prob`,
    cx: "50%",
    cy: "100%",
    r: "80%"
  }, /*#__PURE__*/React.createElement("stop", {
    offset: "0%",
    stopColor: "#3b6cd9",
    stopOpacity: "0.55"
  }), /*#__PURE__*/React.createElement("stop", {
    offset: "60%",
    stopColor: "#1d3a7a",
    stopOpacity: "0.32"
  }), /*#__PURE__*/React.createElement("stop", {
    offset: "100%",
    stopColor: "#1d3a7a",
    stopOpacity: "0"
  })), /*#__PURE__*/React.createElement("linearGradient", {
    id: `${uid}-profit`,
    x1: "0",
    y1: "0",
    x2: "0",
    y2: "1"
  }, /*#__PURE__*/React.createElement("stop", {
    offset: "0%",
    stopColor: "#22e07a",
    stopOpacity: "0.50"
  }), /*#__PURE__*/React.createElement("stop", {
    offset: "100%",
    stopColor: "#22e07a",
    stopOpacity: "0.05"
  })), /*#__PURE__*/React.createElement("linearGradient", {
    id: `${uid}-loss`,
    x1: "0",
    y1: "0",
    x2: "0",
    y2: "1"
  }, /*#__PURE__*/React.createElement("stop", {
    offset: "0%",
    stopColor: "#ff3b6b",
    stopOpacity: "0.05"
  }), /*#__PURE__*/React.createElement("stop", {
    offset: "100%",
    stopColor: "#7a1230",
    stopOpacity: "0.55"
  })), /*#__PURE__*/React.createElement("filter", {
    id: `${uid}-glow`,
    x: "-30%",
    y: "-30%",
    width: "160%",
    height: "160%"
  }, /*#__PURE__*/React.createElement("feGaussianBlur", {
    stdDeviation: "2.4",
    result: "b"
  }), /*#__PURE__*/React.createElement("feMerge", null, /*#__PURE__*/React.createElement("feMergeNode", {
    in: "b"
  }), /*#__PURE__*/React.createElement("feMergeNode", {
    in: "SourceGraphic"
  })))), /*#__PURE__*/React.createElement("rect", {
    x: "0",
    y: "0",
    width: W,
    height: H,
    fill: `url(#${uid}-bg)`,
    rx: "10"
  }), ticks.map((t, i) => /*#__PURE__*/React.createElement("line", {
    key: `g${i}`,
    x1: padL,
    x2: W - padR,
    y1: yScale(t),
    y2: yScale(t),
    stroke: "#3a5a99",
    strokeOpacity: "0.18",
    strokeWidth: "1"
  })), xTicks.map((t, i) => /*#__PURE__*/React.createElement("line", {
    key: `gx${i}`,
    x1: xScale(t),
    x2: xScale(t),
    y1: padT,
    y2: padT + innerH,
    stroke: "#3a5a99",
    strokeOpacity: "0.10",
    strokeWidth: "1"
  })), /*#__PURE__*/React.createElement("path", {
    d: `M ${probPath[0][0]} ${zeroY} ${probPath.map(p => `L ${p[0]} ${p[1]}`).join(" ")} L ${probPath[probPath.length - 1][0]} ${zeroY} Z`,
    fill: `url(#${uid}-prob)`
  }), /*#__PURE__*/React.createElement("path", {
    d: probPath.map((p, i) => `${i === 0 ? "M" : "L"} ${p[0]} ${p[1]}`).join(" "),
    fill: "none",
    stroke: "#6aa8ff",
    strokeOpacity: "0.7",
    strokeWidth: "1.4"
  }), pathSegments.map((seg, i) => {
    if (seg.pts.length < 2) return null;
    const grad = seg.sign ? `${uid}-profit` : `${uid}-loss`;
    const first = seg.pts[0],
      last = seg.pts[seg.pts.length - 1];
    const d = `M ${xScale(first[0])} ${zeroY} ` + seg.pts.map(p => `L ${xScale(p[0])} ${yScale(p[1])}`).join(" ") + ` L ${xScale(last[0])} ${zeroY} Z`;
    return /*#__PURE__*/React.createElement("path", {
      key: i,
      d: d,
      fill: `url(#${grad})`
    });
  }), /*#__PURE__*/React.createElement("line", {
    x1: padL,
    x2: W - padR,
    y1: zeroY,
    y2: zeroY,
    stroke: "white",
    strokeOpacity: "0.85",
    strokeWidth: "1.2"
  }), /*#__PURE__*/React.createElement("line", {
    x1: xScale(currentPrice),
    x2: xScale(currentPrice),
    y1: padT,
    y2: padT + innerH,
    stroke: "white",
    strokeOpacity: "0.55",
    strokeDasharray: "3 4",
    strokeWidth: "1"
  }), /*#__PURE__*/React.createElement("g", {
    transform: `translate(${xScale(currentPrice)}, ${padT + innerH + 18})`
  }, /*#__PURE__*/React.createElement("rect", {
    x: "-44",
    y: "-11",
    width: "88",
    height: "18",
    rx: "9",
    fill: "#0a1228",
    stroke: "white",
    strokeOpacity: "0.4"
  }), /*#__PURE__*/React.createElement("text", {
    x: "0",
    y: "3",
    textAnchor: "middle",
    fill: "white",
    opacity: "0.95",
    fontSize: "11",
    fontFamily: "ui-monospace, monospace"
  }, "spot $", currentPrice.toFixed(2))), pathSegments.map((seg, i) => {
    if (seg.pts.length < 2) return null;
    const stroke = seg.sign ? "#22e07a" : "#ff3057";
    const d = "M " + seg.pts.map(p => `${xScale(p[0])} ${yScale(p[1])}`).join(" L ");
    return /*#__PURE__*/React.createElement("path", {
      key: `l${i}`,
      d: d,
      fill: "none",
      stroke: stroke,
      strokeWidth: "2.4",
      filter: `url(#${uid}-glow)`
    });
  }), legs.filter(l => l.type !== "stock").map((leg, i) => {
    const isShort = leg.qty < 0;
    const color = leg.type === "call" ? "#5fd6ff" : "#ffd75f";
    // Stack labels in two rows. Row 0 sits 14px above the chart, row 1
    // sits 32px above so the chip never collides with the upper edge.
    const labelY = padT - (i % 2 === 0 ? 14 : 32);
    return /*#__PURE__*/React.createElement("g", {
      key: `s${i}`
    }, /*#__PURE__*/React.createElement("line", {
      x1: xScale(leg.strike),
      x2: xScale(leg.strike),
      y1: padT,
      y2: padT + innerH,
      stroke: color,
      strokeWidth: isShort ? "1.6" : "1.0",
      strokeDasharray: isShort ? null : "4 3",
      strokeOpacity: isShort ? "0.85" : "0.55"
    }), /*#__PURE__*/React.createElement("text", {
      x: xScale(leg.strike),
      y: labelY,
      textAnchor: "middle",
      fill: color,
      fontFamily: "ui-monospace, monospace",
      fontSize: "11",
      fontWeight: "600"
    }, isShort ? "−" : "+", leg.type === "call" ? "C" : "P", " $", leg.strike.toFixed(2), leg.dte > 7 ? `  ${leg.dte}d` : ""));
  }), bes.map((be, i) => {
    // Alternate above / below the zero line so two close B/E points
    // don't overlap. Push further away if too close to the spot label.
    const above = i % 2 === 0;
    const dy = above ? -10 : 18;
    return /*#__PURE__*/React.createElement("g", {
      key: `b${i}`
    }, /*#__PURE__*/React.createElement("circle", {
      cx: xScale(be),
      cy: zeroY,
      r: "4",
      fill: "white"
    }), /*#__PURE__*/React.createElement("text", {
      x: xScale(be),
      y: zeroY + dy,
      textAnchor: "middle",
      fill: "white",
      opacity: "0.92",
      fontSize: "11",
      fontFamily: "ui-monospace, monospace"
    }, "B/E $", be.toFixed(2)));
  }), ticks.map((t, i) => /*#__PURE__*/React.createElement("text", {
    key: `yt${i}`,
    x: padL - 10,
    y: yScale(t) + 4,
    fill: "#9fb6e0",
    fontSize: "11",
    fontFamily: "ui-monospace, monospace",
    textAnchor: "end"
  }, t < 0 ? "−" : "", "$", Math.abs(t).toFixed(2))), xTicks.map((t, i) => {
    // skip any tick whose label would collide with the spot pill
    const spotX = xScale(currentPrice);
    if (Math.abs(xScale(t) - spotX) < 50) return null;
    return /*#__PURE__*/React.createElement("text", {
      key: `xt${i}`,
      x: xScale(t),
      y: padT + innerH + 22,
      fill: "#9fb6e0",
      fontSize: "11",
      fontFamily: "ui-monospace, monospace",
      textAnchor: "middle"
    }, "$", t.toFixed(2));
  }), hoverPt && /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("line", {
    x1: xScale(hoverPt.s),
    x2: xScale(hoverPt.s),
    y1: padT,
    y2: padT + innerH,
    stroke: "white",
    strokeOpacity: "0.4",
    strokeDasharray: "2 3"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: xScale(hoverPt.s),
    cy: yScale(hoverPt.pl),
    r: "4.5",
    fill: hoverPt.pl >= 0 ? "#22e07a" : "#ff3057",
    stroke: "white",
    strokeWidth: "1.5"
  })), /*#__PURE__*/React.createElement("g", {
    transform: `translate(${padL + 10}, ${padT + 10})`
  }, /*#__PURE__*/React.createElement("rect", {
    x: "0",
    y: "0",
    width: "240",
    height: "22",
    rx: "11",
    fill: "#0a1228",
    stroke: "#2a4a85",
    strokeOpacity: "0.6"
  }), /*#__PURE__*/React.createElement("polygon", {
    points: "10,16 17,7 24,16",
    fill: "#6aa8ff"
  }), /*#__PURE__*/React.createElement("text", {
    x: "32",
    y: "15",
    fill: "#bcd0f5",
    fontSize: "11",
    fontFamily: "ui-sans-serif, system-ui"
  }, strategyName ? strategyName + " · " : "", "\xB1$", sigma.toFixed(2), " 1\u03C3"))), hoverPt && /*#__PURE__*/React.createElement("div", {
    className: "chart-tooltip pl-tip",
    style: {
      left: `${xScale(hoverPt.s) / W * 100}%`
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "Stock at expiry"), /*#__PURE__*/React.createElement("b", null, "$", hoverPt.s.toFixed(2))), /*#__PURE__*/React.createElement("div", {
    className: "tt-row"
  }, /*#__PURE__*/React.createElement("span", null, "P/L"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: hoverPt.pl >= 0 ? "#22e07a" : "#ff3057"
    }
  }, hoverPt.pl >= 0 ? "+" : "", "$", hoverPt.pl.toFixed(2)))));
}

// ── Theta vs gamma timing panel ────────────────────────────────────────────
// Models the suggested call's premium across each remaining trading day
// of the current week. Spot path uses the historical day-of-week pattern:
//   spot[day] = baseline * (1 + dayReturn[day] / 100)
// where dayReturn is the median historical close return on that weekday.
// On the typical-high-day, the high return (not close) is used to capture
// the rally Jerry is trying to time around.
//
// Outputs three scenarios: "sell now + close pre-rally", "sell now + hold
// to expiry", "wait + sell on rally day". Recommendation picks the highest
// expected captured credit.
function ThetaPanel({
  rows,
  sugCall,
  sugPut,
  callIv,
  putIv,
  side,
  currentPrice,
  baselinePrice,
  expDate,
  FRONT_DTE,
  typicalHighDay,
  typicalLowDay,
  medianHigh,
  medianLow,
  colors
}) {
  const bs = window.OptionStrats?.bsPrice;
  if (!bs) return /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      padding: 12
    }
  }, "BS engine not loaded.");

  // Side selector — call by default, but user can flip to put.
  const isCall = side !== "put";
  const strike = isCall ? sugCall : sugPut;
  const iv = (isCall ? callIv : putIv) || 0.30;
  const targetDay = isCall ? typicalHighDay : typicalLowDay;
  const targetMove = isCall ? medianHigh : medianLow;

  // Build per-day spot estimates for Mon..Fri ahead of expiry. We only model
  // the trading days remaining until expiration. If today is mid-week we
  // start there.
  const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  const today = new Date();
  const todayDow = today.getDay(); // 0=Sun ... 6=Sat
  const expDow = expDate.getDay() || 5; // expiration day of week

  // Compute per-day return estimate from historical rows. Each weekday gets
  // the median *close-vs-baseline* return on that day. If there's no data
  // for a day, fall back to 0 (assume flat).
  const dayMedianClose = (() => {
    const m = {};
    dayNames.forEach((d, i) => {
      // Approximate: weeks where high or low landed on this day, use
      // partial-week return between baseline and that day's level. Without
      // intraday data we approximate using the linear fraction of the
      // close return distributed across days — except on the typical-high
      // day where we use the high return.
      const sameHigh = rows.filter(r => r.high_day_name === d).map(r => r.high_return);
      const sameLow = rows.filter(r => r.low_day_name === d).map(r => r.low_return);
      const avgHigh = sameHigh.length ? sameHigh.reduce((a, b) => a + b, 0) / sameHigh.length : 0;
      const avgLow = sameLow.length ? sameLow.reduce((a, b) => a + b, 0) / sameLow.length : 0;
      // Use the higher-magnitude of the two so the day's "typical excursion"
      // dominates. This reflects the day's historical contribution.
      m[d] = Math.abs(avgHigh) >= Math.abs(avgLow) ? avgHigh : avgLow;
    });
    return m;
  })();

  // Build trading-day schedule from today (or next trading day if weekend)
  // through expiration day.
  function nextWeekday(from) {
    const d = new Date(from);
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
    return d;
  }
  const startDay = nextWeekday(today);
  const days = [];
  let cursor = new Date(startDay);
  while (cursor <= expDate) {
    if (cursor.getDay() >= 1 && cursor.getDay() <= 5) {
      const dowName = dayNames[cursor.getDay() - 1];
      // DTE in calendar days. BS gets 1/365 minimum to avoid div-by-zero.
      const msToExp = expDate.getTime() - cursor.getTime();
      const dte = Math.max(1, Math.ceil(msToExp / 86400000));
      // Spot estimate: baseline * (1 + median return for this day)
      const ret = dayMedianClose[dowName] || 0;
      const spot = baselinePrice * (1 + ret / 100);
      const T = dte / 365;
      const price = bs(spot, strike, T, iv, isCall, 0.045);
      days.push({
        date: new Date(cursor),
        dowName,
        dte,
        spot,
        price,
        isTargetDay: dowName === targetDay
      });
    }
    cursor.setDate(cursor.getDate() + 1);
  }
  if (days.length < 2) {
    return /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        padding: 16,
        fontSize: 13
      }
    }, "Not enough trading days left this cycle to model theta capture. Pick a later expiration.");
  }

  // Three scenarios.
  const entry = days[0];
  const expDay = days[days.length - 1];
  // 1. Sell now + hold to expiry. Captured = entry premium - intrinsic at exp.
  const expIntrinsic = isCall ? Math.max(expDay.spot - strike, 0) : Math.max(strike - expDay.spot, 0);
  const scenarioHold = {
    label: "Sell now, hold to expiry",
    entry: entry.price,
    exitDay: expDay,
    exit: expIntrinsic,
    captured: entry.price - expIntrinsic,
    daysAtRisk: days.length
  };
  // 2. Sell now + close at end of day BEFORE the typical rally / target day.
  //    Find the last day where dowName != targetDay and there is at least
  //    one more day after it (otherwise we're at expiry anyway).
  let preTargetIdx = -1;
  for (let i = days.length - 1; i >= 1; i--) {
    if (days[i].isTargetDay) {
      preTargetIdx = i - 1;
      break;
    }
  }
  const scenarioClose = preTargetIdx >= 0 ? {
    label: `Sell now, close ${days[preTargetIdx].dowName} EOD`,
    entry: entry.price,
    exitDay: days[preTargetIdx],
    exit: days[preTargetIdx].price,
    captured: entry.price - days[preTargetIdx].price,
    daysAtRisk: preTargetIdx + 1
  } : null;
  // 3. Wait, sell on target day (typical-high day for calls). Hold to expiry.
  const targetIdx = days.findIndex(d => d.isTargetDay);
  const scenarioWait = targetIdx >= 0 ? {
    label: `Wait, sell ${days[targetIdx].dowName} on rally`,
    entry: days[targetIdx].price,
    exitDay: expDay,
    exit: expIntrinsic,
    captured: days[targetIdx].price - expIntrinsic,
    daysAtRisk: days.length - targetIdx
  } : null;
  const scenarios = [scenarioHold];
  if (scenarioClose) scenarios.push(scenarioClose);
  if (scenarioWait && targetIdx > 0) scenarios.push(scenarioWait);

  // Pick the winner (highest captured premium per day at risk, but display
  // raw $ captured as the headline number).
  const winner = scenarios.reduce((a, b) => b.captured > a.captured ? b : a);

  // Chart layout
  const W = 720,
    H = 200;
  const padL = 44,
    padR = 16,
    padT = 16,
    padB = 32;
  const innerW = W - padL - padR,
    innerH = H - padT - padB;
  const xs = days.map((_, i) => i);
  const xScale = i => padL + innerW * i / Math.max(1, days.length - 1);
  const minPrice = Math.min(0, ...days.map(d => d.price));
  const maxPrice = Math.max(...days.map(d => d.price)) * 1.15;
  const yScale = v => padT + innerH - (v - minPrice) / (maxPrice - minPrice || 1) * innerH;
  const linePath = days.map((d, i) => `${i === 0 ? "M" : "L"} ${xScale(i)} ${yScale(d.price)}`).join(" ");

  // Theta per day (forward-difference of price) — last entry has no next.
  const thetas = days.map((d, i) => i + 1 < days.length ? d.price - days[i + 1].price : null);
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12
    }
  }, "Modeling the ", isCall ? "call" : "put", " at ", /*#__PURE__*/React.createElement("b", {
    style: {
      color: "var(--fg)"
    }
  }, "$", strike.toFixed(2)), " · ", "IV ", /*#__PURE__*/React.createElement("b", {
    style: {
      color: "var(--fg)"
    }
  }, (iv * 100).toFixed(1), "%"), " · ", "target day ", /*#__PURE__*/React.createElement("b", {
    style: {
      color: isCall ? "var(--up)" : "var(--down)"
    }
  }, targetDay))), /*#__PURE__*/React.createElement("div", {
    className: "chart-wrap"
  }, /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    className: "chart-svg"
  }, [0, 0.25, 0.5, 0.75, 1].map((p, i) => /*#__PURE__*/React.createElement("line", {
    key: i,
    x1: padL,
    x2: W - padR,
    y1: padT + innerH * (1 - p),
    y2: padT + innerH * (1 - p),
    className: "grid"
  })), days.map((d, i) => d.isTargetDay && /*#__PURE__*/React.createElement("rect", {
    key: `hl${i}`,
    x: xScale(i) - 16,
    y: padT,
    width: "32",
    height: innerH,
    fill: isCall ? colors.up : colors.down,
    opacity: "0.10",
    rx: "3"
  })), /*#__PURE__*/React.createElement("path", {
    d: linePath,
    fill: "none",
    stroke: colors.accent,
    strokeWidth: "2"
  }), days.map((d, i) => /*#__PURE__*/React.createElement("g", {
    key: i
  }, /*#__PURE__*/React.createElement("circle", {
    cx: xScale(i),
    cy: yScale(d.price),
    r: "4",
    fill: d.isTargetDay ? isCall ? colors.up : colors.down : colors.accent,
    stroke: "var(--bg-2)",
    strokeWidth: "1.5"
  }), /*#__PURE__*/React.createElement("text", {
    x: xScale(i),
    y: yScale(d.price) - 10,
    textAnchor: "middle",
    fontSize: "10",
    fontFamily: "ui-monospace, monospace",
    fill: "var(--fg-2)"
  }, "$", d.price.toFixed(2)), /*#__PURE__*/React.createElement("text", {
    x: xScale(i),
    y: H - 16,
    textAnchor: "middle",
    className: "axis-text",
    style: {
      fontWeight: d.isTargetDay ? 700 : 500,
      fill: d.isTargetDay ? isCall ? colors.up : colors.down : "currentColor"
    }
  }, d.dowName), /*#__PURE__*/React.createElement("text", {
    x: xScale(i),
    y: H - 4,
    textAnchor: "middle",
    fontSize: "9",
    fill: "var(--fg-3)",
    fontFamily: "ui-monospace, monospace"
  }, d.dte, "d"))), [minPrice, (minPrice + maxPrice) / 2, maxPrice].map((v, i) => /*#__PURE__*/React.createElement("text", {
    key: `yt${i}`,
    x: padL - 6,
    y: yScale(v) + 3,
    textAnchor: "end",
    className: "axis-text"
  }, "$", v.toFixed(2))))), /*#__PURE__*/React.createElement("div", {
    className: "theta-scenarios"
  }, scenarios.map((s, i) => {
    const isWinner = s.label === winner.label;
    return /*#__PURE__*/React.createElement("div", {
      key: i,
      className: `theta-scenario ${isWinner ? "winner" : ""}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "theta-scenario-name"
    }, s.label), /*#__PURE__*/React.createElement("div", {
      className: "theta-scenario-stats"
    }, /*#__PURE__*/React.createElement("span", null, "Entry ", /*#__PURE__*/React.createElement("b", null, "$", s.entry.toFixed(2))), /*#__PURE__*/React.createElement("span", null, "Exit ", /*#__PURE__*/React.createElement("b", null, "$", s.exit.toFixed(2))), /*#__PURE__*/React.createElement("span", null, "Days ", /*#__PURE__*/React.createElement("b", null, s.daysAtRisk))), /*#__PURE__*/React.createElement("div", {
      className: "theta-scenario-cap",
      style: {
        color: s.captured >= 0 ? "var(--up)" : "var(--down)"
      }
    }, s.captured >= 0 ? "+" : "", "$", s.captured.toFixed(2), "/sh captured"), isWinner && /*#__PURE__*/React.createElement("div", {
      className: "theta-scenario-badge"
    }, "Best expected capture"));
  })), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      lineHeight: 1.55,
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      color: "var(--fg)"
    }
  }, "Read."), " ", "Premium at expiration is intrinsic only, $", expIntrinsic.toFixed(2), ".", " ", "Today's mid is $", entry.price.toFixed(2), ".", " ", "Theta from today through expiry totals $", (entry.price - expIntrinsic).toFixed(2), " if the spot path follows history.", scenarioClose && /*#__PURE__*/React.createElement(React.Fragment, null, " ", " ", "Closing ", scenarioClose.exitDay.dowName, " EOD captures", /*#__PURE__*/React.createElement("b", {
    style: {
      color: "var(--fg)"
    }
  }, " $", scenarioClose.captured.toFixed(2)), " ", "and removes the ", targetDay, " gamma exposure."), " ", "IV used is the chain's marked IV, not a forward forecast.", " ", "Spot path is purely historical median behavior, actuals will deviate."));
}
Object.assign(window, {
  PriceChart,
  ReturnsChart,
  DayBarChart,
  PLChart,
  ThetaPanel,
  fmt$,
  fmtPct,
  fmtDate,
  niceTicks
});
})();
