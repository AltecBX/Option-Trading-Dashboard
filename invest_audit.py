"""invest_audit.py — is production actually collecting clean data every day?

Everything the Investment tab's forward work rests on is collected going
forward and can never be back-filled. That makes one question worth asking
before any further feature: will the next 30, 90, 180 and 365 days of data
actually be usable?

This module answers it. It reads the stores; it does not write to them, and
it never repairs anything. A corrupt record is REPORTED, with enough detail
to decide what to do about it, and left exactly where it is — silently
mending history is how a dataset stops being evidence.

Five questions:

  WHERE DOES IT LIVE      Is the data directory a mounted volume that
                          survives a redeploy, or container storage that
                          does not? Answered from the filesystem rather
                          than from an assumption.
  DID YESTERDAY LAND      For the previous trading day: expected against
                          captured, per component, with the missing tickers
                          named.
  IS IT SOUND             Duplicate dates, future dates, impossible prices,
                          crossed quotes, a contract that is not the one
                          recommended, a missing config hash.
  CAN IT BE REPRODUCED    Does every config hash in the history still have
                          the configuration behind it on disk?
  WILL IT SURVIVE         How fast each store grows, and whether any
                          retention limit would delete a day before the
                          365-day horizon it is needed for.

Nothing here builds a second monitoring framework. The day-by-day record is
capture_health's, the calendar is capture_health's, and this reads both.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import capture_health as health

AUDIT_VERSION = "invest-audit-1.0.0"

HEALTHY, PARTIAL, FAILURE = health.HEALTHY, health.HEALTH_PARTIAL, health.FAILURE

# The longest horizon forward validation has to reach back across. Every
# retention limit in the app is measured against it.
LONGEST_HORIZON_DAYS = 365
# Trading days in a calendar year — what a limit counted in stored entries
# has to clear to cover that horizon.
TRADING_DAYS_PER_YEAR = 252


# ── where the data lives ────────────────────────────────────────────────────

PERSISTENT = "PERSISTENT"
EPHEMERAL = "EPHEMERAL"
UNKNOWN = "UNKNOWN"


def data_home(data_dir=None) -> dict:
    """The data directory, and whether a redeploy would erase it.

    This is the single most important line in the whole audit. On Railway a
    volume mounted at /data survives deploys and restarts; without one the
    container's filesystem is rebuilt on every redeploy and every day of
    prospective history goes with it. The difference is visible from inside:
    a mounted volume is a different device from the root filesystem.
    """
    out = {"path": str(data_dir) if data_dir else "",
           "state": UNKNOWN, "reason": "", "env_var": "",
           "exists": False, "writable": False, "version": AUDIT_VERSION}
    env = (os.environ.get("JERRY_DATA_DIR") or "").strip()
    out["env_var"] = env
    if not data_dir:
        out["reason"] = ("No data directory is configured, so nothing is "
                         "being stored at all.")
        out["state"] = EPHEMERAL
        return out
    p = Path(data_dir)
    out["exists"] = p.is_dir()
    if out["exists"]:
        out["writable"] = os.access(p, os.W_OK)

    try:
        root_dev = os.stat("/").st_dev
        here_dev = os.stat(p if p.exists() else p.parent).st_dev
        mounted = here_dev != root_dev
    except Exception:                                # pragma: no cover
        mounted = None
    out["separate_device"] = mounted

    if mounted:
        out["state"] = PERSISTENT
        out["reason"] = (
            f"{p} sits on a different filesystem from the container's root, "
            f"which is what a mounted volume looks like from inside. A "
            f"redeploy replaces the container and leaves this alone.")
    elif str(p).startswith("/data"):
        out["state"] = PERSISTENT
        out["reason"] = (
            f"{p} is under /data, the mount point this app's deployment "
            f"notes use for its volume. It could not be confirmed as a "
            f"separate filesystem from inside the container, so check the "
            f"volume is actually attached.")
    else:
        out["state"] = EPHEMERAL
        out["reason"] = (
            f"{p} is on the container's own filesystem. It survives a "
            f"restart and does NOT survive a redeploy: every snapshot, "
            f"option chain and long-dated observation stored here is erased "
            f"the next time the app is deployed, and none of it can be "
            f"back-filled. Attach a volume and point JERRY_DATA_DIR at it "
            f"before letting the history accumulate."
            + (f" JERRY_DATA_DIR is set to {env!r}." if env else
               " JERRY_DATA_DIR is not set."))
    return out


# ── did yesterday land ──────────────────────────────────────────────────────

def previous_day(expected: dict, today=None, day=None) -> dict:
    """The previous trading day, component by component, with names.

    Yesterday is the honest thing to audit: today may still be in progress,
    and a day whose capture window has not passed is not a failure. Reuses
    capture_health for both the calendar and the log.
    """
    ref = health._as_date(today or date.today()).isoformat()  # noqa: SLF001
    d = day or health.previous_trading_day(ref)
    status = health.day_status(d, expected)
    rows = []
    for kind in health.KINDS:
        block = status["kinds"].get(kind) or {}
        want = block.get("expected") or 0
        got = block.get("successful") or 0
        rows.append({
            "kind": kind, "label": health.KIND_LABEL[kind],
            "expected": want, "captured": got,
            "missing": block.get("missing") or [],
            "late": block.get("late") or [],
            "state": block.get("state"),
            "pct": (got / want * 100.0) if want else None,
            "recoverable": kind != health.CHAIN,
            "note": health.KIND_NOTE[kind],
        })
    return {
        "date": d, "pretty": health._pretty(d),          # noqa: SLF001
        "state": status["state"], "reason": status["reason"],
        "expected_symbols": sorted({s for v in (expected or {}).values()
                                    for s in v}),
        "components": rows,
        "missing_by_component": {r["kind"]: r["missing"] for r in rows
                                 if r["missing"]},
        "version": AUDIT_VERSION,
    }


# ── is it sound ─────────────────────────────────────────────────────────────
#
# Every check here answers "could this row be believed later". None of them
# rewrites anything. A finding is a finding.

MISSING_DATE = "NO DATE"
DUPLICATE_DATE = "DUPLICATE DATE"
FUTURE_DATE = "FUTURE DATE"
OUT_OF_ORDER = "OUT OF ORDER"
BAD_PRICE = "IMPOSSIBLE PRICE"
MISSING_HASH = "NO CONFIG HASH"
UNRECOVERABLE_CONFIG = "CONFIG NOT ARCHIVED"
CONTRACT_MISMATCH = "CONTRACT DOES NOT MATCH THE RECOMMENDATION"
CROSSED_QUOTE = "CROSSED QUOTE"
BAD_SPOT = "IMPOSSIBLE UNDERLYING PRICE"
WRONG_EXPIRATION = "EXPIRATION BEFORE THE OBSERVATION"

FINDING_NOTE = {
    MISSING_DATE: "A stored row carries no trading day at all, so there is "
                  "no day to score it on and nothing to compare it against.",
    DUPLICATE_DATE: "Two rows claim the same trading day. Only one of them "
                    "was the decision made that day.",
    FUTURE_DATE: "A row is dated later than the day it was read on. A "
                 "recommendation cannot be made for a day that has not "
                 "happened, and scoring it would measure a return against a "
                 "price that did not exist.",
    OUT_OF_ORDER: "The rows are not in date order, so a reader walking them "
                  "forward would walk backwards.",
    BAD_PRICE: "A share price of zero or less is not a price.",
    MISSING_HASH: "Without the config hash there is no way to say which "
                  "rules produced this recommendation.",
    UNRECOVERABLE_CONFIG: "The row names a configuration that is not in the "
                          "archive, so the rules behind it cannot be read "
                          "back. Rows written from here on will be.",
    CONTRACT_MISMATCH: "The stored quote belongs to a different contract "
                       "from the one the recommendation names, so settling "
                       "up against it would score the wrong trade.",
    CROSSED_QUOTE: "The bid is above the ask. No such market existed.",
    BAD_SPOT: "The underlying price stored with this chain is zero or less.",
    WRONG_EXPIRATION: "The chain holds an expiration that had already passed "
                      "on the day it was captured.",
}


def _finding(kind, symbol, where, detail):
    return {"finding": kind, "symbol": symbol, "where": where,
            "detail": detail, "note": FINDING_NOTE[kind]}


def audit_history(symbol: str, rows: list, today=None,
                  known_hashes=None) -> list:
    """Every problem in one ticker's stored recommendation history."""
    ref = health._as_date(today or date.today()).isoformat()   # noqa: SLF001
    out, seen, last = [], set(), ""
    for row in rows or []:
        day = str(row.get("date") or "")[:10]
        if not day:
            out.append(_finding(MISSING_DATE, symbol, "(no date)",
                                "a row with no date at all"))
            continue
        if day in seen:
            out.append(_finding(DUPLICATE_DATE, symbol, day,
                                f"{day} appears more than once"))
        seen.add(day)
        if day > ref:
            out.append(_finding(FUTURE_DATE, symbol, day,
                                f"dated {day}, later than {ref}"))
        if last and day < last:
            out.append(_finding(OUT_OF_ORDER, symbol, day,
                                f"{day} follows {last}"))
        last = day

        price = row.get("price")
        if price is not None and not (isinstance(price, (int, float))
                                      and price > 0):
            out.append(_finding(BAD_PRICE, symbol, day, f"price {price!r}"))

        cfg_hash = row.get("config_hash")
        if not cfg_hash:
            out.append(_finding(MISSING_HASH, symbol, day, "no config_hash"))
        elif known_hashes is not None and cfg_hash not in known_hashes:
            out.append(_finding(UNRECOVERABLE_CONFIG, symbol, day,
                                f"config {cfg_hash} is not in the archive"))

        out.extend(_audit_contract(symbol, day, row))
    return out


