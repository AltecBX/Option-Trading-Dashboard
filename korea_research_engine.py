"""korea_research_engine.py — the statistics behind KOREA LEAD V2.

korea_lead_engine.py answers "what happened after Korean sessions like this
one". This file answers the harder question the first one cannot: does
Korea actually tell us anything we did not already know, and does it still
do so on data the model has never seen?

Pure + stdlib, like every engine in this app. No I/O, no clock, no
persistence, no network. korea_research.py owns the data.

WHAT IS IN HERE AND WHY

  Regression with ROBUST standard errors. Daily market returns are
  heteroskedastic — quiet months and violent months in one sample — and
  ordinary standard errors under-report the uncertainty in exactly the
  periods that matter most. Every t-statistic here is HC1.

  A real Student's t distribution, not a normal approximation. The samples
  here are large enough that it rarely changes an answer, but "rarely" is
  not a reason to report a p-value that is quietly wrong.

  Incremental R². The number that matters is not how well Korea explains
  the U.S. open, it is how much Korea adds once the previous U.S. session
  is already in the model. A variable that only repeats what is already
  known has a large correlation and no value.

  Walk-forward evaluation, never a random split. This is time-series data:
  a random split trains on the future and scores the past, and every model
  looks brilliant when it does. Folds here are expanding windows, and a
  model is only scored on days that were strictly after every day it was
  fitted on.

  Benjamini-Hochberg. Test forty pairs and two will look significant by
  luck. The FDR-adjusted q-value is what decides whether a cell in the
  sensitivity matrix means anything.

  Empirical residual bands, not sigma. Gap residuals are fat-tailed, so
  "±1 standard deviation is 68%" is false in the direction that matters.
  The bands here are quantiles of the residuals that actually occurred.

WHAT IS DELIBERATELY NOT IN HERE

  No composite score with hand-chosen weights. Candidate models are
  compared out of sample and the comparison is reported; nothing is
  promoted to a production signal by this file.
"""

from __future__ import annotations

import math

ENGINE_VERSION = "korea-research-1.0.0"

# Below this many observations a regression is refused rather than fitted.
# Not a statistical law — a floor chosen so a slope is never reported from
# a sample where one quarter of unusual weather would dominate it.
MIN_REGRESSION_N = 60

# Folds are scored only after the model has seen at least this much history.
MIN_TRAIN_N = 250          # roughly one trading year
WALK_STEP = 21             # re-fit about monthly


# ── small numerics ──────────────────────────────────────────────────────────

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f


