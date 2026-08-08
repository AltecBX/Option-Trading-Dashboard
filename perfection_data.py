"""perfection_data.py — provider adapters for the Priced-for-Perfection model.

Gathers REAL data from the providers this app already uses — Schwab (live
quote, option chain), yfinance (estimates, revisions, earnings history,
quarterly statements, share-count history, analyst/short data) and the
app's own stored IV history — prepares the raw-input dict, and calls
perfection.assemble().

Honesty rules (same contract as the rest of the app):
  • Every fetch is individually guarded; a failed source becomes None plus
    an entry in `data_issues` — never a fabricated value.
  • Whisper numbers: NO legitimate free whisper source exists, so none is
    configured and none is invented. The payload says so explicitly.
  • Historical guidance and per-event revenue consensus are not available
    from the configured providers — disclosed in `limitations`.
  • Valuation history percentiles are TRAILING multiples rebuilt from
    daily price × actual share count and quarterly filings (forward-
    multiple history is paid data) — labeled as such.
  • Point-in-time: pre-earnings snapshots are APPENDED (never rewritten)
    to data_dir/perfection/<SYM>.jsonl, so each day's view is preserved
    with its own as-of stamps; per-event "vs implied move" comparisons are
    only made when a stored same-day snapshot exists (they accumulate
    going forward — nothing is backfilled from hindsight).

perfection.py stays pure; everything network-touching lives here.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception:  # pragma: no cover
    _YF_OK = False

import perfection
from perfection import SECTOR_ETF, _num

_SCHWAB_GETTER = None
_PEERS_GETTER = None          # () -> watchlist_table board dict | None
_IV_HISTORY_LOAD = None       # (symbol) -> [{date, iv}]
_SNAP_DIR: Path | None = None
_SESSION_FACTORY = None       # optional: () -> requests.Session (sandbox/tests)

_LOCK = threading.RLock()
_CACHE: dict = {}
TTL = {"fund": 3600, "est": 1800, "px": 900, "chain": 300, "shares": 86400, "bench": 3600}


def configure(schwab_getter, data_dir, peers_getter=None, iv_history_load=None,
              session_factory=None) -> None:
    global _SCHWAB_GETTER, _PEERS_GETTER, _SNAP_DIR, _IV_HISTORY_LOAD, _SESSION_FACTORY
    _SCHWAB_GETTER = schwab_getter
    _PEERS_GETTER = peers_getter
    _IV_HISTORY_LOAD = iv_history_load
    _SESSION_FACTORY = session_factory
    if data_dir:
        _SNAP_DIR = Path(data_dir) / "perfection"


def _cache_get(key):
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < hit[2]:
            return hit[1]
    return None


def _cache_set(key, value, ttl):
    with _LOCK:
        _CACHE[key] = (time.time(), value, ttl)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _yft(symbol: str):
    if _SESSION_FACTORY is not None:
        return yf.Ticker(symbol, session=_SESSION_FACTORY())
    return yf.Ticker(symbol)


def _row(df, names):
    """First matching row (as a Series) from a statement DataFrame."""
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def _ser_vals(row, n=None):
    """Statement row → list of floats, NEWEST FIRST (yfinance column order)."""
    if row is None:
        return []
    out = []
    for v in list(row.values)[: (n or len(row))]:
        out.append(_num(v))
    return out


def _slope_pp_per_q(vals_newest_first: list) -> float | None:
    """Least-squares slope (pp per quarter) over a short margin series."""
    ys = [v for v in vals_newest_first if v is not None]
    if len(ys) < 3:
        return None
    ys = list(reversed(ys))                      # oldest → newest
    n = len(ys)
    xbar = (n - 1) / 2.0
    ybar = sum(ys) / n
    num = sum((i - xbar) * (y - ybar) for i, y in enumerate(ys))
    den = sum((i - xbar) ** 2 for i in range(n))
    return num / den * 100.0 if den else None    # margins are decimals → pp


# ───────────────────────────── data blocks ─────────────────────────────────

def _fundamentals(symbol: str, issues: list) -> dict:
    key = f"fund:{symbol}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    out = {"asof": _now_iso()}
    t = _yft(symbol)
    info = {}
    try:
        info = t.info or {}
    except Exception as exc:  # noqa: BLE001
        issues.append(f"yfinance info: {exc}")
    for k_out, k_in in (
        ("company", "shortName"), ("sector", "sector"), ("industry", "industry"),
        ("market_cap", "marketCap"), ("enterprise_value", "enterpriseValue"),
        ("forward_pe", "forwardPE"), ("trailing_pe", "trailingPE"),
        ("peg", "trailingPegRatio"), ("ev_to_revenue", "enterpriseToRevenue"),
        ("ev_to_ebitda", "enterpriseToEbitda"), ("share_count", "sharesOutstanding"),
        ("total_cash", "totalCash"), ("total_debt", "totalDebt"),
        ("short_pct_float_raw", "shortPercentOfFloat"), ("days_to_cover", "shortRatio"),
        ("institutional_raw", "heldPercentInstitutions"),
        ("target_mean", "targetMeanPrice"), ("target_high", "targetHighPrice"),
        ("target_low", "targetLowPrice"), ("analyst_count", "numberOfAnalystOpinions"),
        ("price_info", "currentPrice"),
    ):
        out[k_out] = info.get(k_in) if not isinstance(info.get(k_in), str) or k_out in ("company", "sector", "industry") else None
    out["short_pct_float"] = _num(info.get("shortPercentOfFloat"))
    if out["short_pct_float"] is not None:
        out["short_pct_float"] *= 100.0
    out["institutional_pct"] = _num(info.get("heldPercentInstitutions"))
    if out["institutional_pct"] is not None:
        out["institutional_pct"] *= 100.0
    if out.get("total_cash") is not None and out.get("total_debt") is not None:
        out["net_debt"] = (out["total_debt"] or 0) - (out["total_cash"] or 0)
    else:
        out["net_debt"] = None

    # Quarterly statements (5-6 periods) + annual (4y) for history depth.
    qi = qc = qb = ai = ac = None
    try:
        qi = t.quarterly_income_stmt
        qc = t.quarterly_cashflow
        qb = t.quarterly_balance_sheet
        ai = t.income_stmt
        ac = t.cashflow
    except Exception as exc:  # noqa: BLE001
        issues.append(f"yfinance statements: {exc}")
    rev_q = _ser_vals(_row(qi, ["Total Revenue", "Operating Revenue"]))
    gp_q = _ser_vals(_row(qi, ["Gross Profit"]))
    oi_q = _ser_vals(_row(qi, ["Operating Income", "Total Operating Income As Reported"]))
    ni_q = _ser_vals(_row(qi, ["Net Income", "Net Income Common Stockholders"]))
    ocf_q = _ser_vals(_row(qc, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]))
    capex_q = _ser_vals(_row(qc, ["Capital Expenditure"]))
    sbc_q = _ser_vals(_row(qc, ["Stock Based Compensation"]))
    inv_q = _ser_vals(_row(qb, ["Inventory"]))
    ca_q = _ser_vals(_row(qb, ["Current Assets", "Total Current Assets"]))
    cl_q = _ser_vals(_row(qb, ["Current Liabilities", "Total Current Liabilities"]))
    out["stmt_asof"] = None
    try:
        if qi is not None and not qi.empty:
            out["stmt_asof"] = str(qi.columns[0].date())
    except Exception:
        pass

    def _win(vals, start):
        xs = vals[start:start + 4]
        return sum(xs) if len(xs) == 4 and all(v is not None for v in xs) else None

    # Right after a report the provider's newest column is often PARTIAL
    # (cashflow filled, income not yet). Find the first offset where the
    # revenue window is complete and compute EVERY ratio over that same
    # aligned window — never mixing windows across statements.
    start = next((i for i in (0, 1, 2) if _win(rev_q, i) is not None), None)
    fcf_row_q = _ser_vals(_row(qc, ["Free Cash Flow"]))
    if start is None:
        rev_ttm = ocf_ttm = capex_ttm = fcf_ttm = ni_ttm = sbc_ttm = None
    else:
        rev_ttm = _win(rev_q, start)
        ocf_ttm = _win(ocf_q, start)
        capex_ttm = _win(capex_q, start)         # negative by convention
        fcf_ttm = (ocf_ttm + capex_ttm) if (ocf_ttm is not None and capex_ttm is not None) \
            else _win(fcf_row_q, start)          # provider's own FCF row as fallback
        ni_ttm = _win(ni_q, start)
        sbc_ttm = _win(sbc_q, start)
    out["stmt_lag_quarters"] = start
    out.update({
        "revenue_ttm": rev_ttm, "ocf_ttm": ocf_ttm, "fcf_ttm": fcf_ttm, "ni_ttm": ni_ttm,
        "rev_quarters": rev_q, "ni_quarters": ni_q,
        "fcf_margin_ttm": (fcf_ttm / rev_ttm) if (fcf_ttm is not None and rev_ttm) else None,
        "gross_margin_slope_pp_q": _slope_pp_per_q(
            [(g / r) if (g is not None and r) else None for g, r in zip(gp_q, rev_q)]),
        "op_margin_slope_pp_q": _slope_pp_per_q(
            [(o / r) if (o is not None and r) else None for o, r in zip(oi_q, rev_q)]),
        "sbc_pct_ocf": (sbc_ttm / ocf_ttm * 100.0) if (sbc_ttm is not None and ocf_ttm) else None,
        "capex_pct_revenue": (abs(capex_ttm) / rev_ttm * 100.0) if (capex_ttm is not None and rev_ttm) else None,
        "ocf_conversion": (ocf_ttm / ni_ttm) if (ocf_ttm is not None and ni_ttm and ni_ttm > 0) else None,
        "conversion_quarters": len([v for v in rev_q if v is not None]),
    })
    # Annual FCF margins (4y) for "best margin" + revenue CAGR 3y.
    rev_a = _ser_vals(_row(ai, ["Total Revenue", "Operating Revenue"]))
    ocf_a = _ser_vals(_row(ac, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]))
    capex_a = _ser_vals(_row(ac, ["Capital Expenditure"]))
    fcf_a = _ser_vals(_row(ac, ["Free Cash Flow"]))
    margins = []
    for i, r in enumerate(rev_a):
        o = ocf_a[i] if i < len(ocf_a) else None
        cx = capex_a[i] if i < len(capex_a) else None
        f = (o + cx) if (o is not None and cx is not None) else (fcf_a[i] if i < len(fcf_a) else None)
        if r and f is not None:
            margins.append(f / r)
    if out["fcf_margin_ttm"] is not None:
        margins.append(out["fcf_margin_ttm"])
    out["fcf_margin_best"] = max(margins[:4]) if margins else None
    out["fcf_margins_annual"] = margins
    if len(rev_a) >= 4 and rev_a[0] and rev_a[3]:
        try:
            out["revenue_cagr_3y"] = (rev_a[0] / rev_a[3]) ** (1.0 / 3.0) - 1.0
        except Exception:
            out["revenue_cagr_3y"] = None
    else:
        out["revenue_cagr_3y"] = None
    # Growth-vs-conversion gaps (yoy over the SAME aligned window offset).
    s0 = start or 0
    def _at(vals, i):
        return vals[i] if i < len(vals) and vals[i] is not None else None
    if _at(rev_q, s0) and _at(rev_q, s0 + 4):
        rev_yoy = (rev_q[s0] / rev_q[s0 + 4] - 1.0) * 100.0
        out["rev_yoy_pct"] = rev_yoy
        fcf_now = (ocf_q[s0] + capex_q[s0]) if (_at(ocf_q, s0) is not None
                                                and _at(capex_q, s0) is not None) else _at(fcf_row_q, s0)
        fcf_ago = (ocf_q[s0 + 4] + capex_q[s0 + 4]) if (_at(ocf_q, s0 + 4) is not None
                                                        and _at(capex_q, s0 + 4) is not None) else _at(fcf_row_q, s0 + 4)
        if fcf_now is not None and fcf_ago is not None and fcf_ago > 0:
            out["rev_vs_fcf_growth_gap"] = rev_yoy - (fcf_now / fcf_ago - 1.0) * 100.0
        else:
            out["rev_vs_fcf_growth_gap"] = None
        if _at(ni_q, s0) is not None and _at(ni_q, s0 + 4) and ni_q[s0 + 4] > 0:
            out["rev_vs_eps_growth_gap"] = round(rev_yoy - (ni_q[s0] / ni_q[s0 + 4] - 1.0) * 100.0, 1)
        else:
            out["rev_vs_eps_growth_gap"] = None
        if _at(inv_q, s0) and _at(inv_q, s0 + 4):
            out["inventory_vs_rev_growth_gap"] = (inv_q[s0] / inv_q[s0 + 4] - 1.0) * 100.0 - rev_yoy
        else:
            out["inventory_vs_rev_growth_gap"] = None
    else:
        out["rev_yoy_pct"] = out["rev_vs_fcf_growth_gap"] = None
        out["rev_vs_eps_growth_gap"] = out["inventory_vs_rev_growth_gap"] = None
    if len(ca_q) >= 2 and len(cl_q) >= 2 and None not in (ca_q[0], cl_q[0], ca_q[1], cl_q[1]):
        out["working_capital_delta"] = (ca_q[0] - cl_q[0]) - (ca_q[1] - cl_q[1])
    else:
        out["working_capital_delta"] = None

    # Analyst ratings + recent rating actions.
    try:
        rec = t.recommendations
        if rec is not None and not rec.empty:
            r0 = rec.iloc[0]
            sb, b = _num(r0.get("strongBuy")) or 0, _num(r0.get("buy")) or 0
            h = _num(r0.get("hold")) or 0
            s, ss = _num(r0.get("sell")) or 0, _num(r0.get("strongSell")) or 0
            total = sb + b + h + s + ss
            out["ratings"] = {"strong_buy": int(sb), "buy": int(b), "hold": int(h),
                              "sell": int(s), "strong_sell": int(ss)}
            out["buy_ratio_pct"] = (sb + b) / total * 100.0 if total else None
    except Exception as exc:  # noqa: BLE001
        issues.append(f"yfinance recommendations: {exc}")
    try:
        ud = t.upgrades_downgrades
        if ud is not None and not ud.empty:
            cutoff = pd.Timestamp.now(tz=ud.index.tz) - pd.Timedelta(days=30) if ud.index.tz \
                else pd.Timestamp.now() - pd.Timedelta(days=30)
            recent = ud[ud.index >= cutoff]
            ups = int((recent["Action"].str.lower() == "up").sum())
            downs = int((recent["Action"].str.lower() == "down").sum())
            out["pt_changes_net_30d"] = ups - downs
            out["rating_actions_30d"] = {"up": ups, "down": downs}
    except Exception as exc:  # noqa: BLE001
        issues.append(f"yfinance upgrades_downgrades: {exc}")
    _cache_set(key, out, TTL["fund"])
    return out


def _estimates(symbol: str, issues: list) -> dict:
    key = f"est:{symbol}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    out = {"asof": _now_iso()}
    t = _yft(symbol)

    def _get(name, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            issues.append(f"yfinance {name}: {exc}")
            return None

    ee = _get("earnings_estimate", lambda: t.earnings_estimate)
    re_ = _get("revenue_estimate", lambda: t.revenue_estimate)
    tr = _get("eps_trend", lambda: t.eps_trend)
    rv = _get("eps_revisions", lambda: t.eps_revisions)

    def _cell(df, row, col):
        try:
            if df is None or row not in df.index or col not in df.columns:
                return None
            return _num(df.loc[row, col])
        except Exception:
            return None

    out["consensus_eps"] = _cell(ee, "0q", "avg")
    out["consensus_eps_low"] = _cell(ee, "0q", "low")
    out["consensus_eps_high"] = _cell(ee, "0q", "high")
    out["consensus_eps_analysts"] = _cell(ee, "0q", "numberOfAnalysts")
    out["consensus_eps_yr_ago"] = _cell(ee, "0q", "yearAgoEps")
    out["consensus_eps_growth"] = _cell(ee, "0q", "growth")
    out["consensus_revenue"] = _cell(re_, "0q", "avg")
    out["consensus_rev_q_growth"] = _cell(re_, "0q", "growth")
    out["consensus_rev_growth_fy0"] = _cell(re_, "0y", "growth")
    out["consensus_rev_growth_fy1"] = _cell(re_, "+1y", "growth")
    out["consensus_rev_fy1"] = _cell(re_, "+1y", "avg")
    # Horizon comparison uses the geometric mean of the fiscal years analysts
    # actually publish — one explosive single year would otherwise dominate a
    # 5-year CAGR comparison.
    gs = [g for g in (out["consensus_rev_growth_fy0"], out["consensus_rev_growth_fy1"])
          if g is not None and g > -1]
    if gs:
        prod = 1.0
        for g in gs:
            prod *= (1.0 + g)
        out["consensus_rev_growth"] = prod ** (1.0 / len(gs)) - 1.0
    else:
        out["consensus_rev_growth"] = None
    avg = out["consensus_eps"]
    lo, hi = out["consensus_eps_low"], out["consensus_eps_high"]
    out["eps_dispersion_pct"] = (hi - lo) / abs(avg) * 100.0 if (avg and lo is not None and hi is not None) else None
    cur = _cell(tr, "0q", "current")
    out["eps_rev_pct_7d"] = ((cur - _cell(tr, "0q", "7daysAgo")) / abs(_cell(tr, "0q", "7daysAgo")) * 100.0
                             if cur is not None and _cell(tr, "0q", "7daysAgo") else None)
    out["eps_rev_pct_30d"] = ((cur - _cell(tr, "0q", "30daysAgo")) / abs(_cell(tr, "0q", "30daysAgo")) * 100.0
                              if cur is not None and _cell(tr, "0q", "30daysAgo") else None)
    out["eps_rev_pct_60d"] = ((cur - _cell(tr, "0q", "60daysAgo")) / abs(_cell(tr, "0q", "60daysAgo")) * 100.0
                              if cur is not None and _cell(tr, "0q", "60daysAgo") else None)
    out["eps_rev_pct_90d"] = ((cur - _cell(tr, "0q", "90daysAgo")) / abs(_cell(tr, "0q", "90daysAgo")) * 100.0
                              if cur is not None and _cell(tr, "0q", "90daysAgo") else None)
    up30 = _cell(rv, "0q", "upLast30days")
    dn30 = _cell(rv, "0q", "downLast30days")
    out["revisions_up_30d"] = int(up30) if up30 is not None else None
    out["revisions_down_30d"] = int(dn30) if dn30 is not None else None
    out["revisions_up_minus_down"] = (up30 - dn30) if (up30 is not None and dn30 is not None) else None
    if out["eps_rev_pct_30d"] is not None:
        out["revisions_direction"] = "up" if out["eps_rev_pct_30d"] > 0.2 else \
            ("down" if out["eps_rev_pct_30d"] < -0.2 else "flat")
    else:
        out["revisions_direction"] = None

    # Earnings events: next + past, with report-clock session detection.
    events, next_ev = [], None
    ed = _get("earnings_dates", lambda: t.get_earnings_dates(limit=14))
    if ed is not None and not getattr(ed, "empty", True):
        today = date.today()
        for ts in ed.index:
            try:
                d = ts.date()
                hour = ts.hour
            except Exception:
                continue
            session = "AMC" if hour >= 15 else ("BMO" if 0 < hour <= 10 else "unknown")
            row = ed.loc[ts]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            rec = {
                "date": d.isoformat(), "session": session,
                "eps_estimate": _num(row.get("EPS Estimate")),
                "eps_actual": _num(row.get("Reported EPS")),
                "surprise_pct": _num(row.get("Surprise(%)")),
            }
            if rec["surprise_pct"] is not None and abs(rec["surprise_pct"]) < 1.0:
                rec["surprise_pct"] = rec["surprise_pct"] * 100.0   # provider returns a ratio
            if d >= today and (next_ev is None or d < date.fromisoformat(next_ev["date"])):
                next_ev = rec
            elif d < today and rec["eps_actual"] is not None:
                events.append(rec)
        events.sort(key=lambda e: e["date"], reverse=True)
    out["next_event"] = next_ev
    out["events"] = events[:10]
    _cache_set(key, out, TTL["est"])
    return out


def _benchmark_closes(etf: str, issues: list):
    key = f"bench:{etf}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    try:
        h = _yft(etf).history(period="3y", auto_adjust=True)
        closes = h["Close"] if h is not None and not h.empty else None
    except Exception as exc:  # noqa: BLE001
        issues.append(f"yfinance {etf} history: {exc}")
        closes = None
    _cache_set(key, closes, TTL["bench"])
    return closes


def _prices(symbol: str, sector: str | None, events: list, issues: list) -> dict:
    """Price/momentum block + per-event reactions. Split-safe: auto_adjust."""
    key = f"px:{symbol}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    out = {"asof": _now_iso()}
    try:
        h = _yft(symbol).history(period="3y", auto_adjust=True)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"yfinance price history: {exc}")
        h = None
    if h is None or h.empty or len(h) < 60:
        out["ok"] = False
        _cache_set(key, out, TTL["px"])
        return out
    closes, vols = h["Close"], h["Volume"]
    out["ok"] = True
    out["px_asof"] = str(h.index[-1].date())
    last = float(closes.iloc[-1])
    out["last_close"] = round(last, 2)

    def ret(n):
        return (last / float(closes.iloc[-(n + 1)]) - 1.0) * 100.0 if len(closes) > n else None

    out["returns_pct"] = {"d5": perfection._rnd(ret(5), 1), "d20": perfection._rnd(ret(20), 1),
                          "d60": perfection._rnd(ret(60), 1), "d120": perfection._rnd(ret(120), 1)}
    out["runup_20d_pct"] = ret(20)

    etf = SECTOR_ETF.get(sector or "", None)
    out["sector_etf"] = etf
    spy = _benchmark_closes("SPY", issues)
    sec = _benchmark_closes(etf, issues) if etf else None

    def _relret(bench, n):
        if bench is None or len(bench) <= n or len(closes) <= n:
            return None
        r_s = last / float(closes.iloc[-(n + 1)]) - 1.0
        r_b = float(bench.iloc[-1]) / float(bench.iloc[-(n + 1)]) - 1.0
        return (r_s - r_b) * 100.0

    out["vs_market_pct"] = {"d20": perfection._rnd(_relret(spy, 20), 1), "d60": perfection._rnd(_relret(spy, 60), 1)}
    out["vs_sector_pct"] = {"d20": perfection._rnd(_relret(sec, 20), 1), "d60": perfection._rnd(_relret(sec, 60), 1)}

    # Own-history distribution of rolling 60d relative return (vs sector,
    # falling back to SPY) → a REAL percentile, not an anchor.
    bench = sec if sec is not None else spy
    rel60_hist = []
    if bench is not None and len(bench) > 90 and len(closes) > 90:
        b = bench.reindex(closes.index).ffill()
        for i in range(60, len(closes)):
            c0, c1 = float(closes.iloc[i - 60]), float(closes.iloc[i])
            b0, b1 = float(b.iloc[i - 60]), float(b.iloc[i])
            if c0 and b0 and not math.isnan(b0) and not math.isnan(b1):
                rel60_hist.append((c1 / c0 - b1 / b0) * 100.0)
    cur_rel60 = _relret(bench, 60)
    out["rel60_hist_pctile"] = perfection.pct_rank(cur_rel60, rel60_hist, min_n=120)

    ma20 = float(closes.tail(20).mean())
    ma50 = float(closes.tail(50).mean())
    ma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else None
    out["ma_distance_pct"] = {
        "ma20": round((last / ma20 - 1) * 100.0, 1) if ma20 else None,
        "ma50": round((last / ma50 - 1) * 100.0, 1) if ma50 else None,
        "ma200": round((last / ma200 - 1) * 100.0, 1) if ma200 else None,
    }
    dist50_hist = []
    for i in range(50, len(closes)):
        m = float(closes.iloc[i - 50:i].mean())
        if m:
            dist50_hist.append((float(closes.iloc[i]) / m - 1) * 100.0)
    out["ma50_dist_hist_pctile"] = perfection.pct_rank(
        (last / ma50 - 1) * 100.0 if ma50 else None, dist50_hist, min_n=120)
    hi52 = float(closes.tail(252).max())
    out["from_52wk_high_pct"] = (last / hi52 - 1) * 100.0 if hi52 else None

    v20 = float(vols.tail(20).mean()) if len(vols) >= 20 else None
    v120 = float(vols.tail(120).mean()) if len(vols) >= 120 else None
    out["rvol20"] = round(v20 / v120, 2) if (v20 and v120) else None
    opens = h["Open"]
    gaps = 0
    for i in range(max(1, len(closes) - 60), len(closes)):
        pc, o = float(closes.iloc[i - 1]), float(opens.iloc[i])
        if pc and abs(o / pc - 1.0) > 0.03:
            gaps += 1
    out["gap_days_60d"] = gaps
    rets = closes.pct_change().dropna()
    s20 = float(rets.tail(20).std()) if len(rets) >= 20 else None
    s120 = float(rets.tail(120).std()) if len(rets) >= 120 else None
    out["vol20_vs_120_ratio"] = round(s20 / s120, 2) if (s20 and s120) else None

    # Per-event reactions with the session-correct cutoff:
    #   BMO → reaction day IS the report date; AMC/unknown → next trading day.
    idx_dates = [ts.date() for ts in closes.index]
    spy_re = spy.reindex(closes.index).ffill() if spy is not None else None
    sec_re = sec.reindex(closes.index).ffill() if sec is not None else None
    enriched = []
    for ev in events:
        try:
            evd = date.fromisoformat(ev["date"])
        except Exception:
            continue
        after = next((i for i, d0 in enumerate(idx_dates) if d0 >= evd), None)
        if after is None or after == 0:
            enriched.append(dict(ev))
            continue
        rx_i = after if ev.get("session") == "BMO" else (
            after + 1 if idx_dates[after] == evd else after)
        if rx_i >= len(closes):
            enriched.append(dict(ev))
            continue
        base_i = rx_i - 1
        base = float(closes.iloc[base_i])
        r1 = (float(closes.iloc[rx_i]) / base - 1.0) * 100.0 if base else None
        r5 = ((float(closes.iloc[min(rx_i + 4, len(closes) - 1)]) / base - 1.0) * 100.0
              if base and rx_i + 4 < len(closes) else None)
        e = dict(ev)
        e["reaction_1d_pct"] = perfection._rnd(r1, 2)
        e["reaction_5d_pct"] = perfection._rnd(r5, 2)
        for tag, b in (("rel_spy_1d_pct", spy_re), ("rel_sector_1d_pct", sec_re)):
            if b is not None and base and rx_i < len(b):
                b0, b1 = float(b.iloc[base_i]), float(b.iloc[rx_i])
                e[tag] = perfection._rnd(r1 - (b1 / b0 - 1.0) * 100.0, 2) if (b0 and r1 is not None) else None
            else:
                e[tag] = None
        if e.get("surprise_pct") is not None:
            e["beat_consensus"] = e["surprise_pct"] > 0
        elif e.get("eps_actual") is not None and e.get("eps_estimate") is not None:
            e["beat_consensus"] = e["eps_actual"] >= e["eps_estimate"]
        else:
            e["beat_consensus"] = None
        e["exceeded_implied"] = _implied_check(ev["date"], r1)
        enriched.append(e)
    out["events"] = enriched
    if enriched and enriched[0].get("reaction_1d_pct") is not None:
        try:
            last_ev = date.fromisoformat(enriched[0]["date"])
            i0 = next((i for i, d0 in enumerate(idx_dates) if d0 >= last_ev), None)
            if i0 is not None and i0 < len(closes) - 1:
                out["drift_since_last_er_pct"] = round(
                    (last / float(closes.iloc[i0]) - 1.0) * 100.0, 1)
        except Exception:
            out["drift_since_last_er_pct"] = None
    out["realized_er_moves_pct"] = [abs(e["reaction_1d_pct"]) for e in enriched
                                    if e.get("reaction_1d_pct") is not None]
    _cache_set(key, out, TTL["px"])
    return out


def _implied_check(ev_date: str, reaction_pct) -> bool | None:
    """Was |reaction| beyond the implied move WE recorded before that event?
    Uses this module's own pre-earnings snapshots only (point-in-time by
    construction). No snapshot for that date → None, never a guess."""
    if reaction_pct is None or _SNAP_DIR is None:
        return None
    try:
        snaps = _load_snapshots_near(ev_date)
        if not snaps:
            return None
        imp = _num(snaps[-1].get("implied_move_pct"))
        if imp is None or imp <= 0:
            return None
        return abs(reaction_pct) > imp
    except Exception:
        return None


_SNAP_INDEX: dict = {}


def _load_snapshots_near(ev_date: str) -> list[dict]:
    sym = _SNAP_INDEX.get("sym")
    rows = _SNAP_INDEX.get("rows") or []
    return [r for r in rows
            if r.get("next_earnings") == ev_date and r.get("date", "") <= ev_date]


def _prime_snapshot_index(symbol: str) -> None:
    _SNAP_INDEX.clear()
    _SNAP_INDEX["sym"] = symbol
    rows = []
    if _SNAP_DIR is not None:
        p = _SNAP_DIR / f"{symbol.upper()}.jsonl"
        if p.exists():
            try:
                for line in p.read_text().splitlines():
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
            except Exception:
                pass
    _SNAP_INDEX["rows"] = rows


def _valuation_history(symbol: str, fund: dict, issues: list) -> dict:
    """Trailing EV/S and P/E percentiles from daily price × real share count
    and quarterly filings. Labeled approximation (see module docstring)."""
    key = f"shares:{symbol}"
    out = _cache_get(key)
    if out is not None:
        return out
    out = {}
    t = _yft(symbol)
    try:
        h = t.history(period="3y", auto_adjust=False)   # UNadjusted close ×
        shares = t.get_shares_full(start=(date.today() - timedelta(days=3 * 365)).isoformat())
        # actual share count of the day = true market cap (split-safe by identity)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"yfinance shares/history: {exc}")
        h, shares = None, None
    if h is None or h.empty or shares is None or len(shares) == 0:
        _cache_set(key, out, TTL["shares"])
        return out
    try:
        sh = shares[~shares.index.duplicated(keep="last")]
        # Normalize BOTH indexes to tz-naive before any alignment/compare.
        if getattr(sh.index, "tz", None) is not None:
            sh.index = sh.index.tz_localize(None)
        px_idx = h.index.tz_localize(None) if getattr(h.index, "tz", None) is not None else h.index
        sh = sh.reindex(px_idx, method="ffill")
        qi = t.quarterly_income_stmt
        rev_row = _row(qi, ["Total Revenue", "Operating Revenue"])
        ni_row = _row(qi, ["Net Income", "Net Income Common Stockholders"])
        if rev_row is None:
            _cache_set(key, out, TTL["shares"])
            return out
        cols = sorted(qi.columns)                        # oldest → newest
        rev_ttm_steps, ni_ttm_steps = [], []
        for i in range(3, len(cols)):
            window = cols[i - 3:i + 1]
            rv = [_num(rev_row.get(c)) for c in window]
            nv = [_num(ni_row.get(c)) for c in window] if ni_row is not None else [None]
            rev_ttm_steps.append((cols[i], sum(v for v in rv if v is not None) if all(v is not None for v in rv) else None))
            ni_ttm_steps.append((cols[i], sum(v for v in nv if v is not None) if all(v is not None for v in nv) else None))
        net_debt = _num(fund.get("net_debt")) or 0.0
        evs_series, pe_series = [], []
        closes = h["Close"]
        for ts, px in closes.items():
            key_ts = ts.tz_localize(None) if ts.tz is not None else ts
            n_sh = _num(sh.get(key_ts))
            if not n_sh or not _num(px):
                continue
            mcap = float(px) * n_sh
            rev_ttm = next((v for c, v in reversed(rev_ttm_steps) if c <= key_ts and v), None)
            if rev_ttm:
                evs_series.append((mcap + net_debt) / rev_ttm)
            ni_ttm = next((v for c, v in reversed(ni_ttm_steps) if c <= key_ts and v and v > 0), None)
            if ni_ttm:
                pe_series.append(mcap / ni_ttm)
        out["evs_series_n"] = len(evs_series)
        if evs_series:
            cur = evs_series[-1]
            out["evs_current_trailing"] = round(cur, 2)
            out["evs_hist_pctile"] = perfection.pct_rank(cur, evs_series, min_n=120)
            for tag, n in (("30d", 30), ("90d", 90), ("180d", 180)):
                if len(evs_series) > n and evs_series[-(n + 1)]:
                    out[f"evs_expansion_{tag}_pct"] = (cur / evs_series[-(n + 1)] - 1.0) * 100.0
        if pe_series:
            out["pe_hist_pctile"] = perfection.pct_rank(pe_series[-1], pe_series, min_n=120)
        out["evs_hist_years"] = 3
        out["note"] = ("trailing multiples rebuilt from daily unadjusted price × actual share count "
                       "(get_shares_full) and quarterly filings; net debt held at latest value")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"valuation history: {exc}")
    _cache_set(key, out, TTL["shares"])
    return out


def _peer_block(symbol: str, sector: str | None, fwd_pe, issues: list) -> dict:
    out = {}
    try:
        board = _PEERS_GETTER() if _PEERS_GETTER else None
        rows = (board or {}).get("rows") or []
        peers = [r for r in rows if r.get("sector") == sector
                 and _num(r.get("forward_pe")) and r.get("symbol") != symbol]
        pes = sorted(_num(r["forward_pe"]) for r in peers)
        if len(pes) >= 8 and _num(fwd_pe):
            out["peer_fwd_pe_pctile"] = perfection.pct_rank(_num(fwd_pe), pes, min_n=8)
            out["peer_median_fwd_pe"] = round(pes[len(pes) // 2], 1)
            out["peer_count"] = len(pes)
            out["peer_basis"] = sector
    except Exception as exc:  # noqa: BLE001
        issues.append(f"peer block: {exc}")
    return out


def _chain_block(symbol: str, next_earnings: str | None, issues: list) -> dict:
    """Live options read from Schwab: straddle-implied move for the expiry
    covering earnings, skew, positioning, protection cost."""
    key = f"chain:{symbol}:{next_earnings}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    out = {"available": False}
    sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
    if sc is None:
        out["note"] = "Schwab client not configured — options read unavailable"
        _cache_set(key, out, TTL["chain"])
        return out
    try:
        chain = sc.get_option_chain(symbol, strike_count=60)
        if not chain or not chain.get("chains"):
            out["note"] = "no chain returned"
            _cache_set(key, out, TTL["chain"])
            return out
        spot = _num((chain.get("underlying") or {}).get("last"))
        exps = chain.get("expirations") or []
        target = None
        if next_earnings:
            after = [e for e in exps if e >= next_earnings]
            target = after[0] if after else (exps[-1] if exps else None)
        else:
            target = exps[0] if exps else None
        if not target or not spot:
            out["note"] = "no usable expiry/spot"
            _cache_set(key, out, TTL["chain"])
            return out
        legs = chain["chains"].get(target) or {}
        calls, puts = legs.get("calls") or [], legs.get("puts") or []

        def _mid(c):
            b, a = _num(c.get("bid")), _num(c.get("ask"))
            if b is not None and a is not None and a >= b > 0:
                return (a + b) / 2.0
            return _num(c.get("last"))

        def _atm(rows):
            return min(rows, key=lambda c: abs(c["strike"] - spot)) if rows else None

        ac, ap = _atm(calls), _atm(puts)
        straddle = None
        if ac and ap:
            mc, mp = _mid(ac), _mid(ap)
            if mc is not None and mp is not None:
                straddle = mc + mp
        out.update({
            "available": True, "asof": _now_iso(), "expiry": target, "spot": round(spot, 2),
            "atm_strike": ac["strike"] if ac else None,
            "straddle": round(straddle, 2) if straddle else None,
            "implied_move_pct": round(straddle / spot * 100.0, 2) if (straddle and spot) else None,
        })
        if out["implied_move_pct"]:
            out["implied_range"] = [round(spot * (1 - straddle / spot), 2),
                                    round(spot * (1 + straddle / spot), 2)]
        # 25Δ skew (call IV − put IV, vol points).
        c25 = min((c for c in calls if _num(c.get("delta")) is not None), default=None,
                  key=lambda c: abs(abs(c["delta"]) - 0.25))
        p25 = min((p for p in puts if _num(p.get("delta")) is not None), default=None,
                  key=lambda p: abs(abs(p["delta"]) - 0.25))
        if c25 is not None and p25 is not None and _num(c25.get("iv")) and _num(p25.get("iv")):
            out["call_put_skew_vol_pts"] = round((c25["iv"] - p25["iv"]) * 100.0, 2)
        civ = _num((ac or {}).get("iv"))
        piv = _num((ap or {}).get("iv"))
        out["atm_iv"] = round(((civ or 0) + (piv or 0)) / (2 if civ and piv else 1), 4) \
            if (civ or piv) else None
        # Positioning across near expiries (≤45d) — OI/volume ratios + strike walls.
        tot = {"cv": 0, "pv": 0, "coi": 0, "poi": 0}
        walls = []
        horizon = (date.today() + timedelta(days=45)).isoformat()
        for e in exps:
            if e > horizon:
                continue
            lg = chain["chains"].get(e) or {}
            for c in lg.get("calls") or []:
                tot["cv"] += c.get("volume") or 0
                tot["coi"] += c.get("openInterest") or 0
                walls.append(("C", e, c["strike"], c.get("openInterest") or 0))
            for p in lg.get("puts") or []:
                tot["pv"] += p.get("volume") or 0
                tot["poi"] += p.get("openInterest") or 0
                walls.append(("P", e, p["strike"], p.get("openInterest") or 0))
        out["call_put_vol_ratio"] = round(tot["cv"] / tot["pv"], 2) if tot["pv"] else None
        out["call_put_oi_ratio"] = round(tot["coi"] / tot["poi"], 2) if tot["poi"] else None
        walls.sort(key=lambda w: -w[3])
        out["oi_concentration"] = [
            {"kind": k, "expiry": e, "strike": s, "oi": oi} for k, e, s, oi in walls[:5] if oi > 0]
        # Downside protection: ~5% OTM put for the earnings expiry.
        prot = min((p for p in puts if p["strike"] <= spot * 0.95), default=None,
                   key=lambda p: abs(p["strike"] - spot * 0.95))
        if prot is not None:
            m = _mid(prot)
            if m is not None:
                out["protection"] = {"strike": prot["strike"], "mid": round(m, 2),
                                     "pct_of_spot": round(m / spot * 100.0, 2),
                                     "expiry": target}
        # IV percentile from the app's own stored daily IV history.
        if _IV_HISTORY_LOAD is not None and out.get("atm_iv"):
            try:
                hist = _IV_HISTORY_LOAD(symbol)
                if hist and len(hist) >= 20:
                    ranks = perfection.pct_rank(out["atm_iv"], [r["iv"] for r in hist], min_n=20)
                    out["iv_percentile"] = perfection._rnd(ranks, 0)
                    out["iv_history_days"] = len(hist)
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        issues.append(f"schwab chain: {exc}")
        out["note"] = f"chain error: {exc}"
    _cache_set(key, out, TTL["chain"])
    return out


def _store_snapshot(symbol: str, payload: dict, header: dict, chain: dict) -> bool:
    """Append today's pre-earnings snapshot (once per UTC day). Append-only."""
    if _SNAP_DIR is None or not header.get("next_earnings"):
        return False
    try:
        _SNAP_DIR.mkdir(parents=True, exist_ok=True)
        p = _SNAP_DIR / f"{symbol.upper()}.jsonl"
        today = date.today().isoformat()
        if p.exists():
            for line in p.read_text().splitlines():
                try:
                    if json.loads(line).get("date") == today:
                        return True          # already stored today — keep original
                except Exception:
                    continue
        rec = {
            "date": today, "as_of": _now_iso(), "symbol": symbol.upper(),
            "model_version": perfection.MODEL["version"],
            "next_earnings": header.get("next_earnings"),
            "session": header.get("session"),
            "score": payload.get("score"), "classification": payload.get("classification"),
            "confidence": payload.get("confidence"), "coverage_pct": payload.get("coverage_pct"),
            "implied_move_pct": chain.get("implied_move_pct"),
            "component_scores": {k: (c or {}).get("score") for k, c in (payload.get("components") or {}).items()},
        }
        with p.open("a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), allow_nan=False) + "\n")
        return True
    except Exception:
        return False


# ─────────────────────────────── entry point ───────────────────────────────

def build(symbol: str, assumptions: dict | None = None) -> dict:
    """Assemble the full Priced-for-Perfection payload for one symbol."""
    symbol = symbol.upper().strip()
    if not _YF_OK:
        return {"symbol": symbol, "error": "yfinance/pandas unavailable on this deploy"}
    if os.environ.get("JERRY_NO_NET") == "1":
        return {"symbol": symbol, "error": "network disabled (JERRY_NO_NET) — no data fetched, nothing fabricated"}
    issues: list[str] = []
    _prime_snapshot_index(symbol)

    fund = _fundamentals(symbol, issues)
    est = _estimates(symbol, issues)
    px = _prices(symbol, fund.get("sector"), est.get("events") or [], issues)
    vh = _valuation_history(symbol, fund, issues)
    peers = _peer_block(symbol, fund.get("sector"), fund.get("forward_pe"), issues)
    next_ev = est.get("next_event") or {}
    chain = _chain_block(symbol, next_ev.get("date"), issues)

    # Live price: Schwab quote wins; else latest close (flagged if old).
    spot = None
    try:
        sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
        if sc is not None:
            q = sc.get_quote(symbol)
            if q and _num(q.get("last")):
                spot = float(q["last"])
    except Exception as exc:  # noqa: BLE001
        issues.append(f"schwab quote: {exc}")
    if spot is None:
        spot = _num(chain.get("spot")) or _num(px.get("last_close")) or _num(fund.get("price_info"))

    freshness = []
    stmt_asof = fund.get("stmt_asof")
    if stmt_asof:
        try:
            if (date.today() - date.fromisoformat(stmt_asof)).days > 150:
                freshness.append(f"latest quarterly filing is {stmt_asof} (stale)")
        except Exception:
            pass
    if px.get("ok") and px.get("px_asof"):
        try:
            if (date.today() - date.fromisoformat(px["px_asof"])).days > 5:
                freshness.append(f"price history ends {px['px_asof']} (stale)")
        except Exception:
            pass

    pt_upside = None
    if _num(fund.get("target_mean")) and spot:
        pt_upside = (fund["target_mean"] / spot - 1.0) * 100.0

    mcap = _num(fund.get("market_cap"))
    if mcap is None and spot and _num(fund.get("share_count")):
        mcap = spot * fund["share_count"]
    ev_val = _num(fund.get("enterprise_value"))
    if ev_val is None and mcap is not None and _num(fund.get("net_debt")) is not None:
        ev_val = mcap + fund["net_debt"]

    required_accel = None
    if _num(est.get("consensus_rev_q_growth")) is not None and _num(fund.get("rev_yoy_pct")) is not None:
        required_accel = est["consensus_rev_q_growth"] * 100.0 - fund["rev_yoy_pct"]

    inputs = {
        # component 1
        "market_cap": mcap, "enterprise_value": ev_val,
        "revenue_ttm": fund.get("revenue_ttm"),
        "fcf_margin_ttm": fund.get("fcf_margin_ttm"), "fcf_margin_best": fund.get("fcf_margin_best"),
        "consensus_rev_growth": est.get("consensus_rev_growth"),
        "revenue_cagr_3y": fund.get("revenue_cagr_3y"),
        "share_count": fund.get("share_count"), "net_debt": fund.get("net_debt"),
        "rev_vs_fcf_growth_gap": fund.get("rev_vs_fcf_growth_gap"),
        "valuation_hist_pctile": vh.get("evs_hist_pctile"),
        "revisions_direction": est.get("revisions_direction"),
        "sources_execution": [
            {"source": "yfinance statements + info", "asof": fund.get("asof"),
             "stmt_asof": fund.get("stmt_asof")},
            {"source": "yfinance revenue_estimate", "asof": est.get("asof")}],
        # component 2
        "forward_pe": _num(fund.get("forward_pe")), "ev_to_revenue": _num(fund.get("ev_to_revenue")),
        "ev_to_ebitda": _num(fund.get("ev_to_ebitda")), "peg": _num(fund.get("peg")),
        "price_to_fcf": round(mcap / fund["fcf_ttm"], 1)
        if (mcap and _num(fund.get("fcf_ttm")) and fund["fcf_ttm"] > 0) else None,
        "fcf_yield_pct": round(fund["fcf_ttm"] / mcap * 100.0, 2)
        if (mcap and _num(fund.get("fcf_ttm"))) else None,
        "evs_hist_pctile": vh.get("evs_hist_pctile"), "pe_hist_pctile": vh.get("pe_hist_pctile"),
        "evs_hist_years": vh.get("evs_hist_years"),
        "evs_expansion_30d_pct": vh.get("evs_expansion_30d_pct"),
        "evs_expansion_90d_pct": vh.get("evs_expansion_90d_pct"),
        "evs_expansion_180d_pct": vh.get("evs_expansion_180d_pct"),
        **peers,
        "sources_valuation": [
            {"source": "yfinance info (forward multiples)", "asof": fund.get("asof")},
            {"source": "rebuilt trailing series (price × get_shares_full ÷ quarterly filings)",
             "asof": fund.get("asof"), "note": vh.get("note")},
            {"source": "watchlist board (sector peers)", "asof": _now_iso()}],
        # component 3
        "consensus_eps": est.get("consensus_eps"), "consensus_revenue": est.get("consensus_revenue"),
        "consensus_eps_analysts": est.get("consensus_eps_analysts"),
        "eps_rev_pct_7d": est.get("eps_rev_pct_7d"), "eps_rev_pct_30d": est.get("eps_rev_pct_30d"),
        "eps_rev_pct_60d": est.get("eps_rev_pct_60d"), "eps_rev_pct_90d": est.get("eps_rev_pct_90d"),
        "revisions_up_30d": est.get("revisions_up_30d"), "revisions_down_30d": est.get("revisions_down_30d"),
        "revisions_up_minus_down": est.get("revisions_up_minus_down"),
        "eps_dispersion_pct": est.get("eps_dispersion_pct"),
        "required_accel_pp": required_accel,
        "whisper": {"available": False,
                    "note": ("No reliable whisper estimate available — no legitimate, attributable "
                             "whisper source is configured (free feeds do not exist; nothing is "
                             "inferred in its place).")},
        "guidance": {"available": False,
                     "note": "No structured guidance feed from the configured free providers."},
        "market_implied_hurdle": None,   # filled below from component 1
        "sources_expectations": [
            {"source": "yfinance earnings_estimate / revenue_estimate / eps_trend / eps_revisions",
             "asof": est.get("asof")}],
        # component 4
        "events": px.get("events") or [],
        "sources_reactions": [
            {"source": "yfinance get_earnings_dates (EPS est/actual/surprise at report)",
             "asof": est.get("asof")},
            {"source": "yfinance 3y daily history (auto_adjust=True, split-safe) + SPY/sector ETF",
             "asof": px.get("asof")}],
        # component 5
        "returns_pct": px.get("returns_pct"), "vs_sector_pct": px.get("vs_sector_pct"),
        "vs_market_pct": px.get("vs_market_pct"), "ma_distance_pct": px.get("ma_distance_pct"),
        "from_52wk_high_pct": px.get("from_52wk_high_pct"), "runup_20d_pct": px.get("runup_20d_pct"),
        "drift_since_last_er_pct": px.get("drift_since_last_er_pct"),
        "rel60_hist_pctile": px.get("rel60_hist_pctile"),
        "ma50_dist_hist_pctile": px.get("ma50_dist_hist_pctile"),
        "rvol20": px.get("rvol20"), "gap_days_60d": px.get("gap_days_60d"),
        "vol20_vs_120_ratio": px.get("vol20_vs_120_ratio"),
        "sector_etf": px.get("sector_etf"),
        "sources_momentum": [{"source": "yfinance 3y daily history + SPY/sector ETF", "asof": px.get("asof")}],
        # component 6
        "pt_upside_pct": pt_upside, "target_mean": fund.get("target_mean"),
        "buy_ratio_pct": fund.get("buy_ratio_pct"), "ratings": fund.get("ratings"),
        "analyst_count": fund.get("analyst_count"),
        "short_pct_float": fund.get("short_pct_float"), "days_to_cover": _num(fund.get("days_to_cover")),
        "call_put_oi_ratio": chain.get("call_put_oi_ratio"),
        "call_put_vol_ratio": chain.get("call_put_vol_ratio"),
        "call_put_skew_vol_pts": chain.get("call_put_skew_vol_pts"),
        "oi_concentration": chain.get("oi_concentration"),
        "pt_changes_net_30d": fund.get("pt_changes_net_30d"),
        "institutional_pct": fund.get("institutional_pct"),
        "sources_crowding": [
            {"source": "yfinance info/recommendations/upgrades_downgrades", "asof": fund.get("asof")},
            {"source": "Schwab option chain (live)", "asof": chain.get("asof")}],
        # component 7
        "gross_margin_slope_pp_q": fund.get("gross_margin_slope_pp_q"),
        "op_margin_slope_pp_q": fund.get("op_margin_slope_pp_q"),
        "fcf_margin_ttm_pct": perfection._rnd((fund.get("fcf_margin_ttm") or 0) * 100.0, 1)
        if _num(fund.get("fcf_margin_ttm")) is not None else None,
        "ocf_conversion": fund.get("ocf_conversion"), "sbc_pct_ocf": fund.get("sbc_pct_ocf"),
        "capex_pct_revenue": fund.get("capex_pct_revenue"),
        "inventory_vs_rev_growth_gap": fund.get("inventory_vs_rev_growth_gap"),
        "rev_vs_eps_growth_gap": fund.get("rev_vs_eps_growth_gap"),
        "conversion_quarters": fund.get("conversion_quarters"),
        "sources_conversion": [{"source": "yfinance quarterly statements",
                                "asof": fund.get("asof"), "stmt_asof": fund.get("stmt_asof")}],
        "freshness_penalties": freshness,
    }

    payload = perfection.assemble(inputs, assumptions)

    # Market-Implied Hurdle (labeled derivation, never a whisper): from the
    # reverse valuation vs consensus.
    eh = (payload.get("components") or {}).get("execution_hurdle")
    if eh:
        gap = perfection._num((eh.get("benchmarks") or {}).get("gap_vs_consensus_pp"))
        cur = eh.get("current") or {}
        if gap is not None:
            mih = {
                "label": "Market-implied hurdle (derived from reverse valuation — NOT a whisper)",
                "implied_rev_cagr_pct": cur.get("implied_rev_cagr_pct"),
                "consensus_rev_growth_pct": cur.get("consensus_rev_growth_pct"),
                "gap_vs_consensus_pp": gap,
                "read": (f"The price already underwrites ~{cur.get('implied_rev_cagr_pct')}%/yr revenue "
                         f"growth — {abs(gap):.1f}pp {'ABOVE' if gap > 0 else 'below'} published consensus. "
                         + ("Beating consensus modestly may not satisfy the price." if gap > 0 else
                            "Published consensus is the effective bar.")),
            }
            payload["components"]["expectations_gap"] = payload["components"].get("expectations_gap") or {}
            if payload["components"]["expectations_gap"]:
                bm = payload["components"]["expectations_gap"].setdefault("benchmarks", {})
                bm["market_implied_hurdle"] = mih

    # Header block.
    days_to = None
    if next_ev.get("date"):
        try:
            days_to = (date.fromisoformat(next_ev["date"]) - date.today()).days
        except Exception:
            days_to = None
    header = {
        "symbol": symbol, "company": fund.get("company"),
        "sector": fund.get("sector"), "industry": fund.get("industry"),
        "price": round(spot, 2) if spot else None,
        "next_earnings": next_ev.get("date"),
        "session": next_ev.get("session") or "unknown",
        "days_to_earnings": days_to,
        "consensus_eps_next": next_ev.get("eps_estimate") if next_ev else None,
        "as_of": _now_iso(),
    }
    payload["header"] = header
    payload["options_panel"] = _options_panel(chain, px)
    payload["data_issues"] = issues
    payload["limitations"] = [
        "Estimate revisions and consensus values are the provider's CURRENT state — historical "
        "snapshots of estimates are not archived by free sources, so earlier snapshots cannot be "
        "reconstructed retroactively (this module's own daily snapshots accumulate from now on).",
        "No legitimate free whisper source exists; the whisper slot stays empty rather than inferred. "
        "The market-implied hurdle shown is a labeled reverse-valuation derivation.",
        "Historical guidance and per-event revenue consensus are unavailable from configured "
        "providers, so event classifications are consensus-EPS-based.",
        "Valuation history percentiles use rebuilt TRAILING multiples (forward-multiple history is "
        "licensed data); current levels and peer comparisons use forward numbers.",
        "Per-event implied-move comparisons only exist where this module stored a same-day "
        "pre-earnings snapshot; nothing is backfilled.",
        "This score has not been backtested as a probability — it is a transparency/severity "
        "measure, not a calibrated prediction.",
    ]
    payload["snapshot_stored"] = _store_snapshot(symbol, payload, header, chain)
    return payload


def _options_panel(chain: dict, px: dict) -> dict:
    """Event-uncertainty panel. Explicitly OUTSIDE the risk score."""
    realized = px.get("realized_er_moves_pct") or []
    med = perfection._median(realized)
    avg = perfection._mean(realized)
    imp = _num(chain.get("implied_move_pct"))
    return {
        "available": bool(chain.get("available")),
        "note": chain.get("note"),
        "asof": chain.get("asof"),
        "expiry": chain.get("expiry"),
        "spot": chain.get("spot"),
        "atm_strike": chain.get("atm_strike"),
        "straddle": chain.get("straddle"),
        "implied_move_pct": imp,
        "implied_range": chain.get("implied_range"),
        "iv_percentile": chain.get("iv_percentile"),
        "iv_history_days": chain.get("iv_history_days"),
        "atm_iv": chain.get("atm_iv"),
        "realized_er_moves_pct": [round(m, 2) for m in realized],
        "median_realized_move_pct": perfection._rnd(med, 2),
        "avg_realized_move_pct": perfection._rnd(avg, 2),
        "implied_vs_realized": perfection._rnd(imp / med, 2) if (imp and med) else None,
        "call_put_skew_vol_pts": chain.get("call_put_skew_vol_pts"),
        "oi_concentration": chain.get("oi_concentration"),
        "protection": chain.get("protection"),
        "disclaimer": ("Measures event uncertainty and protection cost. A high implied move does NOT "
                       "raise the Perfection Risk Score."),
    }
