"""hf_watch.py — NAMED FUND WATCH: what is known about each manager, and when.

The card this feeds has one rule that every other line serves: between
filings, a manager's current activity is UNKNOWN, and the card says so in
that word. A strong industry-wide trend does not soften it. The trend is
rendered under its own heading, in its own evidence class, and the code
that builds the fund block never sees it.

For each watched manager the record holds:

  * the filing trail — every 13F-family and 13D/13G filing, with the date
    each describes and the date it became public;
  * the latest 13F, parsed, diffed against the prior quarter, rolled up by
    sector, and measured for concentration and options share;
  * a successor link when the current identity filed a 13F-NT naming who
    reports the book now (Pershing Square, 2026);
  * SCHEDULE 13D events since the last 13F — the only fast, verified,
    named-fund channel there is;
  * the manager's own statements (their feed) and press about them, kept
    apart from filings and never rendered as positions;
  * a cross-check against Unusual Whales' parse of the same filing, with
    disagreements stored as conflicts rather than resolved silently.

Lazy worker, same as every other board: nothing is fetched until somebody
looks, and a manager is refreshed at most every six hours.

HEDGE_FUND_INTEL.md §5d.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import hf_registry as R
import hf_sources as S

HF_WATCH_VERSION = "1.1.0"

DEFAULTS = {
    "_doc": ("Hedge Fund Intelligence (hf_watch.py, HEDGE_FUND_INTEL.md). Phase 1 — Named Fund "
             "Watch. The only knobs are cadences."),
    "watch": {"refresh_hours": 6,        # a filing trail cannot change faster than this matters
              "statement_days": 45,      # how far back a manager's own channel is read
              "press_days": 14,          # how far back press about them is read
              "event_days": 180,         # how far back 13D/13G events are listed
              "new_filing_days": 7},     # the daily-index sweep for "major new filings"
}


def config() -> dict:
    """DEFAULTS overlaid by thresholds.json → hedge, so every cadence is a
    number the reader can see and change."""
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        import premium_edge as pe
        full = json.loads((Path(__file__).resolve().parent / "thresholds.json").read_text())
        cfg = pe._deep_merge(cfg, full.get("hedge") or {})  # noqa: SLF001
        dd = getattr(pe, "_DATA_DIR", None)
        if dd:
            p = Path(dd) / "thresholds.json"
            if p.exists():
                cfg = pe._deep_merge(cfg, (json.loads(p.read_text()).get("hedge") or {}))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass
    return cfg


def _knob(name: str) -> float:
    try:
        return float((config().get("watch") or {}).get(name, DEFAULTS["watch"][name]))
    except (TypeError, ValueError):
        return float(DEFAULTS["watch"][name])

_DATA_DIR: Path | None = None
_UW_GETTER = None                # () -> UWClient | None
_SECTOR_FN = None                # (symbol) -> sector name | None
_SECTOR_NORM = None              # (any sector label) -> one of the app's sector names | None
_NOW_FN = None
_TREND_FN = None                 # () -> the Pulse's sentence for the combined block
_SEED_PATH: Path | None = None
_LOCK = threading.RLock()
_STATE: dict = {"records": {}, "as_of": None, "refreshing": False, "thread": None,
                "errors": {}, "last_sweep": None, "new_filings": [], "cusips": 0}


def configure(data_dir=None, uw_getter=None, sector_fn=None, now_fn=None, seed_path=None,
              sector_norm=None, trend_fn=None) -> None:
    global _DATA_DIR, _UW_GETTER, _SECTOR_FN, _NOW_FN, _SEED_PATH, _SECTOR_NORM, _TREND_FN
    _DATA_DIR = Path(data_dir) if data_dir else None
    _UW_GETTER, _SECTOR_FN, _NOW_FN = uw_getter, sector_fn, now_fn
    _SECTOR_NORM = sector_norm
    _TREND_FN = trend_fn
    _SEED_PATH = Path(seed_path) if seed_path else None
    if _DATA_DIR is not None:
        try:
            (_DATA_DIR / "hf" / "funds").mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
    _load_records()


def _now() -> datetime:
    if _NOW_FN:
        try:
            n = _NOW_FN()
            if isinstance(n, datetime):
                return n
        except Exception:  # noqa: BLE001
            pass
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now().date()


def long_date(s) -> str | None:
    """Month Day, Year — the only way a date is ever shown on the card."""
    try:
        d = date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None
    return f"{d.strftime('%B')} {d.day}, {d.year}"


# ── registry (seed + the user's overlay) ────────────────────────────────────

def _overlay_path() -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "hf" / "watchlist_overlay.json"


def _load_overlay() -> dict:
    p = _overlay_path()
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_overlay(overlay: dict) -> bool:
    p = _overlay_path()
    if p is None:
        return False
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(overlay, indent=1), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception:  # noqa: BLE001
        return False


def registry() -> dict:
    return R.merge(R.load_seed(_SEED_PATH), _load_overlay())


def set_watchlist(payload: dict) -> dict:
    """Replace the user's overlay. Every manager is validated first; one bad
    entry rejects the whole save so the list can never be half-updated."""
    overlay = {"managers": [], "removed": []}
    problems = {}
    for m in (payload or {}).get("managers") or []:
        bad = R.validate(m)
        if bad:
            problems[m.get("key") or m.get("name") or "?"] = bad
        else:
            overlay["managers"].append(m)
    for k in (payload or {}).get("removed") or []:
        if isinstance(k, str):
            overlay["removed"].append(k)
    if problems:
        return {"ok": False, "problems": problems}
    ok = _save_overlay(overlay)
    return {"ok": ok, "managers": len(registry().get("managers") or [])}


# ── the store ───────────────────────────────────────────────────────────────

def _fund_path(key: str) -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "hf" / "funds" / f"{key}.json"


def _load_records() -> None:
    if _DATA_DIR is None:
        return
    d = _DATA_DIR / "hf" / "funds"
    if not d.exists():
        return
    for p in d.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            # A record written by an older version of this module may carry
            # an older shape (the first cross-check compared row counts).
            # Leaving it out means the next look re-reads the manager, which
            # costs a handful of cached EDGAR calls and never shows a stale
            # verdict under a new label.
            if rec.get("key") and rec.get("watch_version") == HF_WATCH_VERSION:
                with _LOCK:
                    _STATE["records"][rec["key"]] = rec
        except Exception:  # noqa: BLE001
            continue


def _save_record(rec: dict) -> None:
    if not S.attribution_ok(rec.get("evidence") or []):
        raise ValueError("refusing to store a record that attributes anonymous data to a fund")
    p = _fund_path(rec["key"])
    if p is None:
        return
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        pass


# ── building one manager's record ───────────────────────────────────────────
_13F_FORMS = ("13F-HR", "13F-HR/A", "13F-NT", "13F-NT/A")
_13DG_FORMS = ("SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A",
               "SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A")


def _sector_of(sym: str):
    if _SECTOR_FN is None:
        return None
    try:
        return _SECTOR_FN(sym)
    except Exception:  # noqa: BLE001
        return None


def _normalize_sector(raw):
    """Fold whatever label a source used onto the app's own sector names,
    through the injected normaliser when there is one. Unknown stays None."""
    if not raw:
        return None
    if _SECTOR_NORM is None:
        return str(raw)
    try:
        return _SECTOR_NORM(raw) or None
    except Exception:  # noqa: BLE001
        return None


def _latest_tables(cik: int, rows: list[dict]) -> tuple[dict | None, dict | None, list[dict]]:
    """The current period's holdings and the prior period's, each with any
    RESTATEMENT amendment applied (a restatement replaces the table; a
    NEW HOLDINGS amendment adds to it). Returns (current, prior, trail)."""
    hr = [r for r in rows if r["form"] in ("13F-HR", "13F-HR/A") and r.get("period")]
    periods = sorted({r["period"] for r in hr}, reverse=True)
    trail = []

    def build(period: str) -> dict | None:
        parts = sorted((r for r in hr if r["period"] == period), key=lambda r: r["filed"])
        base, extra, filed_on, amended_on, amend_kinds = None, [], None, None, []
        for r in parts:
            parsed = S.fetch_13f(cik, r["accession"])
            if not parsed:
                continue
            prim = parsed.get("primary") or {}
            kind = (prim.get("amendment_type") or "").upper()
            trail.append({"form": r["form"], "as_of": period, "public_on": r["filed"],
                          "accession": r["accession"], "amendment_type": kind or None,
                          "entries": prim.get("entries"), "value_total": prim.get("value_total")})
            if r["form"] == "13F-HR" or kind == "RESTATEMENT" or base is None:
                base = parsed
                if r["form"] == "13F-HR":
                    filed_on = r["filed"]
                else:
                    amended_on = r["filed"]
                    amend_kinds.append(kind or "AMENDMENT")
            else:
                extra.append(parsed)
                amended_on = r["filed"]
                amend_kinds.append(kind or "NEW HOLDINGS")
        if base is None:
            return None
        positions = list(base.get("positions") or [])
        for e in extra:
            positions.extend(e.get("positions") or [])
        return {"period": period, "public_on": filed_on or amended_on, "amended_on": amended_on,
                "amendments": amend_kinds, "primary": base.get("primary") or {},
                "positions": positions}

    cur = build(periods[0]) if periods else None
    prior = build(periods[1]) if len(periods) > 1 else None
    return cur, prior, trail


PARSE_EVENTS_CAP = 40      # structured XML is read for at most this many recent events per manager


def _events_13d(cik: int, rows: list[dict], name: str) -> list[dict]:
    """13D and 13G filings in the window, newest first.

    The two forms mean different things about TIME. A 13D is filed within
    five business days of the transaction it reports, so its date is an
    event date and it counts as activity. A 13G is a passive holder's
    quarterly notice: it describes a quarter end, usually the same one the
    13F already covers, so its filing date is not a date anything happened.
    A 13G with no stated period therefore carries no `as_of` at all, and
    activity_state ignores it. Multi-strategy managers file hundreds of 13Gs
    a quarter; reading every one as "activity" would make every card say
    FILED SINCE forever."""
    since = (_today() - timedelta(days=int(_knob("event_days")))).isoformat()
    out = []
    parsed_n = 0
    for r in sorted(rows, key=lambda x: x.get("filed") or "", reverse=True):
        if r["form"] not in _13DG_FORMS or (r.get("filed") or "") < since:
            continue
        is_13d = "13D" in r["form"]
        ev = {"form": r["form"], "public_on": r["filed"],
              "as_of": (r.get("period") or r["filed"]) if is_13d else (r.get("period") or None),
              "kind": "EVENT" if is_13d else "PASSIVE NOTICE",
              "accession": r["accession"], "issuer": None, "percent": None, "purpose": None}
        if parsed_n < PARSE_EVENTS_CAP:
            parsed = S.fetch_13d(cik, r["accession"])
            parsed_n += 1
            if parsed:
                ev.update({"issuer": parsed.get("issuer_name"), "percent": parsed.get("percent_max"),
                           "purpose": parsed.get("purpose"), "security": parsed.get("security_title")})
                if is_13d and parsed.get("event_date"):
                    ev["as_of"] = parsed["event_date"]
        out.append(ev)
    return out


def _statements(entry: dict) -> tuple[list[dict], list[dict]]:
    own, press = [], []
    cut_own = (_now() - timedelta(days=int(_knob("statement_days")))).isoformat()
    cut_press = (_now() - timedelta(days=int(_knob("press_days")))).isoformat()
    for f in entry.get("feeds") or []:
        if f.get("kind") != "rss" or not f.get("url"):
            continue
        for it in S.feed_items(f["url"]):
            if (it.get("published") or "") >= cut_own:
                own.append({**it, "channel": f.get("label") or f["url"]})
    q = entry.get("news_query")
    if q:
        for it in S.news_items(q):
            if (it.get("published") or "") >= cut_press:
                press.append(it)
    own.sort(key=lambda x: x.get("published") or "", reverse=True)
    press.sort(key=lambda x: x.get("published") or "", reverse=True)
    return own[:20], press[:15]


CROSSCHECK_TOP = 10        # the largest lines compared between EDGAR and Unusual Whales
CROSSCHECK_AGREE = 7       # this many of them in common is agreement


def _uw_crosscheck(cik: int, cur: dict | None, symbol_of: dict | None = None) -> dict | None:
    """Unusual Whales parses the same filing. Agreement is reassurance;
    disagreement is a conflict row, not something to average away.

    What is compared is WHICH names are the largest, not how many rows each
    side used. EDGAR lists Berkshire's book as 89 lines across subsidiaries
    where Unusual Whales dedupes to 30 tickers; counting rows would call
    that a conflict on every card, and it is only a counting difference.
    If seven of the ten largest positions by value are the same tickers on
    both sides, the two readings agree."""
    if _UW_GETTER is None or cur is None:
        return None
    try:
        uw = _UW_GETTER()
        if uw is None or not hasattr(uw, "institution_holdings"):
            return None
        data = uw.institution_holdings(f"{int(cik):010d}", date=cur["period"])
    except Exception:  # noqa: BLE001
        return None
    if not data:
        return {"available": False}
    rows = data if isinstance(data, list) else (data.get("data") or [])
    positions = cur.get("positions") or []
    agg = S._aggregate(positions)  # noqa: SLF001
    symbol_of = symbol_of or {}
    edgar_top = []
    for p in sorted(agg.values(), key=lambda x: -(x.get("value") or 0)):
        sym = symbol_of.get(p.get("cusip") or "")
        if sym and sym not in edgar_top:
            edgar_top.append(sym)
        if len(edgar_top) >= CROSSCHECK_TOP:
            break
    uw_top = []
    for r in sorted(rows, key=lambda x: -float(x.get("value") or 0)):
        t = str(r.get("ticker") or "").upper()
        if t and t not in uw_top:
            uw_top.append(t)
        if len(uw_top) >= CROSSCHECK_TOP:
            break
    overlap = len(set(edgar_top) & set(uw_top))
    compared = min(len(edgar_top), len(uw_top))
    # Agreement needs enough mapped names to compare; with fewer than the
    # threshold on either side the check is inconclusive, not a conflict.
    agree = None if compared < CROSSCHECK_AGREE else overlap >= CROSSCHECK_AGREE
    return {"available": True, "n_uw": len(rows), "n_edgar": len(agg), "n_edgar_lines": len(positions),
            "top_compared": compared, "top_overlap": overlap, "agree": agree,
            "edgar_top": edgar_top, "uw_top": uw_top,
            # UW labels each held name with a sector; that is the fallback
            # for names the user's own board has never classified.
            "sector_by_symbol": {str(r.get("ticker")).upper(): r.get("sector")
                                 for r in rows if r.get("ticker") and r.get("sector")}}


def _build(entry: dict, cusips: dict[str, str]) -> dict:
    key, name = entry["key"], entry["name"]
    rec: dict = {"key": key, "name": name, "style": entry.get("style"),
                 "watch_version": HF_WATCH_VERSION,
                 "turnover": entry.get("turnover"), "status": R.status_of(entry),
                 "people": entry.get("people") or [], "built_at": _now().isoformat(timespec="seconds"),
                 "cik": None, "identities": entry.get("ciks") or [], "evidence": [],
                 "filings": [], "holdings": None, "change": None, "sectors": None,
                 "concentration": None, "top": [], "events": [], "statements": [], "press": [],
                 "successor": None, "crosscheck": None, "notes": []}
    cik = R.current_cik(entry) or R.last_cik(entry)
    if cik is None:
        rec["notes"].append("No EDGAR identity on file.")
    else:
        rec["cik"] = cik
        sub = S.submissions(cik)
        if not sub:
            rec["notes"].append("EDGAR could not be read for this manager on this refresh.")
        else:
            _build_from_edgar(rec, entry, cik, sub, cusips)
    # The manager's own words and the press about them do not depend on
    # EDGAR answering. A CEASED manager, or an EDGAR outage, still gets
    # both — statements are the only channel a ceased manager has left.
    own, press = _statements(entry)
    rec["statements"], rec["press"] = own, press
    for it in own:
        rec["evidence"].append(S.evidence(S.VERIFIED, "STATEMENT", it.get("published"), it.get("published"),
                                          it.get("channel") or "feed", fund=name, title=it.get("title"),
                                          link=it.get("link")))
    for it in press:
        rec["evidence"].append(S.evidence(S.VERIFIED, "PRESS", it.get("published"), it.get("published"),
                                          it.get("outlet") or "press", fund=name, title=it.get("title"),
                                          link=it.get("link")))
    return rec


def _build_from_edgar(rec: dict, entry: dict, cik: int, sub: dict, cusips: dict[str, str]) -> None:
    """Everything the SEC record says: the filing trail, the latest table
    and its diff, 13D events. Mutates `rec`; the caller adds statements."""
    name = rec["name"]
    rows = S.recent_filings(sub)
    rec["edgar_name"] = sub.get("name")

    # A 13F-NT with no holdings report for the same period means somebody
    # else reports the book. Follow the notice to them before reading anything.
    f13 = [r for r in rows if r["form"] in _13F_FORMS and r.get("period")]
    if f13:
        newest = max(f13, key=lambda r: (r["period"], r["filed"]))
        same = [r for r in f13 if r["period"] == newest["period"]]
        if newest["form"].startswith("13F-NT") and not any(r["form"].startswith("13F-HR") for r in same):
            parsed = S.fetch_13f(cik, newest["accession"]) or {}
            succ = S.successor_from_notice(parsed.get("primary") or {})
            if succ and succ.get("cik") and succ["cik"] != cik:
                rec["successor"] = {"cik": succ["cik"], "name": succ.get("name"),
                                    "as_of": newest["period"], "public_on": newest["filed"]}
                rec["notes"].append(f"Filed a 13F-NT for {long_date(newest['period'])}: the book is "
                                    f"reported by {succ.get('name')} (CIK {succ['cik']}).")
                cik = int(succ["cik"])
                rec["cik"] = cik
                sub2 = S.submissions(cik)
                if sub2:
                    rows = S.recent_filings(sub2)
                    rec["edgar_name"] = sub2.get("name")
            elif entry.get("status") != "CEASED":
                rec["notes"].append(f"Filed a 13F-NT for {long_date(newest['period'])} and named no "
                                    f"other manager; nothing further can be read.")

    cur, prior, trail = _latest_tables(cik, rows)
    rec["filings"] = sorted(trail, key=lambda t: t["public_on"], reverse=True)[:12]
    for t in rec["filings"]:
        rec["evidence"].append(S.evidence(S.VERIFIED, "FILING", t["as_of"], t["public_on"],
                                          f"EDGAR {t['form']} {t['accession']}", fund=name,
                                          form=t["form"], amendment_type=t.get("amendment_type")))
    if cur:
        positions = cur["positions"]
        rec["holdings"] = {"as_of": cur["period"], "public_on": cur["public_on"],
                           "amended_on": cur.get("amended_on"), "amendments": cur.get("amendments") or [],
                           "n": len(positions), "value_total": sum(p.get("value") or 0 for p in positions),
                           "prior_as_of": prior["period"] if prior else None}
        rec["change"] = S.diff_positions(prior["positions"] if prior else None, positions) if prior else None
        rec["concentration"] = S.concentration(positions)
        rec["top"] = S.top_positions(positions, 10)
        symbol_of = {p["cusip"]: cusips.get(p["cusip"]) for p in positions if p.get("cusip") and cusips.get(p.get("cusip"))}
        for t in rec["top"]:
            t["symbol"] = symbol_of.get(t.get("cusip"))
        for bucket in ("new", "increased", "reduced", "exited"):
            for r in (rec["change"] or {}).get(bucket) or []:
                r["symbol"] = symbol_of.get(r.get("cusip"))
        rec["crosscheck"] = _uw_crosscheck(cik, cur, symbol_of)
        uw_sectors = (rec["crosscheck"] or {}).get("sector_by_symbol") or {}

        def sector_for(sym: str):
            # The user's own board first; Unusual Whales' label second; both
            # folded onto the app's eleven sector names. Anything else is
            # UNMAPPED, and is reported as such.
            raw = _sector_of(sym) or uw_sectors.get(str(sym).upper())
            return _normalize_sector(raw)

        rec["sectors"] = S.sector_exposure(positions, symbol_of, sector_for)
        if rec["crosscheck"] is not None:
            rec["crosscheck"].pop("sector_by_symbol", None)
        cc = rec["crosscheck"]
        if cc and cc.get("available") and cc.get("agree") is False:
            rec["notes"].append(f"Unusual Whales and EDGAR disagree on what this book's largest positions are: "
                                f"only {cc['top_overlap']} of the top {cc['top_compared']} match. Shown as a "
                                f"conflict, not resolved.")
    elif entry.get("status") == "CEASED":
        rec["notes"].append("No holdings report is on file for a current period; this manager has ceased filing.")
    else:
        rec["notes"].append("No 13F holdings report could be read for this manager.")

    rec["events"] = _events_13d(cik, rows, name)
    for ev in rec["events"]:
        rec["evidence"].append(S.evidence(S.VERIFIED, "FILING", ev["as_of"], ev["public_on"],
                                          f"EDGAR {ev['form']} {ev['accession']}", fund=name,
                                          form=ev["form"], issuer=ev.get("issuer"), percent=ev.get("percent")))


# ── the rule the card exists for ────────────────────────────────────────────

def activity_state(rec: dict, today: date | None = None) -> dict:
    """What can be said about this manager's activity right now.

    CEASED     — the manager no longer files; nothing will ever arrive.
    FILED SINCE — a verified filing describes a date after the last 13F
                  period (a 13D, or a newer 13F). It is listed.
    UNKNOWN    — nothing verified since the last 13F. This is the normal
                  state for roughly 300 days a year, and it is rendered in
                  that word, whatever the industry is doing."""
    today = today or _today()
    if rec.get("status") == "CEASED":
        return {"state": "CEASED", "since": None, "items": []}
    h = rec.get("holdings") or {}
    last = h.get("as_of")
    newer = [e for e in (rec.get("events") or [])
             if e.get("as_of") and e.get("kind") == "EVENT" and (last is None or e["as_of"] > last)]
    if newer:
        return {"state": "FILED SINCE", "since": last, "items": newer[:10]}
    days = (today - date.fromisoformat(last)).days if last else None
    return {"state": "UNKNOWN", "since": last, "days_since": days, "items": []}


def broader_trend() -> dict:
    """The Hedge Fund Pulse's sentence for the combined block.

    Injected rather than imported, so this module cannot reach into the
    aggregate layer and accidentally mix the two: it receives a finished
    sentence, already classed MODEL INFERENCE, and renders it under its own
    heading. Nothing about a fund is computed from it, and nothing about it
    is computed from a fund."""
    if _TREND_FN is not None:
        try:
            out = _TREND_FN()
            if isinstance(out, dict):
                return out
        except Exception:  # noqa: BLE001
            pass
    return {"available": False, "class": S.INFERENCE,
            "note": "The aggregate layer (Hedge Fund Pulse) has not been read yet. Nothing here is about this fund."}


# ── refresh ─────────────────────────────────────────────────────────────────

def _sweep_new_filings(reg: dict) -> list[dict]:
    """Every 13D/13G/13F-family filing by a watched identity in the last
    week, from the EDGAR daily index — the cheapest complete answer."""
    idx = R.cik_index(reg)
    out = []
    d = _today()
    seen_days = 0
    tries = 0
    want = int(_knob("new_filing_days"))
    while seen_days < want and tries < want * 2:
        tries += 1
        if d.weekday() < 5:
            for row in S.daily_index(d):
                m = idx.get(row["cik"])
                if m:
                    out.append({**row, "manager": m["name"], "key": m["key"]})
            seen_days += 1
        d -= timedelta(days=1)
    return sorted(out, key=lambda r: r["filed"], reverse=True)


def _refresh(keys: list[str] | None = None) -> None:
    reg = registry()
    try:
        cusips = S.cusip_map()
    except Exception:  # noqa: BLE001
        cusips = {}
    with _LOCK:
        _STATE["cusips"] = len(cusips)
    for entry in reg.get("managers") or []:
        if keys and entry["key"] not in keys:
            continue
        try:
            rec = _build(entry, cusips)
            _save_record(rec)
            with _LOCK:
                _STATE["records"][entry["key"]] = rec
                _STATE["errors"].pop(entry["key"], None)
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _STATE["errors"][entry["key"]] = str(exc)[:200]
    try:
        nf = _sweep_new_filings(reg)
        with _LOCK:
            _STATE["new_filings"] = nf
            _STATE["last_sweep"] = _now().isoformat(timespec="seconds")
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATE["errors"]["_sweep"] = str(exc)[:200]
    with _LOCK:
        _STATE["as_of"] = _now().isoformat(timespec="seconds")


def _stale() -> bool:
    with _LOCK:
        if not _STATE["as_of"]:
            return True
        try:
            then = datetime.fromisoformat(_STATE["as_of"])
        except ValueError:
            return True
    return (_now() - then).total_seconds() > _knob("refresh_hours") * 3600.0


def _kick() -> None:
    if not S.available():
        return
    with _LOCK:
        if _STATE["refreshing"]:
            return
        _STATE["refreshing"] = True

    def run():
        try:
            _refresh()
        finally:
            with _LOCK:
                _STATE["refreshing"] = False

    t = threading.Thread(target=run, name="hf-watch", daemon=True)
    with _LOCK:
        _STATE["thread"] = t
    t.start()


def refresh_now(keys: list[str] | None = None) -> dict:
    """Synchronous, for the route that asks for one manager on demand."""
    _refresh(keys)
    return status()


# ── payloads ────────────────────────────────────────────────────────────────

def _summary(rec: dict) -> dict:
    h = rec.get("holdings") or {}
    c = rec.get("concentration") or {}
    ch = rec.get("change") or {}
    st = activity_state(rec)
    return {"key": rec["key"], "name": rec["name"], "style": rec.get("style"),
            "turnover": rec.get("turnover"), "status": rec.get("status"), "cik": rec.get("cik"),
            "activity": st,
            "last_as_of": h.get("as_of"), "last_public_on": h.get("public_on"),
            "amended_on": h.get("amended_on"), "n_positions": h.get("n"),
            "value_total": h.get("value_total"),
            "top10_weight": c.get("top10_weight"), "options_value_share": c.get("options_value_share"),
            "n_new": len(ch.get("new") or []), "n_increased": len(ch.get("increased") or []),
            "n_reduced": len(ch.get("reduced") or []), "n_exited": len(ch.get("exited") or []),
            "n_events": len(rec.get("events") or []), "n_statements": len(rec.get("statements") or []),
            "n_press": len(rec.get("press") or []), "successor": rec.get("successor"),
            "notes": rec.get("notes") or [], "built_at": rec.get("built_at"),
            "dates": {"last_as_of": long_date(h.get("as_of")), "last_public_on": long_date(h.get("public_on")),
                      "amended_on": long_date(h.get("amended_on"))}}


def snapshot() -> dict:
    if _stale():
        _kick()
    reg = registry()
    with _LOCK:
        recs = dict(_STATE["records"])
        errors = dict(_STATE["errors"])
        nf = list(_STATE["new_filings"])
        out = {"ok": True, "version": HF_WATCH_VERSION, "as_of": _STATE["as_of"],
               "refreshing": _STATE["refreshing"], "last_sweep": _STATE["last_sweep"],
               "cusips_mapped": _STATE["cusips"]}
    managers = []
    for entry in reg.get("managers") or []:
        rec = recs.get(entry["key"])
        if rec:
            managers.append(_summary(rec))
        else:
            managers.append({"key": entry["key"], "name": entry["name"], "style": entry.get("style"),
                             "turnover": entry.get("turnover"), "status": R.status_of(entry),
                             "cik": R.current_cik(entry) or R.last_cik(entry),
                             "activity": {"state": "NOT READ YET", "since": None, "items": []},
                             "notes": ["Not read from EDGAR yet."], "dates": {}})
    out.update({"managers": managers, "errors": errors, "new_filings": nf[:60],
                "broader_trend": broader_trend(), "n_managers": len(managers),
                "n_read": sum(1 for m in managers if m.get("built_at"))})
    return out


def fund(key: str) -> dict:
    with _LOCK:
        rec = _STATE["records"].get(key)
    if rec is None:
        reg = registry()
        entry = next((m for m in reg.get("managers") or [] if m["key"] == key), None)
        if entry is None:
            return {"ok": False, "error": f"no manager with key {key!r}"}
        if S.available():
            try:
                cusips = S.cusip_map()
            except Exception:  # noqa: BLE001
                cusips = {}
            rec = _build(entry, cusips)
            _save_record(rec)
            with _LOCK:
                _STATE["records"][key] = rec
        else:
            return {"ok": True, "key": key, "name": entry["name"], "activity": {"state": "NOT READ YET"},
                    "broader_trend": broader_trend(), "offline": True}
    out = dict(rec)
    out["ok"] = True
    out["activity"] = activity_state(rec)
    out["broader_trend"] = broader_trend()
    out["dates"] = {k: long_date(v) for k, v in {
        "last_as_of": (rec.get("holdings") or {}).get("as_of"),
        "last_public_on": (rec.get("holdings") or {}).get("public_on"),
        "amended_on": (rec.get("holdings") or {}).get("amended_on"),
        "prior_as_of": (rec.get("holdings") or {}).get("prior_as_of")}.items()}
    reg_entry = next((m for m in registry().get("managers") or [] if m["key"] == key), None)
    out["describe"] = R.describe(reg_entry) if reg_entry else None
    return out


def status() -> dict:
    with _LOCK:
        return {"version": HF_WATCH_VERSION, "as_of": _STATE["as_of"], "refreshing": _STATE["refreshing"],
                "records": len(_STATE["records"]), "errors": dict(_STATE["errors"]),
                "last_sweep": _STATE["last_sweep"], "new_filings": len(_STATE["new_filings"]),
                "cusips_mapped": _STATE["cusips"], "online": S.available()}
