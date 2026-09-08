"""hf_obs.py — the raw observation log (v4.91).

WHAT THIS IS FOR

Everything the hedge fund board stores today is DERIVED. `hf/pulse/{week}.json`
is a finished board — verdicts, confidence, crowding states — and the numbers
that produced it are thrown away the moment it is written. That has a cost the
architecture audit measured:

  * a change to how confidence is computed cannot be applied to history,
    because there is nothing left to recompute it FROM;
  * `hf_replay` exists only to work around that, and it can only reach back
    where a provider happens to keep an archive of its own;
  * `gather_shvol_history` re-downloads FINRA daily files the board already
    read and discarded, and caches one float per session in a side file.

So: one line per observation, appended, never edited. Monthly files, because a
month keeps any single file small and a JSONL append needs no rewrite. At
market-wide and sector granularity that is roughly 15,000 lines and 4 MB a
year.

THE RULES THIS LAYER KEEPS

  * APPEND ONLY. Nothing here ever rewrites or trims a line. A derived
    document may be rebuilt at will; a raw observation is what was seen, and
    what was seen does not change.
  * TWO DATES, always. `as_of` is what the number describes; `public_on` is
    when it could first be read. They are not the same date and the difference
    is usually the whole story.
  * ATTRIBUTION. An anonymous evidence class can never carry a fund's name.
    `observation()` raises rather than writes such a line — the same refusal
    `hf_sources.evidence` enforces, applied at the point of record.
  * MISSING STAYS MISSING. There is no zero-fill and no interpolation. A
    provider that had nothing writes a HEALTH line saying so, which is a
    different fact from a reading of zero.
  * SCHEMA STAMPED. Every line carries `schema`, and `readable()` REFUSES a
    schema it does not know rather than guessing at the shape.

WHAT READS IT: nothing yet, deliberately. This ships as a writer so the record
starts accumulating from today; the daily pulse and the source-health query
that read it are the next steps, and they are only worth building on a log that
already has depth.

HEDGE_FUND_INTEL.md §5g.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import hf_sources as S

HF_OBS_VERSION = "1.0.0"

# The line format. Bump ONLY for a change a reader cannot absorb; `readable()`
# refuses anything it does not know, which is the point of the stamp.
SCHEMA = 1

# How a provider was doing when the line was written. OK is a reading; every
# other value is a fact about the SOURCE rather than about the market, which
# is exactly the distinction that was missing before — a provider quietly
# returning nothing looked identical to a quiet market.
OK = "OK"
STALE = "STALE"                 # answered, but with an older period than expected
PARTIAL = "PARTIAL"             # answered with less than a full reading
RATE_LIMITED = "RATE_LIMITED"
AUTH_MISSING = "AUTH_MISSING"   # no key configured for a paid channel
SHAPE_CHANGED = "SHAPE_CHANGED"  # answered in a shape the parser did not expect
UNAVAILABLE = "UNAVAILABLE"     # did not answer at all
QUALITIES = (OK, STALE, PARTIAL, RATE_LIMITED, AUTH_MISSING, SHAPE_CHANGED,
             UNAVAILABLE)

HEALTH = "health"               # the metric name a source-health line carries


# ── the record, pure ────────────────────────────────────────────────────────

def observation(source: str, evidence_class: str, metric: str, value,
                *, observed_at: str, as_of: str | None = None,
                public_on: str | None = None, market: str | None = None,
                sector: str | None = None, symbol: str | None = None,
                prev=None, units: str | None = None, quality: str = OK,
                engine: str | None = None, fund: str | None = None,
                note: str | None = None) -> dict:
    """One observation, validated. Raises rather than returning a line that
    breaks a rule — a bad line in an append-only log cannot be taken back."""
    if evidence_class not in S.EVIDENCE_CLASSES:
        raise ValueError(f"unknown evidence class {evidence_class!r}")
    if fund is not None and evidence_class != S.VERIFIED:
        raise ValueError(f"{evidence_class} cannot be attributed to a fund ({fund})")
    if quality not in QUALITIES:
        raise ValueError(f"unknown quality {quality!r}")
    if not source or not metric:
        raise ValueError("an observation needs a source and a metric")
    if not observed_at:
        raise ValueError("an observation needs the moment it was observed")
    rec = {
        "schema": SCHEMA, "observed_at": observed_at,
        "as_of": as_of, "public_on": public_on,
        "source": source, "class": evidence_class,
        "market": market, "sector": sector, "symbol": symbol,
        "metric": metric, "value": value, "prev": prev,
        "change": (value - prev) if _both_numbers(value, prev) else None,
        "units": units, "quality": quality,
        "engine": engine or f"hf_obs {HF_OBS_VERSION}",
    }
    if fund is not None:
        rec["fund"] = fund
    if note:
        rec["note"] = note
    return rec


def health(source: str, quality: str, *, observed_at: str,
           evidence_class: str = S.INFERENCE, as_of: str | None = None,
           engine: str | None = None, note: str | None = None,
           n: int | None = None) -> dict:
    """One record per provider per attempt, in the same log.

    Health as a QUERY over the record rather than a parallel structure to keep
    in sync: a structure would have to be updated in the same places the
    readings are written, and the failure mode of that is a health panel that
    is confidently out of date."""
    return observation(source, evidence_class, HEALTH, n, observed_at=observed_at,
                       as_of=as_of, quality=quality, engine=engine, note=note,
                       units="records" if n is not None else None)


def _both_numbers(a, b) -> bool:
    return (isinstance(a, (int, float)) and not isinstance(a, bool)
            and isinstance(b, (int, float)) and not isinstance(b, bool))


def month_of(observed_at: str) -> str:
    """The file a line belongs in: the calendar month it was observed."""
    m = str(observed_at or "")[:7]
    if len(m) != 7 or m[4] != "-":
        raise ValueError(f"cannot place {observed_at!r} in a month")
    return m


def dumps(rec: dict) -> str:
    """One line. Newlines inside a value would split the record in two, and
    `json.dumps` escapes them — this is why the format is JSON rather than
    anything hand-rolled."""
    return json.dumps(rec, separators=(",", ":"), sort_keys=True)


def readable(rec: dict) -> bool:
    """Whether this reader understands the line. A line stamped with a schema
    from the future is SKIPPED, loudly countable, never guessed at."""
    return isinstance(rec, dict) and rec.get("schema") == SCHEMA


# ── the same idea, applied to the DERIVED documents ─────────────────────────
#
# Seven kinds of document are persisted by the hedge layer, and before this
# they carried between one and zero version stamps each. `hf_report.normalize`
# exists because an older stored shape had to be reinterpreted, and it detects
# the version by the PRESENCE OF A FIELD rather than by a stamp — its own
# comment says so. That is the class of drift this closes.
#
# A stamp answers three questions a reader has no other way to ask: what kind
# of document is this, which shape is it in, and which engine wrote it.

DOC_SCHEMAS = {
    "pulse": 1,        # hf/pulse/{week}.json      — one weekly board
    "report": 1,       # hf/reports/{week}.json    — every revision of a week
    "grades": 1,       # hf/grades.json            — the grading card
    "replay": 1,       # hf/replay.json            — reconstructed weeks
    "alerts": 1,       # hf/alerts.json            — what was sent, and what was seen
    "shvol": 1,        # hf/shvol_daily.json       — cached daily short shares
    "fund": 1,         # hf/funds/{key}.json       — one manager's record
}


def stamp(doc: dict, kind: str, *, engine: str, created_at: str) -> dict:
    """Return `doc` with its identity stamped on. Raises on an unknown kind —
    a document nobody declared is a document no reader can check."""
    if kind not in DOC_SCHEMAS:
        raise ValueError(f"unknown document kind {kind!r}")
    return {**doc, "doc": kind, "doc_schema": DOC_SCHEMAS[kind],
            "doc_engine": engine, "created_at": created_at}


def accept(doc, kind: str) -> bool:
    """Whether a reader may use this document.

    An UNSTAMPED document is accepted. Every file written before this shipped
    is unstamped and perfectly readable, and refusing it would throw away
    history to enforce a rule about the future — the opposite of the point.

    A document stamped with a schema this reader does not know is REFUSED.
    That is the case where guessing has actually gone wrong: the reader would
    apply today's meaning to yesterday's field and be confidently wrong,
    which is worse than reporting that it cannot read the file."""
    if not isinstance(doc, dict):
        return False
    got = doc.get("doc_schema")
    if got is None:
        return True
    return got == DOC_SCHEMAS.get(kind)


def why_refused(doc, kind: str) -> str | None:
    """A sentence for a reader, or None when the document is fine."""
    if accept(doc, kind):
        return None
    if not isinstance(doc, dict):
        return f"the stored {kind} is not a document"
    return (f"the stored {kind} is schema {doc.get('doc_schema')}; this build "
            f"reads schema {DOC_SCHEMAS.get(kind)}")


# ── the store ───────────────────────────────────────────────────────────────

_DATA_DIR: Path | None = None
_NOW_FN = None
_LOCK = threading.RLock()


def configure(data_dir=None, now_fn=None) -> None:
    """Both values are ASSIGNED, never merged — passing None clears.

    `hf_scan.configure` works the same way and calls this with its own
    arguments, so the two can never disagree about where the record lives. A
    "keep what was there" configure would let a second test inherit the first
    one's directory and append to a log it does not own."""
    global _DATA_DIR, _NOW_FN
    _DATA_DIR = Path(data_dir) if data_dir else None
    _NOW_FN = now_fn


