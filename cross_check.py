"""cross_check.py — the company's own published figure beside the one this
app rebuilt from XBRL.

Most of what the Investment tab shows is RECONSTRUCTED: funds from
operations built out of net income, depreciation and gains on sale; a loss
ratio built out of claims and premiums. Reconstruction is the only way to
get a figure that is defined the same way for every company, and it is also
where a mistake would hide, because a wrong number that is internally
consistent looks exactly like a right one.

So where a company publishes the same measure in a table this app will read,
the two are put side by side. What comes of that is a STATE, never a
substitution:

  MATCH                  the two agree inside a rounding
  MINOR DIFFERENCE       they differ by less than the tolerance
  MATERIAL MISMATCH      they differ by more, and the reconstruction's
                         confidence is lowered for it
  INCOMPATIBLE BASIS     they are not the same measure, or not the same
                         period, or not the same window
  PUBLISHED UNAVAILABLE  the company does not print it in a readable table

The published figure never replaces the reconstruction and the
reconstruction never suppresses the published figure. Choosing the nicer of
two numbers is how a model stops being a measurement.

COMPARING LIKE WITH LIKE IS MOST OF THE WORK.

  * BASIS. A REIT publishes funds from operations five ways. Simon Property
    Group's FFO of the Operating Partnership is $1,184,945 thousand and its
    dilutive FFO allocable to common stockholders is $1,010,258 thousand,
    and the difference between them is not an error, it is the limited
    partners. Only the common-shareholder basis is compared.
  * PERIOD. A figure for the year to December is not a figure for the year
    to June. Where a company publishes an annual measure, the reconstruction
    is rebuilt AS OF THAT YEAR END and the two are compared there.
  * WINDOW. A quarter is not a year. A published quarter against a trailing
    twelve months is a 300% mismatch that is really a units-of-time error.
  * SCOPE. A combined ratio for Personal Insurance is not the company's
    combined ratio.

Anything that fails one of those tests is INCOMPATIBLE BASIS, which is a
finding rather than a failure: it says the check could not be run, and why.
"""

from __future__ import annotations

CROSS_CHECK_VERSION = "invest-cross-check-1.0.0"

MATCH = "MATCH"
MINOR = "MINOR DIFFERENCE"
MATERIAL = "MATERIAL MISMATCH"
INCOMPATIBLE = "INCOMPATIBLE BASIS"
UNAVAILABLE = "PUBLISHED UNAVAILABLE"
NOT_CHECKED = "NOT CHECKED"

STATE_LABEL = {
    MATCH: "The company's own published figure and the one rebuilt from its "
           "filings are the same number.",
    MINOR: "The two differ by less than the tolerance — a definition detail "
           "or a rounding, not a fault in either.",
    MATERIAL: "The two do not agree. Neither is preferred over the other; "
              "the disagreement is shown and the reconstruction is treated "
              "as less certain because of it.",
    INCOMPATIBLE: "There is a published figure, and it is not the same "
                  "measure, period or window as the reconstruction, so "
                  "comparing them would say nothing.",
    UNAVAILABLE: "This company does not print this measure in a table this "
                 "app will read, so there is nothing to check against.",
}

DEFAULTS = {
    # Inside this the two figures are the same number written twice.
    "cross_check_match_pct": 1.0,
    # Beyond this the difference is a finding rather than a rounding.
    "cross_check_material_pct": 5.0,
}