def _audit_contract(symbol: str, day: str, row: dict) -> list:
    """Does the stored quote belong to the contract that was recommended?"""
    contract = row.get("recommended_contract")
    if not contract:
        return []
    out = []
    preferred = row.get("preferred_structure")
    named = contract.get("structure")
    if preferred and named and preferred != named:
        out.append(_finding(CONTRACT_MISMATCH, symbol, day,
                            f"the row prefers {preferred} and the stored "
                            f"contract is a {named}"))
    bid, ask = contract.get("bid"), contract.get("ask")
    if (isinstance(bid, (int, float)) and isinstance(ask, (int, float))
            and ask > 0 and bid > ask):
        out.append(_finding(CROSSED_QUOTE, symbol, day,
                            f"bid {bid} above ask {ask}"))
    exp = str(contract.get("expiration") or "")[:10]
    if exp and day and exp < day:
        out.append(_finding(WRONG_EXPIRATION, symbol, day,
                            f"expiration {exp} before the observation"))
    return out


def audit_chains(symbol: str, store: dict, today=None) -> list:
    """Every problem in one ticker's captured option-chain history."""
    ref = health._as_date(today or date.today()).isoformat()   # noqa: SLF001
    out = []
    for day in sorted(store or {}):
        snap = store.get(day) or {}
        if day > ref:
            out.append(_finding(FUTURE_DATE, symbol, day,
                                f"a chain dated {day}, later than {ref}"))
        spot = snap.get("spot")
        if not (isinstance(spot, (int, float)) and spot > 0):
            out.append(_finding(BAD_SPOT, symbol, day, f"spot {spot!r}"))
        for exp, sides in (snap.get("exps") or {}).items():
            if str(exp)[:10] < day:
                out.append(_finding(WRONG_EXPIRATION, symbol, day,
                                    f"expiration {exp} in a chain captured "
                                    f"on {day}"))
            for rows in (sides or {}).values():
                for r in rows or []:
                    # [strike, bid, ask, ...] — the version-1 layout, which
                    # every stored row starts with.
                    if len(r) < 3:
                        continue
                    bid, ask = r[1], r[2]
                    if ask > 0 and bid > ask:
                        out.append(_finding(
                            CROSSED_QUOTE, symbol, day,
                            f"{exp} strike {r[0]}: bid {bid} above ask {ask}"))
                        break
    return out