def now() -> str:
    d = _NOW_FN() if _NOW_FN else datetime.now(timezone.utc)
    return d.isoformat(timespec="seconds")


def _dir() -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "hf" / "obs"


def path_for(month: str) -> Path | None:
    d = _dir()
    return None if d is None else d / f"{month}.jsonl"


def append(records) -> int:
    """Append every record, grouped into its month's file. Returns how many
    lines were written.

    Opened in append mode and flushed per batch: a single writer appending
    whole lines under the pipe-buffer size cannot interleave, and a crash
    mid-write costs the tail of one batch rather than the file. Nothing here
    reads the file first, so the cost does not grow with its length."""
    recs = [r for r in (records or []) if r]
    if not recs:
        return 0
    bad = [r for r in recs if not S.attribution_ok([{**r, "class": r.get("class")}])]
    if bad:
        raise ValueError("refusing to log an anonymous observation that names a fund")
    d = _dir()
    if d is None:
        return 0
    by_month: dict[str, list[dict]] = {}
    for r in recs:
        by_month.setdefault(month_of(r.get("observed_at")), []).append(r)
    written = 0
    with _LOCK:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            return 0
        for month, rows in by_month.items():
            p = path_for(month)
            if p is None:
                continue
            try:
                with open(p, "a", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(dumps(r) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                written += len(rows)
            except Exception:  # noqa: BLE001
                continue          # the log is a record, never a gate on a build
    return written


def months() -> list[str]:
    d = _dir()
    if d is None or not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl"))


def read(*, since: str | None = None, until: str | None = None,
         source: str | None = None, metric: str | None = None,
         limit: int | None = None) -> list[dict]:
    """Lines, oldest first, filtered. `since`/`until` are compared against
    `observed_at` as strings, which is why the stamp is ISO — an ISO instant
    sorts the same way as the moment it names.

    Only the months a window can touch are opened. A line whose schema this
    reader does not know is skipped rather than reinterpreted."""
    out: list[dict] = []
    lo, hi = (since or "")[:7], (until or "")[:7]
    for month in months():
        if lo and month < lo:
            continue
        if hi and month > hi:
            continue
        p = path_for(month)
        if p is None:
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if not readable(rec):
                        continue
                    at = rec.get("observed_at") or ""
                    if since and at < since:
                        continue
                    if until and at > until:
                        continue
                    if source and rec.get("source") != source:
                        continue
                    if metric and rec.get("metric") != metric:
                        continue
                    out.append(rec)
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda r: (r.get("observed_at") or "", r.get("source") or "",
                            r.get("metric") or ""))
    return out[-int(limit):] if limit else out


def stats() -> dict:
    """What the log holds, without loading it. Line counts are read; values
    are not parsed, so this stays cheap as the record grows."""
    d = _dir()
    if d is None:
        return {"available": False, "why": "no data directory is configured"}
    if not d.exists():
        return {"available": True, "n_files": 0, "n_lines": 0, "bytes": 0,
                "first_month": None, "last_month": None,
                "note": "nothing has been observed yet"}
    n_lines, size, files = 0, 0, months()
    unreadable = 0
    for month in files:
        p = path_for(month)
        try:
            size += p.stat().st_size
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    n_lines += 1
                    if '"schema":%d' % SCHEMA not in line.replace(", ", ","):
                        unreadable += 1
        except Exception:  # noqa: BLE001
            continue
    return {"available": True, "n_files": len(files), "n_lines": n_lines,
            "bytes": size, "first_month": files[0] if files else None,
            "last_month": files[-1] if files else None,
            "n_unreadable": unreadable, "schema": SCHEMA,
            "version": HF_OBS_VERSION}