def invert(m):
    """Gauss-Jordan inverse with partial pivoting. None when singular —
    a singular design usually means two columns carrying the same
    information, and returning None is how that surfaces as a refusal
    instead of as an enormous meaningless coefficient."""
    n = len(m)
    a = [list(row) + [1.0 if i == j else 0.0 for j in range(n)]
         for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            return None
        a[c], a[p] = a[p], a[c]
        pv = a[c][c]
        a[c] = [v / pv for v in a[c]]
        for r in range(n):
            if r == c:
                continue
            f = a[r][c]
            if f:
                a[r] = [v - f * w for v, w in zip(a[r], a[c])]
    return [row[n:] for row in a]


def _betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta, by the Lentz continued fraction."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _betainc(b, a, 1.0 - x)
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log1p(-x) * b - lbeta) / a
    f, c, d = 1.0, 1.0, 0.0
    for i in range(300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        if abs(d) < 1e-30:
            d = 1e-30
        d = 1.0 / d
        c = 1.0 + num / c
        if abs(c) < 1e-30:
            c = 1e-30
        f *= c * d
        if abs(1.0 - c * d) < 1e-12:
            break
    return front * (f - 1.0)


def t_two_sided_p(t, df) -> float | None:
    """Two-sided p-value from Student's t. The real distribution rather
    than the normal approximation: at these sample sizes the two usually
    agree, and "usually" is not a standard to report a p-value against."""
    tv, dfv = _num(t), _num(df)
    if tv is None or dfv is None or dfv <= 0:
        return None
    return max(0.0, min(1.0, _betainc(dfv / 2.0, 0.5, dfv / (dfv + tv * tv))))


def median(vals) -> float | None:
    xs = sorted(v for v in (_num(v) for v in (vals or [])) if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def quantile(vals, q: float) -> float | None:
    xs = sorted(v for v in (_num(v) for v in (vals or [])) if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    i = (len(xs) - 1) * max(0.0, min(1.0, q))
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def mad(vals) -> float | None:
    """Median absolute deviation — the spread measure that a single
    extraordinary morning cannot move."""
    med = median(vals)
    if med is None:
        return None
    return median([abs(v - med) for v in vals if _num(v) is not None])


def robust_z(value, vals) -> float | None:
    """How unusual a value is against its own history, in MAD units.
    Scaled by 1.4826 so that for normal data it reads like a z-score, and
    refused when the history has no spread at all rather than dividing by
    something indistinguishable from zero."""
    v, med, m = _num(value), median(vals), mad(vals)
    if v is None or med is None or m is None or m <= 1e-12:
        return None
    return (v - med) / (1.4826 * m)


def percentile_of(value, vals) -> float | None:
    """Share of history at or below `value`, as a percentage."""
    v = _num(value)
    xs = [x for x in (_num(x) for x in (vals or [])) if x is not None]
    if v is None or not xs:
        return None
    return sum(1 for x in xs if x <= v) / len(xs) * 100.0


def pearson(xs, ys) -> float | None:
    pairs = [(a, b) for a, b in zip(xs, ys)
             if _num(a) is not None and _num(b) is not None]
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sx = math.sqrt(sum((p[0] - mx) ** 2 for p in pairs))
    sy = math.sqrt(sum((p[1] - my) ** 2 for p in pairs))
    if sx <= 0 or sy <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pairs) / (sx * sy)


def correlation_p(r, n) -> float | None:
    """p-value for a correlation being zero, through its t-statistic."""
    rv, nv = _num(r), _num(n)
    if rv is None or nv is None or nv < 4 or abs(rv) >= 1.0:
        return None
    t = rv * math.sqrt((nv - 2) / (1.0 - rv * rv))
    return t_two_sided_p(t, nv - 2)


# ── regression ──────────────────────────────────────────────────────────────

def ols(y, X, names=None, min_n: int = MIN_REGRESSION_N) -> dict | None:
    """Least squares with HC1 heteroskedasticity-robust standard errors.

    `X` is a list of rows WITHOUT an intercept column; one is added. Daily
    return data is emphatically not homoskedastic — a sample spanning both
    a quiet year and a crash has wildly different residual variance across
    it — and classical standard errors would understate the uncertainty
    precisely in the periods a trader cares about. The sandwich estimator
    does not assume constant variance; the HC1 factor n/(n−p) is the small
    sample correction.
    """
    ys = [_num(v) for v in (y or [])]
    rows = [[_num(v) for v in row] for row in (X or [])]
    keep = [i for i in range(len(ys))
            if ys[i] is not None and all(v is not None for v in rows[i])]
    if len(keep) < min_n:
        return None
    ys = [ys[i] for i in keep]
    rows = [rows[i] for i in keep]
    n = len(ys)
    k = len(rows[0])
    p = k + 1
    if n <= p + 2:
        return None
    Z = [[1.0] + list(r) for r in rows]
    XtX = [[sum(Z[i][a] * Z[i][b] for i in range(n)) for b in range(p)]
           for a in range(p)]
    inv = invert(XtX)
    if inv is None:
        return None
    Xty = [sum(Z[i][a] * ys[i] for i in range(n)) for a in range(p)]
    beta = [sum(inv[a][b] * Xty[b] for b in range(p)) for a in range(p)]
    fitted = [sum(beta[a] * Z[i][a] for a in range(p)) for i in range(n)]
    resid = [ys[i] - fitted[i] for i in range(n)]
    ybar = sum(ys) / n
    sst = sum((v - ybar) ** 2 for v in ys)
    ssr = sum(v * v for v in resid)
    mid = [[sum(resid[i] ** 2 * Z[i][a] * Z[i][b] for i in range(n))
            for b in range(p)] for a in range(p)]
    V = [[sum(sum(inv[a][u] * mid[u][v] for u in range(p)) * inv[v][b]
              for v in range(p)) for b in range(p)] for a in range(p)]
    scale = n / float(n - p)
    df = n - p
    labels = ["intercept"] + list(names or [f"x{i + 1}" for i in range(k)])
    params = []
    for a in range(p):
        se = math.sqrt(max(0.0, V[a][a] * scale))
        t = (beta[a] / se) if se > 0 else None
        params.append({
            "name": labels[a], "beta": beta[a], "se": se, "t": t,
            "p": t_two_sided_p(t, df) if t is not None else None,
            "ci_lo": beta[a] - 1.96 * se, "ci_hi": beta[a] + 1.96 * se,
        })
    return {"n": n, "df": df, "params": params, "beta": beta,
            "r2": (1.0 - ssr / sst) if sst > 0 else None,
            "resid": resid, "fitted": fitted,
            "se_method": "HC1 heteroskedasticity-robust"}


def predict(model: dict, row) -> float | None:
    """Apply a fitted model to one new row of features."""
    if not model:
        return None
    vals = [_num(v) for v in row]
    if any(v is None for v in vals):
        return None
    b = model["beta"]
    if len(b) != len(vals) + 1:
        return None
    return b[0] + sum(b[i + 1] * vals[i] for i in range(len(vals)))


def incremental(y, base_X, full_X, names=None,
                min_n: int = MIN_REGRESSION_N) -> dict:
    """How much the extra columns add once the baseline is already there.

    This is the question that separates a signal from an echo. A variable
    that merely restates what the baseline already knows has a large
    correlation with the outcome and an incremental R² of nothing.
    """
    base = ols(y, base_X, min_n=min_n)
    full = ols(y, full_X, names=names, min_n=min_n)
    out = {"ok": bool(base and full), "base": None, "full": None,
           "r2_base": None, "r2_full": None, "delta_r2": None,
           "added": [], "reason": None}
    if not base or not full:
        out["reason"] = (f"Needs at least {min_n} matched sessions "
                         f"with every column present.")
        return out
    if base["n"] != full["n"]:
        # Comparing R² across two different samples is meaningless.
        out["ok"] = False
        out["reason"] = ("The two models did not fit the same rows, so their "
                         "R² values are not comparable.")
        return out
    out.update({
        "base": {"n": base["n"], "r2": base["r2"]},
        "full": {"n": full["n"], "r2": full["r2"],
                 "params": full["params"], "se_method": full["se_method"]},
        "r2_base": base["r2"], "r2_full": full["r2"],
        "delta_r2": (full["r2"] - base["r2"])
        if (full["r2"] is not None and base["r2"] is not None) else None,
        "added": [p for p in full["params"] if p["name"] != "intercept"],
    })
    return out


# ── multiple testing ────────────────────────────────────────────────────────

def benjamini_hochberg(pvalues) -> list:
    """Benjamini-Hochberg FDR adjustment, returning q-values aligned with
    the input (None where the p-value was missing).

    Forty pairs tested at the five percent level produce two significant
    results from noise alone. The q-value is the share of discoveries at
    that threshold expected to be false, and it is what decides whether a
    cell in the sensitivity matrix is worth believing.
    """
    idx = [i for i, p in enumerate(pvalues) if _num(p) is not None]
    m = len(idx)
    out = [None] * len(pvalues)
    if not m:
        return out
    order = sorted(idx, key=lambda i: pvalues[i])
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        q = min(1.0, float(pvalues[i]) * m / rank)
        running = min(running, q)
        out[i] = running
    return out


# ── rolling and grouped views ───────────────────────────────────────────────

def rolling_correlation(rows, x_key: str, y_key: str, window: int) -> list:
    """A correlation per session over a trailing window, so a relationship
    that is fading can be seen fading rather than averaged into five years
    of history that no longer describes it."""
    out = []
    if not rows or window < 10:
        return out
    for i in range(window - 1, len(rows)):
        chunk = rows[i - window + 1:i + 1]
        xs = [r.get(x_key) for r in chunk]
        ys = [r.get(y_key) for r in chunk]
        r = pearson(xs, ys)
        if r is not None:
            out.append({"date": chunk[-1].get("date"), "r": round(r, 4),
                        "n": len(chunk)})
    return out


def by_year(rows, x_key: str, y_key: str) -> list:
    """The same relationship split by calendar year, which is how a signal
    that only worked during one semiconductor cycle gives itself away."""
    groups: dict = {}
    for r in rows or []:
        d = str(r.get("date") or "")[:4]
        if len(d) == 4:
            groups.setdefault(d, []).append(r)
    out = []
    for year in sorted(groups):
        g = groups[year]
        xs = [r.get(x_key) for r in g]
        ys = [r.get(y_key) for r in g]
        pairs = [(a, b) for a, b in zip(xs, ys)
                 if _num(a) is not None and _num(b) is not None]
        if not pairs:
            continue
        directional = [p for p in pairs if p[0] != 0 and p[1] != 0]
        same = sum(1 for a, b in directional if (a > 0) == (b > 0))
        out.append({
            "year": year, "n": len(pairs),
            "pearson": (lambda v: None if v is None else round(v, 3))(
                pearson([p[0] for p in pairs], [p[1] for p in pairs])),
            "same_direction_pct": (round(same / len(directional) * 100.0, 1)
                                   if directional else None),
            "avg_y_pct": round(sum(p[1] for p in pairs) / len(pairs), 3),
        })
    return out


def split_by_regime(rows, regime_key: str, x_key: str, y_key: str,
                    cut=None, min_n: int = MIN_REGRESSION_N) -> dict:
    """The relationship in calm markets against the relationship in violent
    ones, split at the MEDIAN of the regime variable rather than at a round
    number somebody liked the look of.

    The regime variable must be knowable BEFORE the session it labels —
    trailing realised volatility computed through the prior close, never a
    same-day close. Nothing in this function can enforce that; it is the
    caller's job, and korea_research.py builds it that way.
    """
    vals = [r.get(regime_key) for r in rows or []]
    usable = [v for v in (_num(v) for v in vals) if v is not None]
    # A median split makes two groups, and each of them has to stand on its
    # own — so the requirement is twice the single-sample floor, not once.
    if len(usable) < 2 * min_n:
        return {"ok": False, "n": len(usable),
                "reason": (f"needs at least {2 * min_n} sessions "
                           f"carrying the regime variable so that both halves "
                           f"of the split stand on their own; have "
                           f"{len(usable)}")}
    cut = _num(cut) if cut is not None else median(usable)
    out = {"ok": True, "cut": cut, "regime_key": regime_key, "groups": []}
    for label, pick in (("calm", lambda v: v <= cut), ("volatile", lambda v: v > cut)):
        g = [r for r in rows
             if _num(r.get(regime_key)) is not None and pick(_num(r.get(regime_key)))]
        xs = [r.get(x_key) for r in g]
        ys = [r.get(y_key) for r in g]
        pairs = [(a, b) for a, b in zip(xs, ys)
                 if _num(a) is not None and _num(b) is not None]
        directional = [p for p in pairs if p[0] != 0 and p[1] != 0]
        same = sum(1 for a, b in directional if (a > 0) == (b > 0))
        r = pearson([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else None
        out["groups"].append({
            "label": label, "n": len(pairs),
            "pearson": None if r is None else round(r, 3),
            "same_direction_pct": (round(same / len(directional) * 100.0, 1)
                                   if directional else None),
            "median_y_pct": (lambda v: None if v is None else round(v, 3))(
                median([p[1] for p in pairs])),
        })
    return out


# ── placebo ─────────────────────────────────────────────────────────────────

def placebo_table(rows, x_key: str, y_key: str, shuffles: int = 200,
                  seed: int = 20260820,
                  min_n: int = MIN_REGRESSION_N) -> dict:
    """The correct alignment against deliberately wrong ones.

    If Korean session D really does inform the U.S. open on D, then pairing
    it with the day before or the day after must do measurably worse, and
    pairing it with a random permutation of its own dates must do nothing
    at all. This is the strongest available guard against the failure mode
    that would otherwise be invisible: an off-by-one in the alignment that
    still produces a plausible-looking number.

    The shuffle uses a fixed seed and a stdlib linear congruential
    generator, so the same rows always give the same answer and the test
    can be asserted against.
    """
    xs = [r.get(x_key) for r in rows or []]
    ys = [r.get(y_key) for r in rows or []]
    n = len(xs)
    out = {"ok": False, "correct": None, "placebos": [], "shuffled": None,
           "n": n, "verdict": None, "reason": None}
    if n < min_n:
        out["reason"] = "not enough matched sessions for a placebo test"
        return out
    correct = pearson(xs, ys)
    if correct is None:
        out["reason"] = "the correct alignment produced no correlation"
        return out
    out["ok"] = True
    out["correct"] = round(correct, 4)
    for shift in (-1, 1):
        a, b = ([], [])
        for i in range(n):
            j = i + shift
            if 0 <= j < n:
                a.append(xs[j])
                b.append(ys[i])
        r = pearson(a, b)
        out["placebos"].append({
            "shift": shift, "n": len(a),
            "label": (f"Korea one session {'earlier' if shift < 0 else 'later'} "
                      f"against the same U.S. open"),
            "pearson": None if r is None else round(r, 4)})
    # deterministic shuffle
    state = seed & 0x7FFFFFFF
    mags = []
    for _ in range(max(1, shuffles)):
        perm = list(xs)
        for i in range(len(perm) - 1, 0, -1):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            j = state % (i + 1)
            perm[i], perm[j] = perm[j], perm[i]
        r = pearson(perm, ys)
        if r is not None:
            mags.append(abs(r))
    if mags:
        mags.sort()
        beat = sum(1 for m in mags if m >= abs(correct))
        out["shuffled"] = {
            "draws": len(mags),
            "median_abs": round(median(mags), 4),
            "p95_abs": round(quantile(mags, 0.95), 4),
            "max_abs": round(mags[-1], 4),
            "share_beating_correct_pct": round(beat / len(mags) * 100.0, 2),
        }
    worst_placebo = max(
        (abs(p["pearson"]) for p in out["placebos"] if p["pearson"] is not None),
        default=0.0)
    shuffled_max = (out["shuffled"] or {}).get("max_abs", 0.0)
    if abs(correct) > worst_placebo and abs(correct) > shuffled_max:
        out["verdict"] = "PASSED"
    elif abs(correct) > shuffled_max:
        out["verdict"] = "WEAK — a shifted alignment does nearly as well"
    else:
        out["verdict"] = "FAILED — random dates do as well as the real ones"
    return out


# ── the Korea surprise, computed without hindsight ──────────────────────────

def expanding_residual(rows, y_key: str, x_key: str,
                       min_train: int = MIN_TRAIN_N, out_key: str = "surprise") -> int:
    """Write a point-in-time residual column onto `rows`, in place.

    KOREA SURPRISE is what Korea did minus what the previous U.S.
    semiconductor session said it would do. The obvious way to build it —
    fit the echo model on the whole sample, take the residuals — is also
    the wrong way, because every residual would then be informed by
    sessions that had not happened yet, and the surprise would look far
    better than it is.

    So each row's residual comes from a model fitted ONLY on the rows
    before it. Rows before `min_train` get None rather than a value from a
    model with nothing to learn from.

    Single regressor by design, updated with running sums so the expanding
    re-fit is exact rather than approximated for speed. Returns how many
    residuals were written.
    """
    n_written = 0
    sx = sy = sxx = sxy = 0.0
    count = 0
    for r in rows or []:
        x, y = _num(r.get(x_key)), _num(r.get(y_key))
        if count >= min_train and x is not None:
            denom = sxx - sx * sx / count
            if abs(denom) > 1e-12:
                beta = (sxy - sx * sy / count) / denom
                alpha = sy / count - beta * sx / count
                if y is not None:
                    r[out_key] = y - (alpha + beta * x)
                    n_written += 1
                else:
                    r[out_key] = None
            else:
                r[out_key] = None
        else:
            r[out_key] = None
        if x is not None and y is not None:
            sx += x
            sy += y
            sxx += x * x
            sxy += x * y
            count += 1
    return n_written


def expanding_bucket_residual(rows, y_key: str, x_key: str, bucket_fn,
                              min_train: int = MIN_TRAIN_N,
                              min_bucket: int = 8,
                              out_key: str = "bucket_residual") -> int:
    """Point-in-time residuals from the BUCKET estimator, in place.

    The panel's headline residual is the actual gap minus the MEDIAN of the
    matched bucket. Ranking that against a history of residuals produced by
    a fitted LINE would compare two different estimators — the line and the
    bucket median disagree by a meaningful amount on any given day, so the
    percentile would be measuring the difference between the two methods as
    much as anything about today.

    So this builds the history with the same estimator the headline uses:
    for each session, the median outcome of the earlier sessions that fell
    in the SAME bucket, and the residual against it. Earlier sessions only —
    a bucket median that included today would flatter every residual.

    Each bucket keeps its own sorted list, so the median is a lookup rather
    than a re-sort, and the whole pass is O(n log n).
    """
    import bisect
    seen: dict = {}
    total = 0
    written = 0
    for r in rows or []:
        x, y = _num(r.get(x_key)), _num(r.get(y_key))
        key = bucket_fn(x) if x is not None else None
        r[out_key] = None
        if key is not None and total >= min_train:
            prior = seen.get(key) or []
            if len(prior) >= min_bucket and y is not None:
                k = len(prior)
                med = (prior[k // 2] if k % 2
                       else (prior[k // 2 - 1] + prior[k // 2]) / 2.0)
                r[out_key] = y - med
                written += 1
        if key is not None and y is not None:
            bisect.insort(seen.setdefault(key, []), y)
            total += 1
    return written


# ── walk-forward evaluation ─────────────────────────────────────────────────

def walk_forward(rows, models: dict, y_key: str,
                 min_train: int = MIN_TRAIN_N, step: int = WALK_STEP) -> dict:
    """Expanding-window out-of-sample evaluation.

    Every prediction here is made by a model fitted ONLY on sessions
    strictly earlier than the session being predicted. There is no random
    split anywhere in this file, because a random split on time-series data
    trains on the future and scores the past, and everything looks
    brilliant when it does.

    `models` maps a name to a list of feature keys; an empty list is the
    train-mean baseline, and the name "zero" is the predict-nothing
    baseline. A direction probability comes from the EMPIRICAL training
    residuals rather than from an assumed normal distribution: given a
    predicted gap, the probability of an up open is the share of training
    residuals large enough to carry it above zero. That keeps the Brier
    score honest about fat tails instead of quietly assuming they are not
    there.
    """
    # ONE evaluation set for every candidate. A model whose input is
    # missing on some session would otherwise skip that session while the
    # baseline still scored it, and the comparison would then be between
    # different sets of days — which lets a richer model win by quietly
    # omitting the hard ones. Taiwan holidays are the concrete case here:
    # they cluster around Lunar New Year, so the sessions dropped are not a
    # random sample of anything.
    every_feature = sorted({f for feats in (models or {}).values()
                            for f in (feats or [])})
    dated = [r for r in (rows or []) if _num(r.get(y_key)) is not None]
    usable = [r for r in dated
              if all(_num(r.get(f)) is not None for f in every_feature)]
    dropped: dict = {}
    for r in dated:
        for f in every_feature:
            if _num(r.get(f)) is None:
                dropped[f] = dropped.get(f, 0) + 1
    out = {"ok": False, "y_key": y_key, "n": len(usable), "folds": 0,
           "min_train": min_train, "step": step, "models": {}, "reason": None,
           "shared_evaluation_set": True,
           "rows_with_outcome": len(dated),
           "rows_dropped_for_missing_inputs": len(dated) - len(usable),
           "dropped_by_input": dropped,
           "shared_set_note": (
               "Every model is scored on exactly these sessions. Sessions "
               "where ANY candidate's input was missing are excluded from ALL "
               "of them, so no model can win by skipping the days it could "
               "not answer.")}
    if len(usable) < min_train + step:
        out["reason"] = (f"Needs more than {min_train + step} matched sessions "
                         f"where every candidate's inputs are present, to "
                         f"score even one out-of-sample fold; have "
                         f"{len(usable)}.")
        return out
    preds: dict = {name: [] for name in models}
    folds = 0
    start = min_train
    while start < len(usable):
        train = usable[:start]
        test = usable[start:start + step]
        if not test:
            break
        folds += 1
        for name, feats in models.items():
            if name == "zero":
                for r in test:
                    preds[name].append((r[y_key], 0.0, 0.5))
                continue
            ytr = [r.get(y_key) for r in train]
            if not feats:
                m = median([v for v in ytr if _num(v) is not None])
                resid = [v - m for v in ytr if _num(v) is not None]
                for r in test:
                    preds[name].append((r[y_key], m, _emp_up(m, resid)))
                continue
            Xtr = [[r.get(f) for f in feats] for r in train]
            fit = ols(ytr, Xtr, names=feats)
            if not fit:
                continue
            for r in test:
                p = predict(fit, [r.get(f) for f in feats])
                if p is not None:
                    preds[name].append((r[y_key], p, _emp_up(p, fit["resid"])))
        start += step
    out["folds"] = folds
    for name, rows_ in preds.items():
        out["models"][name] = _score(rows_, models.get(name))
    scored = {v["n"] for v in out["models"].values() if v.get("n")}
    # If this ever fires, two models were compared over different days and
    # the ranking beneath it means nothing — so it is stated in the payload
    # rather than left for a reader to notice.
    out["all_models_scored_same_rows"] = (len(scored) <= 1)
    if not out["all_models_scored_same_rows"]:
        out["reason"] = ("Models were scored over different numbers of "
                         "sessions, so their errors are not comparable: "
                         + ", ".join(f"{k}={v['n']}" for k, v in
                                     sorted(out["models"].items())
                                     if v.get("n")))
    out["ok"] = bool(scored) and out["all_models_scored_same_rows"]
    return out


def _emp_up(pred, resid) -> float:
    """P(the gap is positive), read off the empirical residual distribution
    instead of assumed from a normal. Gap residuals are fat-tailed, and a
    normal assumption is wrong in exactly the direction that flatters a
    forecast."""
    xs = [v for v in (_num(v) for v in (resid or [])) if v is not None]
    p = _num(pred)
    if p is None or not xs:
        return 0.5
    return sum(1 for v in xs if p + v > 0) / len(xs)


def _score(rows, features) -> dict:
    """Out-of-sample scores for one model's accumulated predictions."""
    pairs = [(a, b, c) for a, b, c in rows
             if _num(a) is not None and _num(b) is not None]
    n = len(pairs)
    if not n:
        return {"n": 0, "features": features}
    directional = [(a, b, c) for a, b, c in pairs if a != 0 and b != 0]
    hit = sum(1 for a, b, _ in directional if (a > 0) == (b > 0))
    errs = [abs(a - b) for a, b, _ in pairs]
    brier = sum((c - (1.0 if a > 0 else 0.0)) ** 2 for a, _, c in pairs) / n
    r = pearson([b for _, b, _ in pairs], [a for a, _, _ in pairs])
    return {
        "n": n, "features": features,
        "direction_pct": (round(hit / len(directional) * 100.0, 1)
                          if directional else None),
        "direction_n": len(directional),
        "mae_pct": round(sum(errs) / n, 4),
        "median_ae_pct": round(median(errs), 4),
        "brier": round(brier, 4),
        "pred_actual_corr": None if r is None else round(r, 3),
    }


def compare_models(walk: dict, baseline: str = "kospi") -> dict:
    """Rank the walk-forward results and say plainly whether anything beat
    the simple baseline out of sample.

    Being better in-sample is not a finding. A model earns promotion here
    only by predicting days it was never fitted on more accurately than the
    single-variable model already does.
    """
    models = (walk or {}).get("models") or {}
    base = models.get(baseline)
    out = {"baseline": baseline, "ok": bool(base and base.get("n")),
           "rows": [], "winner": None, "beats_baseline": [], "reason": None}
    if not out["ok"]:
        out["reason"] = f"the {baseline} baseline produced no out-of-sample rows"
        return out
    if walk.get("all_models_scored_same_rows") is False:
        out["ok"] = False
        out["reason"] = (walk.get("reason") or "models were scored over "
                         "different sessions, so they cannot be ranked")
        return out
    for name, m in models.items():
        if not m.get("n"):
            continue
        row = dict(m)
        row["model"] = name
        for key in ("mae_pct", "median_ae_pct", "brier"):
            if m.get(key) is not None and base.get(key) is not None:
                row[f"{key}_vs_baseline"] = round(m[key] - base[key], 4)
        if m.get("direction_pct") is not None and base.get("direction_pct") is not None:
            row["direction_pct_vs_baseline"] = round(
                m["direction_pct"] - base["direction_pct"], 1)
        out["rows"].append(row)
    out["rows"].sort(key=lambda r: (r.get("mae_pct") if r.get("mae_pct")
                                    is not None else 9e9))
    # "Beats" means better on BOTH the size of the error and the direction,
    # because a model that wins on one and loses on the other has not been
    # shown to be better at anything.
    for r in out["rows"]:
        if r["model"] == baseline:
            continue
        better_mae = (r.get("mae_pct") is not None
                      and base.get("mae_pct") is not None
                      and r["mae_pct"] < base["mae_pct"])
        better_dir = (r.get("direction_pct") is not None
                      and base.get("direction_pct") is not None
                      and r["direction_pct"] > base["direction_pct"])
        if better_mae and better_dir:
            out["beats_baseline"].append(r["model"])
    out["winner"] = out["rows"][0]["model"] if out["rows"] else None
    return out


# ── the gap estimate, from a regression rather than a bucket ────────────────

def regression_estimate(rows, x_key: str, y_key: str, x_today,
                        min_n: int = MIN_REGRESSION_N) -> dict:
    """A second, independent estimate of today's opening gap.

    The bucket estimate asks what happened on the sessions that looked like
    this one. This asks what a straight line fitted through every session
    says. They can disagree, and when they do the honest output is that
    they disagree rather than an average of the two that sounds more
    precise than either.

    The bands are quantiles of the residuals that actually occurred, not
    multiples of a standard deviation. Gap residuals have fat tails, so a
    sigma-based band would be too narrow exactly when it mattered.
    """
    out = {"ok": False, "expected_pct": None, "band50": None, "band80": None,
           "n": 0, "slope": None, "slope_t": None, "r2": None, "reason": None}
    v = _num(x_today)
    ys = [r.get(y_key) for r in rows or []]
    Xs = [[r.get(x_key)] for r in rows or []]
    fit = ols(ys, Xs, names=[x_key], min_n=min_n)
    if not fit:
        out["reason"] = (f"Needs at least {min_n} matched sessions "
                         f"to fit a line.")
        return out
    if v is None:
        out["reason"] = "There is no Korean move to put into the line today."
        return out
    exp = predict(fit, [v])
    res = fit["resid"]
    slope = next((p for p in fit["params"] if p["name"] == x_key), None)
    out.update({
        "ok": exp is not None, "n": fit["n"],
        "expected_pct": None if exp is None else round(exp, 3),
        "r2": None if fit["r2"] is None else round(fit["r2"], 4),
        "slope": None if not slope else round(slope["beta"], 4),
        "slope_t": None if not slope or slope["t"] is None else round(slope["t"], 2),
        "band50": [round(exp + quantile(res, 0.25), 3),
                   round(exp + quantile(res, 0.75), 3)] if exp is not None else None,
        "band80": [round(exp + quantile(res, 0.10), 3),
                   round(exp + quantile(res, 0.90), 3)] if exp is not None else None,
        "band_basis": ("empirical residual quantiles — the errors this line "
                       "actually made, not a multiple of a standard deviation"),
    })
    return out


AGREE = "AGREE"
DISAGREE = "MODEL DISAGREEMENT"


def compare_estimates(bucket_pct, regression_pct, tolerance_pct: float = 0.75) -> dict:
    """Do the two independent estimates of today's gap say the same thing?

    They are not averaged. An average of two estimates that disagree is a
    third number nothing supports, stated with more confidence than either
    of the two it came from.
    """
    a, b = _num(bucket_pct), _num(regression_pct)
    out = {"state": None, "bucket_pct": a, "regression_pct": b,
           "difference_pct": None, "detail": None}
    if a is None or b is None:
        out["state"] = "ONE ESTIMATE ONLY"
        out["detail"] = ("Only one of the two estimates could be produced, so "
                         "there is nothing to cross-check it against.")
        return out
    out["difference_pct"] = round(b - a, 3)
    if (a > 0) != (b > 0) or abs(b - a) > tolerance_pct:
        out["state"] = DISAGREE
        out["detail"] = (f"The matched sessions say {a:+.2f}% and the fitted "
                         f"line says {b:+.2f}%. They are not averaged — an "
                         f"average of two estimates that disagree is a third "
                         f"number nothing supports.")
    else:
        out["state"] = AGREE
        out["detail"] = (f"The matched sessions say {a:+.2f}% and the fitted "
                         f"line says {b:+.2f}% — close enough to read as one "
                         f"answer.")
    return out


# ── today's residual, against its own history ───────────────────────────────

RESIDUAL_LABELS = ("IN LINE", "UNDERREACTION", "OVERREACTION", "DIVERGENCE")


def residual_context(today_residual, history, expected_pct=None,
                     actual_pct=None, estimator: str = "the same estimator",
                     min_n: int = MIN_REGRESSION_N) -> dict:
    """Where today's premarket-versus-implied gap sits in the distribution
    of every residual this pair has produced before.

    The label comes from the empirical percentile, not from a percentage
    threshold somebody chose. "Two points below the implied gap" means
    something completely different for MU than for SPY, and something
    different again in a calm month than in a violent one; a percentile
    against this pair's own history carries all of that automatically.
    """
    out = {"ok": False, "residual_pct": _num(today_residual), "percentile": None,
           "robust_z": None, "n": 0, "label": None, "detail": None,
           # Today's residual and the history it is ranked against must come
           # from ONE estimator, or the percentile measures the difference
           # between two methods rather than anything about today.
           "estimator": estimator}
    xs = [v for v in (_num(v) for v in (history or [])) if v is not None]
    r = _num(today_residual)
    out["n"] = len(xs)
    if r is None or len(xs) < min_n:
        out["detail"] = (f"Needs at least {min_n} past residuals to "
                         f"say whether today's is unusual; have {len(xs)}.")
        return out
    out["ok"] = True
    out["percentile"] = round(percentile_of(r, xs), 1)
    z = robust_z(r, xs)
    out["robust_z"] = None if z is None else round(z, 2)
    exp, act = _num(expected_pct), _num(actual_pct)
    if exp is not None and act is not None and exp != 0 and act != 0 \
            and (exp > 0) != (act > 0):
        out["label"] = "DIVERGENCE"
        out["detail"] = ("The premarket is moving the opposite way to the "
                         "matched sessions, not merely by a different amount.")
        return out
    pct = out["percentile"]
    if pct >= 90.0 or pct <= 10.0:
        # Which extreme is an over- or under-reaction depends on the SIGN of
        # what was expected: a residual above the implied gap is an
        # overreaction when the expectation was positive and an
        # underreaction when it was negative.
        stronger = (r > 0) == ((exp or 0) >= 0)
        out["label"] = "OVERREACTION" if stronger else "UNDERREACTION"
        out["detail"] = (f"Today's residual sits at the {pct:.0f}th percentile "
                         f"of the {len(xs)} this pair has produced. It is an "
                         f"unusually large gap between what the matched "
                         f"sessions did and what the premarket is doing — "
                         f"which is an observation, not a prediction that it "
                         f"closes.")
    else:
        out["label"] = "IN LINE"
        out["detail"] = (f"Today's residual is at the {pct:.0f}th percentile of "
                         f"this pair's own history — an ordinary distance "
                         f"between the implied gap and the premarket.")
    return out


# ── does the residual predict anything after the open? ──────────────────────

def convergence_test(rows, residual_key: str, outcome_key: str,
                     extreme_pct: float = 20.0,
                     min_n: int = MIN_REGRESSION_N) -> dict:
    """Do premarket residuals predict the move AFTER the open?

    The hopeful story is that a stock which has not yet moved as far as
    Korea implies will make up the difference after 9:30. This function
    exists to find out, and is written so that a null result is a result:
    if the correlation is nothing, it says so in the verdict rather than
    being quietly dropped from a report.

    A positive coefficient means the residual CONVERGES — the U.S. session
    moves back toward the implied gap. Negative means it keeps going the
    other way.
    """
    pairs = [(_num(r.get(residual_key)), _num(r.get(outcome_key)))
             for r in rows or []]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    out = {"ok": False, "n": len(pairs), "pearson": None, "p": None,
           "slope": None, "slope_t": None, "verdict": None, "extremes": None,
           "outcome": outcome_key, "reason": None}
    if len(pairs) < min_n:
        out["reason"] = (f"Needs at least {min_n} sessions carrying "
                         f"both a residual and {outcome_key}; have {len(pairs)}.")
        return out
    # Convergence means the outcome runs OPPOSITE to the residual: if the
    # premarket undershot the implied gap, closing the difference means
    # moving further in the implied direction after the open.
    xs = [-a for a, _ in pairs]
    ys = [b for _, b in pairs]
    r = pearson(xs, ys)
    fit = ols(ys, [[x] for x in xs], names=["negative_residual"], min_n=min_n)
    slope = next((p for p in (fit or {}).get("params", [])
                  if p["name"] == "negative_residual"), None)
    out.update({
        "ok": True, "pearson": None if r is None else round(r, 3),
        "p": (lambda v: None if v is None else round(v, 5))(
            correlation_p(r, len(pairs))),
        "slope": None if not slope else round(slope["beta"], 4),
        "slope_t": None if not slope or slope["t"] is None else round(slope["t"], 2),
    })
    lo = quantile([a for a, _ in pairs], extreme_pct / 100.0)
    hi = quantile([a for a, _ in pairs], 1.0 - extreme_pct / 100.0)
    groups = []
    for label, pick in (("premarket below implied", lambda a: a <= lo),
                        ("premarket above implied", lambda a: a >= hi)):
        g = [b for a, b in pairs if pick(a)]
        groups.append({"group": label, "n": len(g),
                       "median_outcome_pct": (lambda v: None if v is None
                                              else round(v, 3))(median(g)),
                       "share_positive_pct": (round(sum(1 for v in g if v > 0)
                                                    / len(g) * 100.0, 1)
                                              if g else None)})
    out["extremes"] = groups
    t = out["slope_t"]
    if t is None:
        out["verdict"] = "NOT MEASURABLE"
    elif t >= 2.0:
        out["verdict"] = "CONVERGES"
    elif t <= -2.0:
        out["verdict"] = "DIVERGES FURTHER"
    else:
        out["verdict"] = "NO MEASURABLE EDGE"
    return out


# ── relationship health ─────────────────────────────────────────────────────

HEALTH_STRONG = "STRONG"
HEALTH_STABLE = "STABLE"
HEALTH_WEAK = "WEAK"
HEALTH_UNSTABLE = "UNSTABLE"
HEALTH_INSUFFICIENT = "INSUFFICIENT DATA"


def relationship_health(recent_r, long_r, recent_n, long_n,
                        min_recent: int = 40, min_long: int = 150,
                        strong: float = 0.30, weak: float = 0.12) -> dict:
    """Is this relationship worth trusting right now?

    Deliberately not a score out of a hundred. The label is a summary of
    four numbers that are all shown beside it, so the reader can disagree
    with the summary by looking at what produced it.

    The case that matters most is disagreement in SIGN between the recent
    and the long window. A five-year average that quietly contains a
    relationship which has since inverted is worse than no number at all,
    so that case is called UNSTABLE and nothing else.
    """
    rr, lr = _num(recent_r), _num(long_r)
    rn, ln = _num(recent_n) or 0, _num(long_n) or 0
    out = {"state": HEALTH_INSUFFICIENT, "recent_r": rr, "long_r": lr,
           "recent_n": int(rn), "long_n": int(ln), "same_sign": None,
           "detail": None}
    if rr is None or lr is None or rn < min_recent or ln < min_long:
        out["detail"] = (f"Needs at least {min_recent} recent and {min_long} "
                         f"long-window sessions before the relationship can be "
                         f"described; have {int(rn)} and {int(ln)}.")
        return out
    same = (rr > 0) == (lr > 0)
    out["same_sign"] = same
    if not same:
        out["state"] = HEALTH_UNSTABLE
        out["detail"] = (f"The recent window reads {rr:+.2f} and the long "
                         f"window {lr:+.2f} — they disagree about which way "
                         f"this relationship even runs, so neither should be "
                         f"traded on.")
        return out
    weaker = min(abs(rr), abs(lr))
    if weaker >= strong:
        out["state"] = HEALTH_STRONG
    elif weaker >= weak:
        out["state"] = HEALTH_STABLE
    else:
        out["state"] = HEALTH_WEAK
    out["detail"] = (f"Recent {rr:+.2f} over {int(rn)} sessions, long window "
                     f"{lr:+.2f} over {int(ln)}; both point the same way.")
    return out
