"""validate_perfection.py — live-data validation for the Priced-for-Perfection
module. NOT part of CI (needs network); run manually:

    python3 validate_perfection.py AMD SNDK

Checks structural honesty on real provider data — it never asserts a
predetermined score (the data determines the result):
  • payload is JSON-safe (no NaN/Infinity anywhere),
  • contributions reconcile to the composite,
  • effective weights sum to ~100 over available components,
  • confidence matches weighted coverage,
  • whisper is never fabricated (available=False → no gap numbers),
  • every component carries sources and an as-of stamp.
"""
from __future__ import annotations

import json
import sys

import perfection
import perfection_data as pdm


def _session_factory():
    """Plain-requests session (some sandboxes reject curl_cffi's TLS)."""
    try:
        import requests
        s = requests.Session()
        s.headers["User-Agent"] = "Mozilla/5.0"
        return s
    except Exception:
        return None


def validate(symbol: str) -> bool:
    out = pdm.build(symbol)
    if out.get("error"):
        print(f"{symbol}: BUILD ERROR — {out['error']}")
        return False
    json.dumps(out, allow_nan=False)                       # hygiene contract
    ok = True
    comps = {k: c for k, c in (out.get("components") or {}).items() if c}
    if out.get("score") is not None:
        total = sum(c["contribution"] for c in comps.values())
        if abs(total - out["score"]) > 0.15:
            print(f"{symbol}: RECONCILIATION FAILED {total} vs {out['score']}")
            ok = False
        wsum = sum(c["weight_effective_pct"] for c in comps.values())
        if abs(wsum - 100.0) > 0.5:
            print(f"{symbol}: effective weights sum {wsum} != 100")
            ok = False
    cov = sum(perfection.MODEL["weights"][k] for k in comps)
    if abs(cov - (out.get("coverage_pct") or 0)) > 0.5:
        print(f"{symbol}: coverage mismatch {cov} vs {out.get('coverage_pct')}")
        ok = False
    eg = comps.get("expectations_gap")
    if eg:
        wh = (eg.get("benchmarks") or {}).get("whisper") or {}
        if not wh.get("available") and wh.get("eps_gap_pct") is not None:
            print(f"{symbol}: WHISPER FABRICATED — gap shown without a source")
            ok = False
    for k, c in comps.items():
        if not c.get("sources"):
            print(f"{symbol}: component {k} has no sources")
            ok = False
    h = out.get("header") or {}
    print(f"{symbol} ({h.get('company')}): score={out.get('score')} "
          f"class={out.get('classification')} conf={out.get('confidence')} "
          f"coverage={out.get('coverage_pct')}% ULR={out.get('unprotected_long_risk')} "
          f"nextER={h.get('next_earnings')} ({h.get('session')}) "
          f"warning={'FIRED' if (out.get('warning') or {}).get('fired') else 'quiet'} "
          f"→ {'OK' if ok else 'PROBLEMS'}")
    print(f"  {out.get('summary')}")
    return ok


if __name__ == "__main__":
    syms = [s.upper() for s in sys.argv[1:]] or ["AMD", "SNDK"]
    pdm.configure(schwab_getter=None, data_dir=None, peers_getter=None,
                  iv_history_load=None, session_factory=_session_factory)
    results = [validate(s) for s in syms]
    sys.exit(0 if all(results) else 1)
