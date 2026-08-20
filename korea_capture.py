"""korea_capture.py — the genuine point-in-time record, captured forward.

Everything else in Korea Lead measures the past out of daily bars. This
module records the PRESENT, once a day, so that in a year there is a
history of what the app actually believed before each U.S. open — as
opposed to what today's model, run backwards, would have believed.

THE QUESTION THIS EXISTS TO ANSWER

  At what point during the Korean session does the predictive information
  stop improving?

No amount of daily history can answer it. A daily bar holds one number for
the whole session; the 11:00 state and the 15:30 state are the same bar.
So the answer has to be accumulated, one morning at a time, from now on.
If it turns out that waiting past 14:00 Seoul adds almost nothing, that is
worth a great deal — and it cannot be discovered without this file.

FIVE RULES, AND THEY ARE THE WHOLE DESIGN

  1. NOTHING IS EVER BACKFILLED. A checkpoint the app was not running for
     is MISSED, permanently. Fetching at 13:47 and filing it as the 13:00
     observation would silently poison every future study of when the
     signal matures — the record would say the app knew at 13:00 something
     it only learned at 13:47. The actual capture time is stored on every
     record, always.

  2. A HOLIDAY IS NOT A MISS. MISSED means a session existed and we failed
     to record it. When the evidence later shows Korea never traded, the
     date is NO KOREA SESSION instead. Counting holidays as failures would
     make the capture rate look broken and, worse, would put empty rows
     into a study that reads them as real sessions.

  3. PREDICTIONS ARE IMMUTABLE. The 9:25 record is written once and never
     touched again. What happened afterwards is a SEPARATE record that
     points back at it by snapshot_id. Editing a forecast after seeing the
     answer is not a bug you notice; it is a bug that makes everything
     downstream look good.

  4. RAW INPUTS, NOT ONLY LABELS. "DOWN GAP BIAS" is useless in a year — it
     cannot be recomputed, checked, or reinterpreted. Every number that
     produced a label is stored beside it.

  5. EVERY RECORD CARRIES ITS VERSIONS. Schema, both engine versions, the
     signal definition and the settings hash. When the model changes six
     months from now, old predictions stay attributable to the model that
     made them, and re-running today's model over an old date is a
     BACKTEST — never a forward record.

WHAT THIS MODULE DOES NOT DO

  It fits no models, downloads no history, and runs no research. It reads
  state that is already cached and writes it down. One daemon thread that
  sleeps between checkpoints and cannot take the dashboard down with it.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import korea_lead as kl
import korea_lead_engine as kle

MODULE_VERSION = "korea-capture-1.0.0"
SCHEMA_VERSION = 1

try:
    from zoneinfo import ZoneInfo
    _KST = ZoneInfo("Asia/Seoul")
    _ET = ZoneInfo("America/New_York")
except Exception:                      # pragma: no cover - stdlib since 3.9
    _KST = _ET = None

# The U.S. universe the pre-open snapshot covers. Server-side and fixed on
# purpose: whichever ticker happens to be selected in somebody's browser at
# 9:25 is not server state, and a prediction that only exists on the
# mornings when someone was watching is not a record of anything.
SNAPSHOT_UNIVERSE = ["QQQ", "SMH", "SOXX", "MU", "NVDA", "AVGO", "AMD",
                     "MRVL", "WDC", "STX", "SNDK", "LRCX", "AMAT", "KLAC"]

# Record kinds. Kept as strings in the file so a human reading the JSONL
# can tell what they are looking at without a decoder.
KIND_CHECKPOINT = "korea_checkpoint"
KIND_SNAPSHOT = "pre_open_snapshot"
KIND_OUTCOME = "outcome"

STATUS_CAPTURED = "CAPTURED"
STATUS_MISSED = "MISSED"
STATUS_NO_SESSION = "NO KOREA SESSION"
STATUS_PARTIAL = "PARTIAL"

_LOCK = threading.Lock()
_THREAD: dict = {"t": None, "started": False}
_STATE: dict = {"running": False, "last_cycle": None, "error": None,
                "captured": 0, "missed": 0, "last_event": None}
# What has already been handled, so a checkpoint is written once even though
# the loop wakes every half minute. Keyed by a string that already contains
# its own date, and pruned by age rather than cleared on a date change:
# clearing it when the Eastern date rolls would wipe the memory of Korean
# checkpoints in the MIDDLE of the Seoul session — midnight in New York is
# one in the afternoon in Seoul — and the 11:00 capture already taken would
# then be re-examined, found late, and filed as MISSED on top of itself.
_DONE: dict = {}                # key -> unix timestamp it was handled at
_DONE_TTL_S = 3 * 24 * 3600.0


# ── storage: append-only JSONL, one file per month ──────────────────────────

def _dir() -> Path | None:
    root = kl._DATA_DIR
    if not root:
        return None
    p = Path(root) / "korea" / "forward"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as exc:          # pragma: no cover
        print(f"[korea-capture] storage init failed: {exc}")
        return None
    return p


def _file_for(day: str) -> Path | None:
    d = _dir()
    return None if d is None else d / f"{str(day)[:7]}.jsonl"


def append(record: dict) -> bool:
    """Write one record. Append-only: this function has no other mode.

    There is deliberately no update path anywhere in this module. A
    prediction that can be edited after the fact is worse than no
    prediction, because it looks like evidence and is a memory of the
    answer. Later knowledge arrives as a NEW record pointing back at the
    old one.
    """
    p = _file_for(record.get("session_date") or record.get("et_date") or "")
    if p is None:
        return False
    try:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":"),
                                sort_keys=True) + "\n")
        return True
    except Exception as exc:          # pragma: no cover
        print(f"[korea-capture] write failed: {exc}")
        return False


def already_recorded(day: str, kind: str, checkpoint: str | None = None) -> bool:
    """Has this slot already been written to the file?

    The in-memory memo alone is not enough. A container that restarts three
    times in an evening starts with an empty memo each time, would find the
    same checkpoint long past its grace window on each pass, and would file
    three MISSED records for one missed checkpoint — turning a modest
    outage into what looks like a systematic failure. The file is the
    memory that survives the restart, so it is what gets asked.
    """
    p = _file_for(day)
    if p is None or not p.exists():
        return False
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or r.get("kind") != kind:
                continue
            if (r.get("session_date") or r.get("et_date")) != day:
                continue
            if checkpoint is not None and r.get("checkpoint") != checkpoint:
                continue
            return True
    except Exception:                 # pragma: no cover
        return False
    return False


def read_records(since_days: int = 400, kinds=None) -> list:
    """Every stored record, oldest first. Corrupt lines are skipped and
    counted rather than raising — a half-written line from a container that
    died mid-append must not take out the reader."""
    d = _dir()
    if d is None:
        return []
    cutoff = (_today_et() - timedelta(days=int(since_days))).isoformat()
    want = set(kinds) if kinds else None
    out = []
    for f in sorted(d.glob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                if want and r.get("kind") not in want:
                    continue
                key = r.get("session_date") or r.get("et_date") or ""
                if key and key < cutoff:
                    continue
                out.append(r)
        except Exception:             # pragma: no cover
            continue
    out.sort(key=lambda r: (r.get("session_date") or r.get("et_date") or "",
                            r.get("captured_at") or ""))
    return out


# ── clocks ──────────────────────────────────────────────────────────────────

def _now_kst() -> datetime:
    return kl._now_kst()


def _now_et() -> datetime:
    return kl._now_et()


def _today_et() -> date:
    return _now_et().date()


def _hhmm(s) -> dtime | None:
    try:
        h, _, m = str(s).partition(":")
        return dtime(int(h), int(m))
    except (TypeError, ValueError):
        return None


def _minutes_past(now: datetime, target: dtime) -> float:
    return ((now.hour * 60 + now.minute + now.second / 60.0)
            - (target.hour * 60 + target.minute))


def _cfg() -> dict:
    return kl._section("forward")


# ── the identity every record carries ───────────────────────────────────────

def _versions() -> dict:
    """The provenance block. Without it a stored prediction is an orphan:
    in a year nobody can say which model produced it, which settings it was
    produced under, or whether the numbers still mean the same thing."""
    try:
        import korea_research_engine as kre
        research = kre.ENGINE_VERSION
    except Exception:                 # pragma: no cover
        research = None
    return {
        "schema_version": SCHEMA_VERSION,
        "capture_module_version": MODULE_VERSION,
        "korea_lead_module_version": kl.MODULE_VERSION,
        "korea_lead_engine_version": kle.ENGINE_VERSION,
        "korea_research_engine_version": research,
        "signal_definition": kle.SIGNAL_DEFINITION,
        "config_hash": kl.config()[1],
    }


def _korea_block(korea: dict) -> dict:
    """The raw Korean inputs, with their provider timestamps — never only
    the labels those inputs produced."""
    series = korea.get("series") or {}

    def one(name):
        s = series.get(name) or {}
        return {"pct": s.get("pct"), "close": s.get("close"),
                "session_date": s.get("session_date"),
                "provider_timestamp": s.get("provider_timestamp"),
                "freshness": (s.get("freshness") or {}).get("state"),
                "age_s": (s.get("freshness") or {}).get("age_s"),
                "abs_percentile": s.get("abs_percentile"),
                "trailing_n": s.get("trailing_n"),
                "move_state": s.get("move_state"),
                "source": s.get("source")}

    return {
        "kospi": one(kl.KOSPI), "samsung": one(kl.SAMSUNG),
        "hynix": one(kl.HYNIX), "usdkrw": one(kl.USDKRW),
        "chip_confirmation": korea.get("chip_confirmation"),
        "signal_ok": (korea.get("signal") or {}).get("ok"),
        "signal_reason": (korea.get("signal") or {}).get("reason"),
        "unusual": korea.get("unusual"),
    }


# ── checkpoints during the Korean session ───────────────────────────────────

def capture_checkpoint(label: str, scheduled_kst: str, force: bool = False) -> dict:
    """Archive the Korean state as it stands right now, filed under the
    checkpoint it belongs to.

    `scheduled_kst` is what the record is FOR; `captured_at` is when it
    actually happened. Both are stored on every record, and they are the
    difference between a study that can trust its own timestamps and one
    that cannot.
    """
    now_k = _now_kst()
    korea = kl.korea_today(force=force)
    sess = korea.get("session") or {}
    rec = {
        "kind": KIND_CHECKPOINT, "checkpoint": label,
        "scheduled_kst": scheduled_kst,
        "session_date": sess.get("seoul_date"),
        "captured_at": now_k.isoformat(timespec="seconds"),
        "captured_kst": now_k.strftime("%H:%M:%S"),
        "late_minutes": round(_minutes_past(now_k, _hhmm(scheduled_kst)
                                            or dtime(0, 0)), 2),
        "status": STATUS_CAPTURED,
        "scheduled_state": sess.get("scheduled_state"),
        "data_state": sess.get("data_state"),
        "final": bool(sess.get("final")),
        "preliminary": not bool(sess.get("final")),
        "latest_market_timestamp": sess.get("latest_market_timestamp"),
        "korea": _korea_block(korea),
    }
    rec.update(_versions())
    if rec["korea"]["kospi"]["session_date"] != rec["session_date"]:
        # Korea did not trade today, or has not printed today's bar. Either
        # way this is not a captured Korean session and must never be
        # counted as one.
        rec["status"] = STATUS_NO_SESSION
        rec["status_reason"] = (
            "No Korean bar carries today's Seoul date at this checkpoint, so "
            "there is no session state to record. This is not a missed "
            "capture — a capture that found nothing to capture is a fact "
            "about the market, not about the app.")
    append(rec)
    _note(f"checkpoint {label} {rec['status']}", rec["status"] != STATUS_CAPTURED)
    return rec


def record_missed(label: str, scheduled_kst: str, session_date: str,
                  reason: str) -> dict:
    """File a checkpoint the app was not there for.

    Deliberately a first-class record rather than an absence. A gap in the
    file is ambiguous — was the app down, was it a holiday, did the write
    fail? — and ambiguity is what a future study will resolve in whichever
    direction flatters it.
    """
    rec = {"kind": KIND_CHECKPOINT, "checkpoint": label,
           "scheduled_kst": scheduled_kst, "session_date": session_date,
           "captured_at": None, "status": STATUS_MISSED,
           "status_reason": reason,
           "noticed_at": _now_kst().isoformat(timespec="seconds")}
    rec.update(_versions())
    append(rec)
    _note(f"checkpoint {label} MISSED", True)
    return rec


# ── the pre-open forward prediction ─────────────────────────────────────────

def capture_snapshot(symbols=None, force: bool = False) -> dict:
    """What the app genuinely believed, for every target, before the U.S.
    opened. The single most valuable record this module writes.

    One record per ticker, each with its own snapshot_id, each immutable.
    Raw inputs travel with every label so that a future engine can work out
    what the older engine saw — and so that a label whose definition
    changes later does not silently rewrite history.
    """
    syms = [s.upper() for s in (symbols or SNAPSHOT_UNIVERSE)]
    now_et = _now_et()
    et_date = now_et.date().isoformat()
    out = {"et_date": et_date, "written": [], "failed": [],
           "captured_at": now_et.isoformat(timespec="seconds")}
    korea = kl.korea_today(force=force)
    sess = korea.get("session") or {}
    kblock = _korea_block(korea)
    base_id = f"{et_date}T{now_et.strftime('%H%M%S')}"
    for sym in syms:
        try:
            p = kl.payload(sym, force=False)
        except Exception as exc:      # noqa: BLE001
            out["failed"].append({"symbol": sym, "error": str(exc)[:200]})
            continue
        if not p.get("ok"):
            out["failed"].append({"symbol": sym,
                                  "error": p.get("error") or "no payload"})
            continue
        og = p.get("opening_gap") or {}
        impl = og.get("implied") or {}
        est = p.get("estimates") or {}
        rel = p.get("relationship") or {}
        pm = ((p.get("target") or {}).get("premarket")) or {}
        rec = {
            "kind": KIND_SNAPSHOT,
            "snapshot_id": f"{base_id}:{sym}",
            "symbol": sym, "et_date": et_date,
            "session_date": sess.get("seoul_date"),
            "captured_at": now_et.isoformat(timespec="seconds"),
            "scheduled_et": (_cfg().get("snapshot_et") or "09:25"),
            "status": STATUS_CAPTURED,
            "korea_session": {
                "scheduled_state": sess.get("scheduled_state"),
                "data_state": sess.get("data_state"),
                "final": bool(sess.get("final")),
                "final_by_fallback": bool(sess.get("final_by_fallback")),
                "latest_market_timestamp": sess.get("latest_market_timestamp"),
            },
            "korea": kblock,
            # ── the labels ──
            "bias_state": (og.get("bias") or {}).get("state"),
            "bias_detail": (og.get("bias") or {}).get("detail"),
            "relationship_health": (rel.get("health") or {}).get("state"),
            "relationship_unstable": bool(rel.get("unstable")),
            "primary_driver": (p.get("primary_driver") or {}).get("driver"),
            "primary_driver_basis": (p.get("primary_driver") or {}).get("basis"),
            # ── the raw inputs behind them ──
            "bucket": impl.get("bucket"),
            "bucket_label": impl.get("label"),
            "bucket_n": impl.get("n"),
            "match_n": (impl.get("same_direction") or {}).get("n"),
            "match_count": (impl.get("same_direction") or {}).get("count"),
            "match_rate_pct": (impl.get("same_direction") or {}).get("rate_pct"),
            "wilson_lo_pct": (impl.get("same_direction") or {}).get("lo_pct"),
            "wilson_hi_pct": (impl.get("same_direction") or {}).get("hi_pct"),
            "implied_median_pct": (impl.get("distribution") or {}).get("median_pct"),
            "implied_q25_pct": (impl.get("distribution") or {}).get("p25_pct"),
            "implied_q75_pct": (impl.get("distribution") or {}).get("p75_pct"),
            "regression_expected_pct": ((est.get("regression") or {})
                                        .get("expected_pct")),
            "regression_n": (est.get("regression") or {}).get("n"),
            "estimate_agreement": (est.get("agreement") or {}).get("state"),
            "rolling_60d_r": (rel.get("recent") or {}).get("r"),
            "rolling_60d_n": (rel.get("recent") or {}).get("n"),
            "rolling_1y_r": (rel.get("long") or {}).get("r"),
            "rolling_1y_n": (rel.get("long") or {}).get("n"),
            "window": p.get("window"),
            "matched_sessions": (p.get("target") or {}).get("n"),
            # ── the premarket side, with its basis ──
            "premarket_gap_pct": pm.get("gap_pct") if pm.get("fresh_enough") else None,
            "premarket_basis": pm.get("basis"),
            "premarket_basis_label": pm.get("basis_label"),
            "premarket_mid_gap_pct": pm.get("mid_gap_pct"),
            "premarket_fresh": bool(pm.get("fresh_enough")),
            "premarket_quote_age_s": pm.get("quote_age_s"),
            "premarket_quote_source": pm.get("quote_source"),
            "premarket_prev_close": pm.get("prev_close"),
            "premarket_not_available_reason": pm.get("not_available_reason"),
            "residual_pct": (p.get("premarket_comparison") or {}).get("residual_pct"),
            "self_check_failed": (p.get("self_check") or {}).get("failed"),
        }
        rec.update(_versions())
        if append(rec):
            out["written"].append(rec["snapshot_id"])
        else:
            out["failed"].append({"symbol": sym, "error": "write failed"})
    out["status"] = (STATUS_CAPTURED if out["written"] and not out["failed"]
                     else (STATUS_PARTIAL if out["written"] else STATUS_MISSED))
    _note(f"pre-open snapshot {out['status']} "
          f"({len(out['written'])}/{len(syms)})",
          out["status"] != STATUS_CAPTURED)
    return out


def record_missed_snapshot(et_date: str, reason: str) -> dict:
    rec = {"kind": KIND_SNAPSHOT, "snapshot_id": f"{et_date}:MISSED",
           "symbol": None, "et_date": et_date, "captured_at": None,
           "status": STATUS_MISSED, "status_reason": reason,
           "noticed_at": _now_et().isoformat(timespec="seconds")}
    rec.update(_versions())
    append(rec)
    _note("pre-open snapshot MISSED", True)
    return rec


# ── what actually happened, as a separate record ────────────────────────────

def evaluate(et_date: str | None = None, daily_fn=None) -> dict:
    """Score yesterday's predictions against what the market did, writing
    the answer as NEW records that point back by snapshot_id.

    The prediction is read from the file, never recomputed. Re-running
    today's model over an old date and calling the result that morning's
    forecast is the single most effective way to make a model look
    prescient, and it is a backtest wearing a forward record's clothes.

    Minute-resolution outcomes are only written when genuine minute history
    exists for that session. They are not reconstructed from daily bars,
    because a daily bar does not know when the high happened.
    """
    day = et_date or (_today_et() - timedelta(days=1)).isoformat()
    preds = [r for r in read_records(90, kinds=[KIND_SNAPSHOT])
             if r.get("et_date") == day and r.get("status") == STATUS_CAPTURED
             and r.get("symbol")]
    done = {r.get("snapshot_id") for r in read_records(90, kinds=[KIND_OUTCOME])}
    out = {"et_date": day, "scored": 0, "skipped": 0, "already": 0,
           "no_predictions": not preds}
    for p in preds:
        sid = p.get("snapshot_id")
        if sid in done:
            out["already"] += 1
            continue
        sym = p["symbol"]
        try:
            bars = (kl.us_bars(sym) or {}).get("bars") or []
            measures = kle.us_measures(bars, max_move_pct=kl._max_move())
        except Exception:             # noqa: BLE001
            out["skipped"] += 1
            continue
        m = measures.get(day)
        if not m:
            out["skipped"] += 1
            continue
        actual = m.get("opening_gap")
        predicted = p.get("implied_median_pct")
        rec = {
            "kind": KIND_OUTCOME, "snapshot_id": sid, "symbol": sym,
            "et_date": day, "session_date": p.get("session_date"),
            "evaluated_at": _now_et().isoformat(timespec="seconds"),
            "official_opening_gap_pct": actual,
            "open_to_close_pct": m.get("open_to_close"),
            "full_day_pct": m.get("full_day"),
            # Every derived score states which archived prediction it came
            # from, so the arithmetic can be checked without trusting it.
            "predicted_gap_pct": predicted,
            "predicted_by": "bucket median archived at 9:25",
            "predicted_bias": p.get("bias_state"),
        }
        if actual is not None and predicted is not None:
            rec.update({
                "direction_correct": bool((actual > 0) == (predicted > 0)
                                          and actual != 0 and predicted != 0),
                "abs_gap_error_pct": round(abs(actual - predicted), 4),
                "signed_gap_error_pct": round(actual - predicted, 4),
            })
        else:
            rec["direction_correct"] = None
            rec["scoring_note"] = (
                "Either the archived prediction or the official opening gap "
                "is missing, so this morning is recorded without a score "
                "rather than scored against a guess.")
        # Minute-resolution outcomes, only where minute history genuinely
        # exists. Never reconstructed: a daily bar cannot say when the high
        # of the day happened, and inventing a time would put a fabricated
        # number into the one dataset that exists to be trustworthy.
        rec["minute_outcomes"] = None
        rec["minute_outcomes_note"] = (
            "Not available. This app keeps minute paths only for days that "
            "qualified as gap events, and what it stores is the favourable "
            "and adverse excursion rather than the clock time of the session "
            "high or low. First-30-minute, first-60-minute, open-to-noon, "
            "high-of-day time and low-of-day time are therefore left empty "
            "rather than reconstructed from a daily bar, which does not know "
            "when anything happened inside it.")
        rec.update(_versions())
        # The versions on an outcome describe the SCORER, not the forecast.
        # The forecast's own versions stay on the immutable prediction where
        # they belong, and are echoed here so a mismatch is visible.
        rec["prediction_versions"] = {
            k: p.get(k) for k in
            ("schema_version", "korea_lead_engine_version",
             "korea_research_engine_version", "signal_definition",
             "config_hash")}
        rec["scored_by_same_engine"] = (
            rec["prediction_versions"].get("korea_lead_engine_version")
            == kle.ENGINE_VERSION
            and rec["prediction_versions"].get("config_hash")
            == kl.config()[1])
        append(rec)
        out["scored"] += 1
    return out


# ── the forward scorecard ───────────────────────────────────────────────────

def scorecard(symbol: str | None = None, days: int = 400) -> dict:
    """What the app has actually got right since this file started keeping
    score. FORWARD RECORDED, and labelled as such everywhere.

    It is never combined with the backtested match rates on the panel. They
    answer different questions — one is what the relationship did across
    ten years, the other is what this app said out loud on N mornings — and
    a single blended number would be neither.
    """
    cfg = _cfg()
    min_n = int(cfg.get("min_forward_n", 40))
    outs = [r for r in read_records(days, kinds=[KIND_OUTCOME])
            if (not symbol or r.get("symbol") == str(symbol).upper())]
    scored = [r for r in outs if r.get("direction_correct") is not None]
    out = {"basis": "FORWARD RECORDED", "symbol": (symbol or "").upper() or None,
           "n": len(scored), "min_n": min_n, "usable": False,
           "first_date": scored[0].get("et_date") if scored else None,
           "last_date": scored[-1].get("et_date") if scored else None,
           "note": ("These are the app's own archived predictions scored "
                    "against what happened afterwards. They are never mixed "
                    "with the historical match rates on the panel: a "
                    "backtest and a forward record are different claims, and "
                    "one hit rate covering both would be neither of them.")}
    if len(scored) < min_n:
        out["reason"] = (
            f"{len(scored)} forward record{'' if len(scored) == 1 else 's'} "
            f"so far, of the {min_n} required before a forward scorecard "
            f"means anything. A scorecard built on a handful of mornings is "
            f"worse than none, because it looks like evidence.")
        return out
    right = sum(1 for r in scored if r["direction_correct"])
    errs = sorted(r["abs_gap_error_pct"] for r in scored
                  if r.get("abs_gap_error_pct") is not None)
    mid = len(errs) // 2
    out.update({
        "usable": True,
        "direction_correct": right,
        "direction_pct": round(right / len(scored) * 100.0, 1),
        "gap_mae_pct": (round(sum(errs) / len(errs), 3) if errs else None),
        "gap_median_abs_error_pct": (
            None if not errs else round(
                errs[mid] if len(errs) % 2 else (errs[mid - 1] + errs[mid]) / 2.0,
                3)),
        "engine_versions": sorted({
            (r.get("prediction_versions") or {}).get("korea_lead_engine_version")
            for r in scored if r.get("prediction_versions")} - {None}),
        "config_hashes": sorted({
            (r.get("prediction_versions") or {}).get("config_hash")
            for r in scored if r.get("prediction_versions")} - {None}),
    })
    # Predictions made under different engines or settings are still all
    # forward records, but they are not one experiment. Saying so is
    # cheaper than pretending otherwise.
    if len(out["engine_versions"]) > 1 or len(out["config_hashes"]) > 1:
        out["mixed_versions_note"] = (
            "These records were not all produced by the same engine or the "
            "same settings. They remain genuine forward predictions, but "
            "they are not a single experiment and the rate above averages "
            "across more than one model.")
    return out


def coverage(days: int = 120) -> dict:
    """How complete the forward record actually is — captured, missed and
    holidays counted separately, because a study that cannot tell them
    apart will quietly treat absence as evidence."""
    recs = read_records(days, kinds=[KIND_CHECKPOINT, KIND_SNAPSHOT])
    by_status: dict = {}
    per_checkpoint: dict = {}
    for r in recs:
        st = r.get("status") or "UNKNOWN"
        by_status[st] = by_status.get(st, 0) + 1
        if r.get("kind") == KIND_CHECKPOINT:
            cp = r.get("checkpoint") or "?"
            slot = per_checkpoint.setdefault(
                cp, {"captured": 0, "missed": 0, "no_session": 0})
            if st == STATUS_CAPTURED:
                slot["captured"] += 1
            elif st == STATUS_MISSED:
                slot["missed"] += 1
            elif st == STATUS_NO_SESSION:
                slot["no_session"] += 1
    days_seen = sorted({r.get("session_date") or r.get("et_date")
                        for r in recs} - {None})
    return {
        "days": days, "records": len(recs), "by_status": by_status,
        "per_checkpoint": per_checkpoint,
        "first_date": days_seen[0] if days_seen else None,
        "last_date": days_seen[-1] if days_seen else None,
        "sessions_touched": len(days_seen),
        "note": ("MISSED means a Korean session existed and this app failed "
                 "to record it. NO KOREA SESSION means the market was shut. "
                 "They are counted apart because treating a holiday as a "
                 "failure would make the capture rate look broken, and "
                 "treating a failure as a holiday would hide it."),
    }


def status() -> dict:
    with _LOCK:
        return dict(_STATE, universe=list(SNAPSHOT_UNIVERSE),
                    thread_started=bool(_THREAD["started"]),
                    thread_alive=bool(_THREAD["t"] and _THREAD["t"].is_alive()),
                    checkpoints=list(_cfg().get("checkpoints_kst") or []),
                    snapshot_et=_cfg().get("snapshot_et"))


def _note(event: str, notable: bool = False) -> None:
    """Log a state TRANSITION, never a poll.

    A capture loop that logs every wake produces a thousand lines a day and
    trains whoever maintains it to stop reading them. The only entries that
    reach the log are the ones that would change what somebody does.
    """
    with _LOCK:
        _STATE["last_event"] = f"{_now_et().strftime('%H:%M:%S')} {event}"
        if "MISSED" in event:
            _STATE["missed"] += 1
        elif "CAPTURED" in event or "checkpoint" in event:
            _STATE["captured"] += 1
    if notable:
        print(f"[korea-capture] {event}")


# ── the one background thread ───────────────────────────────────────────────

def _due(now: datetime, target: dtime, grace: float) -> str:
    """`on_time`, `late` or `pending` for one scheduled moment."""
    past = _minutes_past(now, target)
    if past < 0:
        return "pending"
    return "on_time" if past <= grace else "late"


def _cycle(now_kst=None, now_et=None) -> dict:
    """One pass. Pure enough to call directly from a test with injected
    clocks, which is the only way the schedule logic can be checked without
    waiting until 13:00 in Seoul."""
    nk = now_kst or _now_kst()
    ne = now_et or _now_et()
    cfg = _cfg()
    grace = float(cfg.get("checkpoint_grace_min", 12))
    snap_grace = float(cfg.get("snapshot_grace_min", 4))
    day_k = nk.date().isoformat()
    day_e = ne.date().isoformat()
    acted = []
    now_ts = time.time()
    with _LOCK:
        for k in [k for k, ts in _DONE.items() if now_ts - ts > _DONE_TTL_S]:
            _DONE.pop(k, None)
        done = set(_DONE)

    def mark(k):
        with _LOCK:
            _DONE[k] = time.time()

    # Korean checkpoints, weekdays only. A weekend needs no record: nothing
    # was missed, because nothing was scheduled.
    if nk.weekday() < 5:
        for hhmm in (cfg.get("checkpoints_kst") or []):
            t = _hhmm(hhmm)
            if t is None:
                continue
            key = f"{day_k}:cp:{hhmm}"
            if key in done:
                continue
            state = _due(nk, t, grace)
            if state == "pending":
                continue
            if already_recorded(day_k, KIND_CHECKPOINT, hhmm):
                mark(key)
                continue
            if state == "on_time":
                capture_checkpoint(hhmm, hhmm)
                acted.append(f"checkpoint {hhmm}")
            else:
                # Past the grace window. This is NEVER captured late and
                # filed as the scheduled observation — a 13:47 reading is
                # not the 13:00 one, and pretending otherwise would put a
                # lie into the only dataset built to be honest.
                record_missed(
                    hhmm, hhmm, day_k,
                    f"The app was not running, or was not able to capture, "
                    f"within {grace:g} minutes of {hhmm} Seoul. It is "
                    f"{nk.strftime('%H:%M')} now. A later reading is not "
                    f"this checkpoint and is not recorded as one.")
                acted.append(f"missed {hhmm}")
            mark(key)

        # The confirmed final close: taken once, when the data says the
        # session is settled rather than when the clock says it should be.
        key = f"{day_k}:cp:final"
        if key not in done:
            if already_recorded(day_k, KIND_CHECKPOINT, "final"):
                mark(key)
            else:
                sess = kl.session_view()
                if sess.get("final") and sess.get("bar_date") == day_k:
                    capture_checkpoint("final",
                                       kl.KOREA_CLOSE.strftime("%H:%M"))
                    acted.append("checkpoint final")
                    mark(key)

    # The pre-open snapshot, on U.S. weekdays.
    if ne.weekday() < 5:
        t = _hhmm(cfg.get("snapshot_et") or "09:25") or dtime(9, 25)
        key = f"{day_e}:snapshot"
        if key not in done and already_recorded(day_e, KIND_SNAPSHOT):
            mark(key)
        elif key not in done:
            state = _due(ne, t, snap_grace)
            if state == "on_time":
                capture_snapshot()
                acted.append("snapshot")
                mark(key)
            elif state == "late":
                record_missed_snapshot(
                    day_e,
                    f"The pre-open snapshot was not taken within "
                    f"{snap_grace:g} minutes of "
                    f"{cfg.get('snapshot_et')} Eastern. It is "
                    f"{ne.strftime('%H:%M')} now, and a record taken after "
                    f"the open is not a pre-open record.")
                acted.append("missed snapshot")
                mark(key)

        # Yesterday's predictions get their outcomes once the session is
        # done and the daily bar has settled.
        key = f"{day_e}:evaluate"
        if key not in done and ne.time() >= dtime(16, 30):
            evaluate()
            acted.append("evaluate")
            mark(key)

    with _LOCK:
        _STATE["last_cycle"] = ne.isoformat(timespec="seconds")
    return {"acted": acted, "kst": nk.isoformat(timespec="seconds"),
            "et": ne.isoformat(timespec="seconds")}


def _loop() -> None:
    with _LOCK:
        _STATE["running"] = True
    try:
        while True:
            try:
                _cycle()
            except Exception as exc:  # noqa: BLE001
                # A network blip, a provider outage, a bad line of JSON —
                # none of these may take the thread down. It records the
                # error, sleeps, and tries again on the next checkpoint.
                with _LOCK:
                    _STATE["error"] = str(exc)[:300]
            try:
                time.sleep(max(5.0, float(_cfg().get("poll_seconds", 30))))
            except Exception:         # pragma: no cover
                time.sleep(30.0)
    finally:                          # pragma: no cover
        with _LOCK:
            _STATE["running"] = False
            _THREAD["t"] = None


def start(background: bool = True) -> bool:
    """Start the capture thread. Idempotent, and it cannot start twice.

    The guard is the flag, set under the lock BEFORE the thread object is
    created — checking `is_alive()` instead would leave a window where two
    callers both find no thread and both start one, which on a server that
    wires modules from more than one place is not hypothetical.
    """
    with _LOCK:
        if _THREAD["started"]:
            return False
        _THREAD["started"] = True
    if not background:
        return True
    t = threading.Thread(target=_loop, name="korea-capture", daemon=True)
    with _LOCK:
        _THREAD["t"] = t
    t.start()
    return True


def reset_for_tests() -> None:
    """Clear the thread guard and the day's memo. Tests only."""
    with _LOCK:
        _THREAD["started"] = False
        _THREAD["t"] = None
        _DONE.clear()
        _STATE.update({"running": False, "last_cycle": None, "error": None,
                       "captured": 0, "missed": 0, "last_event": None})