# ── will it survive ─────────────────────────────────────────────────────────

def retention(limits: dict) -> dict:
    """Every retention limit, against the horizon it has to clear.

    A limit counted in stored entries is counted in TRADING days, because
    that is what gets stored — so it clears a 365-calendar-day horizon at
    252 entries, not 365.
    """
    need = int(LONGEST_HORIZON_DAYS / 365.0 * TRADING_DAYS_PER_YEAR)
    rows = []
    for name, spec in sorted((limits or {}).items()):
        kept = spec.get("keeps")
        unbounded = kept in (None, 0)
        rows.append({
            "store": name, "label": spec.get("label") or name,
            "keeps": kept, "unit": spec.get("unit") or "trading days",
            "unbounded": unbounded,
            "covers_longest_horizon": True if unbounded else kept >= need,
            "margin_days": None if unbounded else kept - need,
            "note": spec.get("note") or "",
        })
    short = [r for r in rows if not r["covers_longest_horizon"]]
    return {
        "horizon_days": LONGEST_HORIZON_DAYS,
        "trading_days_needed": need,
        "limits": rows, "too_short": short,
        "ok": not short,
        "reason": (
            f"Every retention limit keeps at least {need} trading days, "
            f"which is what a {LONGEST_HORIZON_DAYS}-day horizon needs."
            if not short else
            "A retention limit would delete a day before the longest "
            "validation horizon reaches it: "
            + ", ".join(f"{r['label']} keeps {r['keeps']}" for r in short)),
        "version": AUDIT_VERSION,
    }


def _dir_bytes(p) -> int:
    try:
        root = Path(p)
        if not root.is_dir():
            return 0
        return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    except Exception:                                # pragma: no cover
        return 0


def storage(stores: dict, symbols: int = 0, days: int = 0) -> dict:
    """What each store holds now, and what a year of it would come to.

    Projected from what is actually on disk rather than from a guess: bytes
    per symbol per captured day, times the trading days in a year.
    """
    rows = []
    for name, spec in sorted((stores or {}).items()):
        used = _dir_bytes(spec.get("path"))
        per_day = (used / (symbols * days)) if (symbols and days) else None
        rows.append({
            "store": name, "label": spec.get("label") or name,
            "path": str(spec.get("path") or ""), "bytes": used,
            "megabytes": round(used / 1e6, 2),
            "bytes_per_symbol_day": round(per_day) if per_day else None,
            "projected_mb_per_year": (
                round(per_day * TRADING_DAYS_PER_YEAR * symbols / 1e6, 1)
                if per_day and symbols else None),
            "note": spec.get("note") or "",
        })
    total = sum(r["bytes"] for r in rows)
    return {
        "stores": rows, "bytes": total, "megabytes": round(total / 1e6, 2),
        "symbols": symbols, "captured_days": days,
        "projected_mb_per_year": round(
            sum(r["projected_mb_per_year"] or 0 for r in rows), 1),
        "version": AUDIT_VERSION,
    }