def cfg_get(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def compare(measure: str, published, reconstructed, unit: str = "",
            published_basis: str = "", reconstructed_basis: str = "",
            published_period: str | None = None,
            reconstructed_period: str | None = None,
            published_window: str = "", reconstructed_window: str = "",
            published_scope: str = "", provenance: dict | None = None,
            note: str = "", cfg=None) -> dict:
    """One published figure against one reconstructed figure."""
    out = {
        "measure": measure, "unit": unit,
        "published": _num(published), "reconstructed": _num(reconstructed),
        "difference": None, "difference_pct": None,
        "state": NOT_CHECKED, "reason": "", "note": note,
        "published_basis": published_basis,
        "reconstructed_basis": reconstructed_basis,
        "published_period": published_period,
        "reconstructed_period": reconstructed_period,
        "published_window": published_window,
        "reconstructed_window": reconstructed_window,
        "published_scope": published_scope,
        "provenance": provenance or {},
        "version": CROSS_CHECK_VERSION,
    }
    if out["published"] is None:
        out["state"] = UNAVAILABLE
        out["reason"] = STATE_LABEL[UNAVAILABLE]
        return out
    if out["reconstructed"] is None:
        out["state"] = INCOMPATIBLE
        out["reason"] = ("This app could not rebuild this measure from the "
                         "filings, so there is nothing to compare the "
                         "published figure with.")
        return out

    if published_scope and published_scope == "SEGMENT":
        out["state"] = INCOMPATIBLE
        out["reason"] = (
            "The published figure comes from a table the company heads as one "
            "segment rather than the whole business, so it is not the same "
            "measure as a company-wide reconstruction.")
        return out
    if published_period and reconstructed_period and \
            published_period != reconstructed_period:
        out["state"] = INCOMPATIBLE
        out["reason"] = (
            f"The published figure covers the period ending "
            f"{published_period} and the reconstruction covers the one "
            f"ending {reconstructed_period}. Two different periods are two "
            f"different numbers, so they are not compared.")
        return out
    if published_window and reconstructed_window and \
            published_window != reconstructed_window:
        out["state"] = INCOMPATIBLE
        out["reason"] = (
            f"The published figure covers {published_window.lower()} and the "
            f"reconstruction covers {reconstructed_window.lower()}. A "
            f"quarter compared with a year is a 300% disagreement about "
            f"nothing.")
        return out
    if published_window == "AMBIGUOUS" or reconstructed_window == "AMBIGUOUS":
        out["state"] = INCOMPATIBLE
        out["reason"] = (
            "The table the published figure sits in names two windows at "
            "once — a quarter and a year to date — so which one the figure "
            "covers is not stated, and it is not compared with anything.")
        return out
    if published_basis and reconstructed_basis and \
            published_basis != reconstructed_basis:
        out["state"] = INCOMPATIBLE
        out["reason"] = (
            f"The published figure is {published_basis.lower()} and the "
            f"reconstruction is {reconstructed_basis.lower()}. They are "
            f"different measures that share a name.")
        return out

    diff = out["published"] - out["reconstructed"]
    base = abs(out["published"]) or 1.0
    pct = abs(diff) / base * 100.0
    out["difference"], out["difference_pct"] = diff, pct
    match_at = float(cfg_get(cfg, "cross_check_match_pct"))
    material_at = float(cfg_get(cfg, "cross_check_material_pct"))
    out["state"] = (MATCH if pct <= match_at else
                    MINOR if pct <= material_at else MATERIAL)
    out["reason"] = STATE_LABEL[out["state"]]
    return out


# A material mismatch does not make the reconstruction wrong, and it does not
# make it right either. It makes it less certain, and this is by how much.
CONFIDENCE_PENALTY = {MATCH: 0, MINOR: 0, MATERIAL: 1,
                      INCOMPATIBLE: 0, UNAVAILABLE: 0, NOT_CHECKED: 0}


def report(checks: list, cfg=None) -> dict:
    """Every check that was run, and what they say taken together."""
    checks = [c for c in (checks or []) if c]
    ran = [c for c in checks if c["state"] in (MATCH, MINOR, MATERIAL)]
    mismatched = [c for c in checks if c["state"] == MATERIAL]
    incompatible = [c for c in checks if c["state"] == INCOMPATIBLE]
    if mismatched:
        state = MATERIAL
    elif ran:
        state = MINOR if any(c["state"] == MINOR for c in ran) else MATCH
    elif incompatible:
        state = INCOMPATIBLE
    elif checks:
        state = UNAVAILABLE
    else:
        state = NOT_CHECKED
    return {
        "checks": checks, "state": state,
        "checks_run": len(ran), "mismatches": len(mismatched),
        "incompatible": len(incompatible),
        "confidence_penalty": max(
            [CONFIDENCE_PENALTY.get(c["state"], 0) for c in checks] or [0]),
        "reason": _summary(state, ran, mismatched, incompatible, checks),
        "version": CROSS_CHECK_VERSION,
    }


def _summary(state, ran, mismatched, incompatible, checks) -> str:
    if state == NOT_CHECKED:
        return ("Nothing this company publishes in a readable table lines up "
                "with a measure this app rebuilds, so no cross-check applies "
                "to it.")
    if mismatched:
        names = ", ".join(sorted({c["measure"].lower() for c in mismatched}))
        return (f"The company's own published {names} and the figure rebuilt "
                f"from its filings do not agree. Both are shown. Neither is "
                f"treated as the answer, and the reconstruction is carried "
                f"with less confidence until the difference is understood.")
    if ran:
        names = ", ".join(sorted({c["measure"].lower() for c in ran}))
        return (f"The {names} rebuilt from this company's filings matches "
                f"what the company itself published, which is the only "
                f"outside check on the reconstruction there is.")
    if incompatible:
        return ("This company does publish these measures, and on a "
                "different basis, period or window from the reconstruction — "
                "so the two cannot be compared without inventing an "
                "agreement between them.")
    return ("No filing of this company prints one of these measures in a "
            "table this app will read.")


# ── the measures a filing table supplies, kept apart from one another ───────
#
# Client assets, assets under administration, advisory assets, assets under
# management, net new assets and net flows are six different things. A
# custodian's assets under administration include money it merely holds; an
# adviser's advisory assets are the part it is paid to advise on; assets
# under management are the part it actually runs. Adding them, or treating a
# missing one as satisfied by another, would overstate every custodian in
# the market — so each is stored under its own name and audited on its own
# terms.

def audit_measures(readings: dict, cfg=None) -> dict:
    """Each asset or flow measure with the period, scope and unit it was read
    on, so two of them are never quietly treated as one."""
    rows = []
    for name, r in sorted((readings or {}).items()):
        if not r or r.get("value") is None:
            continue
        prov = r.get("provenance") or {}
        rows.append({
            "metric": name, "label": r.get("label") or name,
            "value": r.get("value"), "kind": r.get("kind"),
            "period": prov.get("period"),
            "scope": prov.get("scope") or "UNSTATED",
            "unit": prov.get("resolved_unit") or "",
            "unit_source": prov.get("unit_source") or "",
            "confidence": r.get("confidence"),
            "form": r.get("form"), "filed": r.get("filed"),
            "row_label": prov.get("row_label") or "",
            "warning": r.get("warning") or "",
        })
    periods = sorted({r["period"] for r in rows if r["period"]})
    return {
        "rows": rows, "periods": periods,
        "mixed_periods": len(periods) > 1,
        "reason": (
            "Each of these is a different measure and they are kept apart. "
            "Assets under administration include money the firm only holds; "
            "advisory assets are the part it is paid to advise on; assets "
            "under management are the part it runs. None of them stands in "
            "for another, and none of them is added to another."
            if rows else
            "No filing of this company prints one of these measures in a "
            "table this app will read."),
        "version": CROSS_CHECK_VERSION,
    }
