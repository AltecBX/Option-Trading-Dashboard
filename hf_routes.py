"""hf_routes.py — every Hedge Fund Intelligence route, in one place (v4.92).

Migration step 12. `options_dashboard.py` is 12,000 lines and its request
handler is a single long chain of `if parsed.path == ...`. The hedge layer had
grown to more than thirty of those branches inside it, so a change to a hedge
route meant editing the file that also serves the chart, the journal, the
scanner and the broker. That is a BLAST RADIUS problem, not a load one: the
monolith works, it is just the wrong place for a feature that now has eleven
modules of its own.

The split is deliberately shallow. This module answers ONE question — given a
section and a query string, what should the response body and status be — and
knows nothing about HTTP. The handler keeps the socket, the headers, the cache
directives and the error logging. That means the routes can be tested by
calling a function, which is what `test_hf_routes` does, and it means nothing
here can accidentally start writing to a response.

`push_configured_fn` is injected because whether a notification provider is
configured is the app's business, not the hedge layer's — and asking rather
than inferring is what fixed the panel that claimed push was set up when it
was not.
"""

from __future__ import annotations

import hf_alert
import hf_grade
import hf_health
import hf_names
import hf_obs
import hf_press
import hf_pulse
import hf_replay
import hf_report
import hf_scan
import hf_sources
import hf_watch

HF_ROUTES_VERSION = "1.0.0"

_PUSH_CONFIGURED_FN = None


def configure(push_configured_fn=None) -> None:
    global _PUSH_CONFIGURED_FN
    _PUSH_CONFIGURED_FN = push_configured_fn


def _push_configured() -> bool:
    if _PUSH_CONFIGURED_FN is None:
        return False
    try:
        return bool(_PUSH_CONFIGURED_FN())
    except Exception:  # noqa: BLE001
        return False


def _r(payload, status: int = 200):
    """One response: a body and a status. No headers, no socket, no caching
    policy — those stay with the handler that owns them."""
    return payload, status


def handle(section: str, qs: dict):
    """(body, status) for one hedge route.

    `section` is the path after /api/hf, with no leading slash; `qs` is the
    parsed query string. Raising is allowed and is the caller's to log — an
    exception here means a provider failed, and swallowing it into a 200
    would be the silent degradation this whole layer is built to refuse."""
    if section == "":
        return _r(hf_watch.snapshot())
    elif section == "fund":
        key = (qs.get("key", [""])[0] or "").strip()
        if not key:
            return _r({"error": "key required"}, status=400)
        out = hf_watch.fund(key)
        return _r(out, status=404 if not out.get("ok") else 200)
    elif section == "refresh":
        key = (qs.get("key", [""])[0] or "").strip()
        return _r(hf_watch.refresh_now([key] if key else None))
    elif section == "status":
        return _r(hf_watch.status())
    elif section == "watchlist":
        return _r({"registry": hf_watch.registry(),
                   "overlay": hf_watch._load_overlay()})  # noqa: SLF001
    elif section == "pulse":
        return _r(hf_scan.snapshot())
    elif section == "pulse/status":
        return _r(hf_scan.status())
    elif section == "pulse/refresh":
        return _r(hf_scan.refresh_now())
    elif section == "pulse/history":
        return _r({"weeks": hf_scan.history(
            int((qs.get("limit", ["60"])[0] or "60"))), "ok": True})
    elif section == "pulse/revisions":
        # Every build of one week, not just the one that survived.
        wk = (qs.get("week", [""])[0] or "").strip()
        if not wk:
            return _r({"ok": False, "error": "a week is required"}, status=400)
        rows = hf_scan.revisions(wk)
        return _r({"ok": True, "week": wk, "revisions": rows, "n": len(rows)})
    elif section == "pulse/reindex":
        # For a store written before the index existed.
        return _r(hf_scan.rebuild_index())
    elif section == "pulse/week":
        wk = (qs.get("week", [""])[0] or "").strip()
        out = hf_scan.snapshot_for(wk) if wk else None
        return _r(out or {"error": f"no reading stored for {wk!r}"},
                  status=200 if out else 404)
    elif section == "report":
        wk = (qs.get("week", [""])[0] or "").strip()
        rv = (qs.get("revision", [""])[0] or "").strip()
        out = hf_scan.report(week=wk or None,
                             revision=int(rv) if rv.isdigit() else None)
        return _r(out, status=200 if out.get("ok") else 404)
    elif section == "report/history":
        return _r({"weeks": hf_scan.report_history(
            int((qs.get("limit", ["60"])[0] or "60"))), "ok": True})
    elif section == "report/build":
        return _r(hf_scan.report_now())
    elif section == "report/status":
        return _r(hf_scan.report_status())
    elif section == "report/compare":
        a = (qs.get("a", [""])[0] or "").strip()
        b = (qs.get("b", [""])[0] or "").strip()
        if not a or not b:
            return _r({"ok": False, "error": "two weeks are required"}, status=400)
        out = hf_scan.report_compare(a, b)
        return _r(out, status=200 if out.get("ok") else 404)
    elif section == "grades":
        return _r(hf_scan.grades())
    elif section == "grades/status":
        return _r(hf_scan.grade_status())
    elif section == "grades/build":
        return _r(hf_scan.grades_now())
    elif section == "replay":
        return _r(hf_scan.replay())
    elif section == "replay/status":
        return _r(hf_scan.replay_status())
    elif section == "replay/build":
        return _r(hf_scan.replay_now())
    elif section == "names":
        return _r(hf_scan.names())
    elif section == "alerts":
        return _r(hf_scan.alerts())
    elif section == "alerts/check":
        # A way to make the alert layer LOOK, rather than waiting
        # for the pulse's twelve-hour clock.
        return _r(hf_scan.alerts_check())
    elif section == "health":
        # Source health as a QUERY over the observation log, not a
        # structure kept beside it.
        return _r(hf_scan.source_health())
    elif section == "daily":
        try:
            days = int((qs.get("days", ["14"])[0] or "14"))
        except ValueError:
            days = 14
        return _r(hf_scan.daily(max(2, min(days, 120))))
    elif section == "obs":
        return _r({"ok": True, **hf_obs.stats()})
    elif section == "cache":
        return _r(hf_sources.cache_stats())
    elif section == "cache/recompress":
        # Batched: hundreds of files, and an HTTP request should
        # not hold a connection open while they are rewritten.
        try:
            lim = int((qs.get("limit", ["200"])[0] or "200"))
        except ValueError:
            lim = 200
        return _r(hf_sources.recompress_cache(limit=max(1, min(lim, 1000))))
    elif section == "cache/prune":
        # Drops only cache files above the body ceiling — they
        # will never be written again and are re-fetchable. A
        # filing cached forever is under the ceiling and stays.
        return _r(hf_sources.prune_cache())
    elif section == "press":
        with hf_scan._LOCK:  # noqa: SLF001
            board = hf_scan._STATE["board"] or {}  # noqa: SLF001
        return _r({"ok": True, "available": bool(board.get("press")),
                   "week": board.get("week"), **(board.get("press") or {})})
    elif section == "config":
        return _r({"config": {**hf_watch.config(), **hf_scan.config()},
                   "version": hf_watch.HF_WATCH_VERSION,
                   "scan": hf_scan.HF_SCAN_VERSION,
                   "pulse": hf_pulse.HF_PULSE_VERSION,
                   "report": hf_report.HF_REPORT_VERSION,
                   "press": hf_press.HF_PRESS_VERSION,
                   "grade": hf_grade.HF_GRADE_VERSION,
                   "replay": hf_replay.HF_REPLAY_VERSION,
                   "names": hf_names.HF_NAMES_VERSION,
                   "alerts": hf_alert.HF_ALERT_VERSION,
                   "obs": hf_obs.HF_OBS_VERSION,
                   "health": hf_health.HF_HEALTH_VERSION,
                   "push": _push_configured(),
                   "x_statements": hf_sources.x_available(),
                   "sources": hf_sources.HF_SOURCES_VERSION,
                   "evidence_classes": list(hf_sources.EVIDENCE_CLASSES)})
    else:
        return _r({"error": f"unknown hf section {section}"}, status=404)
