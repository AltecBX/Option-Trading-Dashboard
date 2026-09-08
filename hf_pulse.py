"""hf_pulse.py — HEDGE FUND PULSE: what the universe appears to be doing.

The aggregate half of the feature. It answers the weekly questions —
exposure, leverage, longs, shorts, covering, sectors, crowding, what
changed, how long it has persisted, how unusual it is — from anonymous
sources only, and it never names a fund. That is not a convention here; the
evidence rows it is handed cannot carry a fund name, because
`hf_sources.evidence()` refuses to build one.

Four ideas do the work:

  * **The change is the signal, not the level.** Leveraged funds are
    structurally net short equity index futures — they hedge long stock
    books and run basis trades. A net short of 300,000 contracts is not
    bearishness, it is Tuesday. So every verdict reads the weekly CHANGE,
    and the level appears only as a percentile of its own history.

  * **A verdict is worth what its inputs are worth.** Confidence is the
    count of corroborating evidence CLASSES that agree — not a probability,
    and not a number that can be tuned. A prime-broker quote can raise
    confidence in something the data already says; it can never create a
    verdict alone. An inference is never evidence for another inference.

  * **Disagreement is a finding.** When inputs point opposite ways the
    verdict is MIXED and both sides are listed. Averaging them would
    manufacture a consensus that does not exist.

  * **Missing is not zero.** A sector with no futures contract, a report
    not yet published, a series that failed to load — each is named, and
    the verdict says how much of its evidence is absent.

Pure: no network, no disk, no clock. Everything is computed from the
evidence rows and the prior week's snapshot that the caller passes in.
HEDGE_FUND_INTEL.md §5c.
"""

from __future__ import annotations

import hf_sources as S

HF_PULSE_VERSION = "1.0.0"

# Verdict vocabulary. Plain words, because the card shows them as written.
ADDING, REDUCING, FLAT, MIXED, NO_DATA = "ADDING", "REDUCING", "LITTLE CHANGE", "MIXED", "NO DATA"
RISING, FALLING = "RISING", "FALLING"
ADDING_SHORTS, COVERING = "ADDING SHORTS", "COVERING"
CROWDED, DECROWDING, NORMAL = "CROWDED", "DE-CROWDING", "NORMAL"

PERSISTENCE_WINDOWS = (2, 4, 8, 12)
CROWDED_PCTILE = 90        # at or above this percentile on an input, that input is crowded
CROWDED_INPUTS = 2         # lean AND size — two different facts, never one counted twice
FLAT_BAND = 0.10           # a weekly change smaller than this share of the recent typical move is noise


# ── primitives ──────────────────────────────────────────────────────────────

def percentile(series, value) -> int | None:
    """Where `value` sits in its own history, 0-100. The only honest way to
    say "unusual" — a number is extreme relative to itself, never in the
    abstract.

    A series with no spread has no percentile. Counting ties would put a
    perfectly flat history at the 100th percentile and flag every quiet
    market as crowded, which is the opposite of what the number means."""
    xs = [x for x in (series or []) if x is not None]
    if not xs or value is None:
        return None
    if max(xs) == min(xs):
        return None
    return int(round(100.0 * sum(1 for x in xs if x <= value) / len(xs)))


def weekly_changes(series: list[dict], field: str) -> list[float]:
    """Week-over-week changes from a newest-first series, newest first.

    A series of n weeks gives n−1 changes. Rows with a missing value break
    the chain rather than being interpolated across — a gap in the record is
    a gap, and pretending otherwise would invent a week."""
    out = []
    for i in range(len(series) - 1):
        a, b = series[i].get(field), series[i + 1].get(field)
        if a is None or b is None:
            break
        out.append(float(a) - float(b))
    return out


def direction(value, band: float = 0.0) -> int:
    """+1, −1 or 0. `band` is the width of "no real change"."""
    if value is None:
        return 0
    if abs(float(value)) <= band:
        return 0
    return 1 if float(value) > 0 else -1


def typical(values, lookback: int = 12) -> float | None:
    """The typical size of a recent move, as the median absolute value. Used
    to decide when a change is too small to call a direction."""
    xs = sorted(abs(float(v)) for v in (values or [])[:lookback] if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def streak(changes: list[float], band: float = 0.0) -> dict:
    """How many consecutive weeks have moved the same way, newest first.

    A week inside the noise band ends the streak without starting one the
    other way — "four straight weeks of selling" should mean four weeks of
    selling, not three plus a week that rounded down."""
    if not changes:
        return {"weeks": 0, "direction": 0, "word": "no data"}
    d0 = direction(changes[0], band)
    if d0 == 0:
        return {"weeks": 0, "direction": 0, "word": "no clear direction this week"}
    n = 1
    for c in changes[1:]:
        if direction(c, band) != d0:
            break
        n += 1
    word = f"{n} consecutive week{'s' if n != 1 else ''} of {'adding' if d0 > 0 else 'reducing'}"
    return {"weeks": n, "direction": d0, "word": word}


def persistence(changes: list[float], band: float = 0.0,
                windows: tuple = PERSISTENCE_WINDOWS) -> dict:
    """"n of the last m weeks" for each window, in the direction of the most
    recent move. Answers Jerry's "what has persisted for 2, 4, 8 and 12
    weeks?" without claiming a trend a single week created."""
    out = {}
    if not changes:
        return {str(w): None for w in windows}
    d0 = direction(changes[0], band) or 1
    for w in windows:
        seg = changes[:w]
        if len(seg) < w:
            out[str(w)] = None       # not enough history yet; say so rather than pad
            continue
        same = sum(1 for c in seg if direction(c, band) == d0)
        out[str(w)] = {"same": same, "of": w,
                       "text": f"{same} of the last {w} weeks",
                       "direction": d0}
    return out


# ── inputs and confidence ───────────────────────────────────────────────────

def make_input(key: str, label: str, cls: str, value, change, *, as_of=None,
               public_on=None, pct=None, n=None, note=None, band: float = 0.0,
               weight: float = 1.0) -> dict:
    """One piece of evidence, reduced to a direction plus its provenance.

    `weight` is not a multiplier on a score — there is no score. It marks an
    input as SUPPORTING (0.5) rather than deciding (1.0): a prime-broker
    quote and a daily flow proxy can corroborate a verdict the regulatory
    data already carries, and cannot create one on their own."""
    return {"key": key, "label": label, "class": cls, "value": value, "change": change,
            "direction": direction(change, band), "as_of": as_of, "public_on": public_on,
            "percentile": pct, "n": n, "note": note, "weight": weight}


def _deciding(inputs):
    return [i for i in inputs if i.get("weight", 1.0) >= 1.0 and i.get("direction")]


def confidence(inputs: list[dict]) -> dict:
    """How much a verdict is worth: the number of CORROBORATING evidence
    classes that agree, never a probability.

    A single source, however official, is one source. Two classes agreeing
    is the first point at which the answer is not an artefact of one
    provider. Supporting inputs are counted for corroboration but can never
    lift a verdict that no deciding input carries.

    The word is CORROBORATING and not "independent", and the change is
    deliberate. Independence is a statistical claim this code does not
    establish: ETF creations, the short-volume share, the CFTC futures
    position and a Goldman note can all be four views of the SAME
    liquidation. What the count actually measures is how many separate KINDS
    of evidence point the same way, which is a real and useful thing and is
    not independence. The maths is unchanged — only the claim it makes."""
    deciding = _deciding(inputs)
    if not deciding:
        return {"level": "NONE", "classes": 0, "agree": 0, "disagree": 0,
                "why": "No deciding input had a direction this week."}
    counts: dict[int, set] = {}
    for i in deciding:
        counts.setdefault(i["direction"], set()).add(i["class"])
    majority = max(counts, key=lambda d: (len(counts[d]), sum(1 for i in deciding if i["direction"] == d)))
    agree_classes = counts[majority]
    supporting = {i["class"] for i in inputs
                  if i.get("weight", 1.0) < 1.0 and i.get("direction") == majority}
    disagree = sum(1 for i in deciding if i["direction"] != majority)
    n_classes = len(agree_classes | supporting)
    if disagree and len(agree_classes) <= 1:
        level = "LOW"
    elif n_classes >= 3 and not disagree:
        level = "HIGH"
    elif n_classes >= 2:
        level = "MODERATE"
    else:
        level = "LOW"
    names = ", ".join(sorted(agree_classes | supporting))
    why = (f"{n_classes} corroborating evidence class{'es' if n_classes != 1 else ''} agree ({names})"
           + (f"; {disagree} input{'s' if disagree != 1 else ''} disagree" if disagree else ""))
    return {"level": level, "classes": n_classes, "agree": len(deciding) - disagree,
            "disagree": disagree, "direction": majority, "why": why}


def conflicts(inputs: list[dict]) -> list[dict]:
    """Inputs pointing the opposite way to the majority, kept as findings.
    Jerry asked for "conflicting signals between data sources" by name."""
    c = confidence(inputs)
    if not c.get("direction"):
        return []
    return [{"label": i["label"], "class": i["class"], "direction": i["direction"],
             "change": i["change"], "as_of": i.get("as_of")}
            for i in inputs if i.get("direction") and i["direction"] != c["direction"]]


def _verdict_word(d: int, vocab: tuple) -> str:
    up, down, flat = vocab
    return up if d > 0 else down if d < 0 else flat


def build_verdict(question: str, inputs: list[dict], *, vocab=(ADDING, REDUCING, FLAT),
                  streak_from: list[float] | None = None, band: float = 0.0,
                  unusual: dict | None = None, note: str | None = None) -> dict:
    """One answered question, with everything needed to check the answer."""
    live = [i for i in inputs if i.get("change") is not None or i.get("value") is not None]
    if not live:
        return {"question": question, "verdict": NO_DATA, "inputs": [], "missing": [i["label"] for i in inputs],
                "confidence": {"level": "NONE", "classes": 0, "why": "No source answered."},
                "note": note}
    conf = confidence(inputs)
    cf = conflicts(inputs)
    if conf["classes"] == 0:
        word = FLAT
    elif cf and conf["level"] == "LOW" and conf["disagree"] >= conf["agree"]:
        word = MIXED
    else:
        word = _verdict_word(conf.get("direction") or 0, vocab)
    out = {"question": question, "verdict": word, "confidence": conf,
           "inputs": inputs, "conflicts": cf,
           "missing": [i["label"] for i in inputs if i.get("change") is None and i.get("value") is None],
           "note": note}
    if streak_from is not None:
        out["streak"] = streak(streak_from, band)
        out["persistence"] = persistence(streak_from, band)
    if unusual is not None:
        out["unusual"] = unusual
    return out


# ── the weekly questions ────────────────────────────────────────────────────

def _cftc_input(m: dict, field: str, key_suffix: str, label: str, weight: float = 1.0) -> dict | None:
    """One CFTC market reduced to an input on one of its fields."""
    series = (m or {}).get("series") or []
    if len(series) < 2:
        return None
    ch = weekly_changes(series, field)
    if not ch:
        return None
    band = (typical(ch) or 0.0) * FLAT_BAND
    hist = [r.get(field) for r in series]
    return make_input(f"cftc_{m['key']}_{key_suffix}", label, S.REGULATORY,
                      series[0].get(field), ch[0], as_of=series[0]["date"],
                      public_on=None, pct=percentile(hist, series[0].get(field)),
                      n=len(series), band=band, weight=weight,
                      note=f"{len(series)} weeks of history")


def exposure(cftc: dict, etf_flows: dict | None) -> dict:
    """Are hedge funds increasing or reducing exposure?

    The broad index contracts carry this: S&P 500, Nasdaq 100, Russell 2000
    and the Dow, by weekly change in net position. ETF creations corroborate
    but do not decide — they are everyone's money, not hedge funds' alone."""
    inputs, streak_src, band = [], None, 0.0
    for key, label in (("sp500", "S&P 500 futures, net position"),
                       ("nasdaq", "Nasdaq 100 futures, net position"),
                       ("russell", "Russell 2000 futures, net position"),
                       ("djia", "Dow futures, net position")):
        m = cftc.get(key)
        i = _cftc_input(m, "lev_net", "net", label) if m else None
        if i:
            inputs.append(i)
            if key == "sp500":
                ch = weekly_changes(m["series"], "lev_net")
                streak_src, band = ch, (typical(ch) or 0.0) * FLAT_BAND
    if etf_flows:
        net = etf_flows.get("net_all")
        inputs.append(make_input("etf_net", "Sector and index ETF creations", S.FLOW_PROXY,
                                 net, net, as_of=etf_flows.get("as_of"), weight=0.5,
                                 note="Creations minus redemptions across the eleven sector ETFs and SPY"))
    sp = cftc.get("sp500") or {}
    hist = [r.get("lev_net") for r in (sp.get("series") or [])]
    unusual = None
    if hist:
        unusual = {"what": "S&P 500 net position", "percentile": percentile(hist, hist[0]),
                   "n": len(hist), "as_of": sp.get("as_of"),
                   "note": ("Leveraged funds are structurally net short index futures. "
                            "The percentile says where this sits in its own three-year range; "
                            "the level alone is not a view.")}
    return build_verdict("Are hedge funds increasing or reducing exposure?", inputs,
                         streak_from=streak_src, band=band, unusual=unusual,
                         note=("Read on the weekly CHANGE in net futures position. "
                               "The level is structurally short and is not a signal."))


def leverage(cftc: dict, ofr: dict | None) -> dict:
    """Are hedge funds increasing or reducing leverage?

    Gross position — long plus short — is the weekly proxy: a book that
    doubles both legs has taken more risk and its net would not move. Form
    PF is the official answer and is about five months behind, so it is
    context, labelled with its own date, and never the weekly picture."""
    inputs, streak_src, band = [], None, 0.0
    for key, label in (("sp500", "S&P 500 futures, gross position (long + short)"),
                       ("nasdaq", "Nasdaq 100 futures, gross position"),
                       ("russell", "Russell 2000 futures, gross position")):
        m = cftc.get(key)
        i = _cftc_input(m, "lev_gross", "gross", label) if m else None
        if i:
            inputs.append(i)
            if key == "sp500":
                ch = weekly_changes(m["series"], "lev_gross")
                streak_src, band = ch, (typical(ch) or 0.0) * FLAT_BAND
    context = []
    for key, label in (("lev_top10", "Form PF: leverage of the ten largest funds"),
                       ("lev_11_50", "Form PF: leverage of funds 11-50"),
                       ("borrow_prime", "Form PF: prime brokerage borrowing")):
        node = (ofr or {}).get(key)
        if not node:
            continue
        prev, val = node.get("prev"), node.get("value")
        context.append({"label": label, "class": S.REGULATORY, "value": val,
                        "change": (val - prev) if (val is not None and prev is not None) else None,
                        "as_of": node.get("as_of"), "n": node.get("n"),
                        "percentile": percentile([p[1] for p in node.get("points") or []], val),
                        "note": "Quarterly, and about five months behind. Context, not this week."})
    out = build_verdict("Are hedge funds increasing or reducing leverage?", inputs,
                        vocab=(RISING, FALLING, FLAT), streak_from=streak_src, band=band,
                        note=("Weekly reading is gross futures position — both legs. "
                              "Form PF is the official measure and lags a quarter or more."))
    out["context"] = context
    return out


def longs_and_shorts(cftc: dict, short_interest: dict | None,
                     short_volume: dict | None) -> tuple[dict, dict]:
    """Are they reducing longs? Are they adding shorts, or covering?

    The two legs are read separately because they answer different
    questions, and a net figure hides both. Short interest is the anchor —
    an official count of shares actually short — and the daily short-volume
    share fills the two-week gap between reports as a proxy, labelled as
    one."""
    long_inputs, short_inputs = [], []
    src_l = src_s = None
    band_l = band_s = 0.0
    for key, label in (("sp500", "S&P 500 futures"), ("nasdaq", "Nasdaq 100 futures"),
                       ("russell", "Russell 2000 futures")):
        m = cftc.get(key)
        if not m:
            continue
        il = _cftc_input(m, "lev_long", "long", f"{label}, long contracts")
        ish = _cftc_input(m, "lev_short", "short", f"{label}, short contracts")
        if il:
            long_inputs.append(il)
            if key == "sp500":
                src_l = weekly_changes(m["series"], "lev_long")
                band_l = (typical(src_l) or 0.0) * FLAT_BAND
        if ish:
            short_inputs.append(ish)
            if key == "sp500":
                src_s = weekly_changes(m["series"], "lev_short")
                band_s = (typical(src_s) or 0.0) * FLAT_BAND
    if short_interest and short_interest.get("total") is not None:
        short_inputs.append(make_input(
            "si_total", "Shares short across all reported symbols", S.REGULATORY,
            short_interest.get("total"), short_interest.get("change"),
            as_of=short_interest.get("settlement"), public_on=short_interest.get("public_on"),
            n=short_interest.get("n_symbols"),
            note="FINRA consolidated short interest, twice a month"))
    if short_volume and short_volume.get("share") is not None:
        # The short share of volume moves in tenths of a percent day to day.
        # Without a band drawn from its own history, a move of 0.02 points
        # reads as a direction and the verdict swings on noise.
        hist = short_volume.get("history") or []
        daily = [hist[i] - hist[i + 1] for i in range(len(hist) - 1)]
        short_inputs.append(make_input(
            "shvol", "Short share of daily volume", S.FLOW_PROXY,
            short_volume.get("share"), short_volume.get("change"),
            as_of=short_volume.get("session"), weight=0.5,
            pct=short_volume.get("percentile"), n=short_volume.get("n_days"),
            band=(typical(daily) or 0.0) * 0.5,
            note="Pressure, not positions — most of it is covered the same day"))
    longs = build_verdict("Are they reducing longs?", long_inputs,
                          vocab=(ADDING, REDUCING, FLAT), streak_from=src_l, band=band_l,
                          note="The long leg alone. A net figure would hide it.")
    shorts = build_verdict("Are they adding shorts, or covering?", short_inputs,
                           vocab=(ADDING_SHORTS, COVERING, FLAT), streak_from=src_s, band=band_s,
                           note=("Short interest is the anchor; the daily short-volume share is a "
                                 "proxy that fills the fortnight between reports."))
    return longs, shorts


# ── sectors ─────────────────────────────────────────────────────────────────

def sectors(cftc: dict, etf_flows: dict | None, tide: dict | None,
            sector_si: dict | None) -> dict:
    """Which sectors are being bought, and which sold.

    Four inputs, and no sector has all four: only seven of the eleven have a
    leveraged-fund futures contract at all. Each sector reports which inputs
    it actually has, so a thin answer reads as thin rather than as a
    scanner fault."""
    names = set()
    for m in cftc.values():
        if m.get("kind") == "sector" and m.get("sector"):
            names.add(m["sector"])
    for src in (etf_flows or {}).get("by_sector", {}), (tide or {}), (sector_si or {}):
        names.update(k for k in (src or {}) if isinstance(k, str))
    names.update(S.CFTC_SECTORS_MISSING)
    rows = []
    for name in sorted(names):
        inputs = []
        streak_src, band = None, 0.0
        m = next((x for x in cftc.values() if x.get("sector") == name), None)
        if m:
            i = _cftc_input(m, "lev_net", "net", f"{name} futures, net position")
            if i:
                inputs.append(i)
                streak_src = weekly_changes(m["series"], "lev_net")
                band = (typical(streak_src) or 0.0) * FLAT_BAND
        fl = ((etf_flows or {}).get("by_sector") or {}).get(name)
        if fl is not None:
            inputs.append(make_input(f"etf_{name}", f"{name} ETF creations", S.FLOW_PROXY,
                                     fl.get("net"), fl.get("net"), as_of=fl.get("as_of"),
                                     weight=0.5, note="Creations minus redemptions, five sessions"))
        td = (tide or {}).get(name)
        if td is not None:
            inputs.append(make_input(f"tide_{name}", f"{name} options net premium", S.FLOW_PROXY,
                                     td.get("net_premium"), td.get("net_premium"),
                                     as_of=td.get("as_of"), weight=0.5,
                                     note="Call premium minus put premium, institutional-sized flow"))
        si = (sector_si or {}).get(name)
        if si is not None:
            # More shares short is selling pressure, so the sign is inverted
            # to keep every input on one scale: positive means bought.
            inputs.append(make_input(f"si_{name}", f"{name} short interest", S.REGULATORY,
                                     si.get("short"), -(si.get("change") or 0.0) if si.get("change") is not None else None,
                                     as_of=si.get("settlement"), n=si.get("n_symbols"),
                                     note="Rising short interest counts as selling"))
        v = build_verdict(f"{name}", inputs, streak_from=streak_src, band=band)
        v["sector"] = name
        v["has_futures"] = m is not None
        v["inputs_available"] = len([i for i in inputs if i.get("change") is not None])
        # How large this week's move is for THIS sector, in multiples of its
        # own typical week. The only scale on which sectors compare.
        v["move_size"] = None
        if streak_src:
            t = typical(streak_src)
            if t:
                v["move_size"] = abs(streak_src[0]) / t
                v["move_size_text"] = f"{v['move_size']:.1f}× its typical weekly move"
        if name in S.CFTC_SECTORS_MISSING:
            v["note"] = "No leveraged-fund futures contract exists for this sector — read on flows only."
        rows.append(v)
    bought = [r for r in rows if r["verdict"] == ADDING]
    sold = [r for r in rows if r["verdict"] == REDUCING]
    return {"rows": rows,
            "most_bought": sorted(bought, key=lambda r: -(r.get("move_size") or 0))[:5],
            "most_sold": sorted(sold, key=lambda r: -(r.get("move_size") or 0))[:5],
            "ranked_by": ("How big this week's move is for that sector IN ITS OWN TERMS — the change "
                          "divided by that contract's typical weekly move. The sector contracts differ "
                          "in size by more than tenfold, so ranking them by raw contracts would put "
                          "Financials above Utilities every week regardless of what happened."),
            "no_futures": list(S.CFTC_SECTORS_MISSING)}


# ── crowding ────────────────────────────────────────────────────────────────

def crowding(cftc: dict, crowded_names: list[dict] | None = None,
             lean_pctile: int = CROWDED_PCTILE, size_pctile: int = CROWDED_PCTILE) -> dict:
    """Where trades are crowded, and where crowding is unwinding.

    A crowded trade is two things at once, and they have to be two DIFFERENT
    things. The first cut counted net position, gross position and
    one-sidedness as three independent measures and called any two of them a
    crowd — but one-sidedness is |net| / gross, so whenever gross is steady a
    high net drags one-sidedness up with it and the same fact votes twice.
    Every quiet market with one notable reading came out crowded.

    So there are two measures: LEAN, how one-sided the book is (|net| over
    gross), and SIZE, how big it is (long plus short). A crowd needs both to
    be unusual for that market. The net percentile still says which side the
    lean is on, but it does not get a second vote.

    De-crowding is a book that was leaning that hard and is now leaning less
    — the part that hurts when it happens quickly."""
    rows = []
    for m in cftc.values():
        series = m.get("series") or []
        if len(series) < 12:
            continue
        nets = [r.get("lev_net") for r in series]
        gross = [r.get("lev_gross") for r in series]
        lean = [abs(r.get("lev_net") or 0) / (r.get("lev_gross") or 1) for r in series]
        p_net, p_gross, p_lean = (percentile(nets, nets[0]), percentile(gross, gross[0]),
                                  percentile(lean, lean[0]))
        flags = []
        if p_lean is not None and p_lean >= lean_pctile:
            flags.append({"what": "one-sidedness", "percentile": p_lean,
                          "side": "long" if (nets[0] or 0) > 0 else "short"})
        if p_gross is not None and p_gross >= size_pctile:
            flags.append({"what": "gross position", "percentile": p_gross, "side": "both"})
        prev_lean = percentile(lean[1:], lean[1]) if len(lean) > 12 else None
        unwinding = (p_lean is not None and prev_lean is not None
                     and prev_lean >= lean_pctile and p_lean < prev_lean)
        state = CROWDED if len(flags) >= 2 else (DECROWDING if unwinding else NORMAL)
        rows.append({"market": m.get("label"), "key": m.get("key"), "kind": m.get("kind"),
                     "state": state, "flags": flags, "as_of": m.get("as_of"), "weeks": len(series),
                     "percentiles": {"net": p_net, "gross": p_gross, "one_sided": p_lean},
                     "side": "long" if (nets[0] or 0) > 0 else "short",
                     "class": S.REGULATORY})
    out = {"markets": rows,
           "crowded": [r for r in rows if r["state"] == CROWDED],
           "decrowding": [r for r in rows if r["state"] == DECROWDING],
           "rule": (f"Crowded = the book is BOTH unusually one-sided (at or beyond the {lean_pctile}th "
                    f"percentile of its own history) AND unusually large (gross position at or beyond "
                    f"the {size_pctile}th). Two different facts, never one counted twice. "
                    f"De-crowding = was leaning that hard, now leaning less.")}
    if crowded_names:
        out["names"] = crowded_names
    return out


DTC_CAP = 999.0            # FINRA reports days-to-cover capped at 1000
MIN_ADV = 1_000_000.0      # shares a day — below this, days-to-cover is arithmetic, not a trade
MIN_SHORT = 1_000_000.0    # shares short


def crowded_shorts(short_interest_rows: dict | None, top: int = 12,
                   min_adv: float = MIN_ADV, min_short: float = MIN_SHORT) -> list[dict]:
    """The most crowded single-name shorts by days to cover — how many days
    of ordinary volume the shorts would need to buy back.

    Two filters, both learned from the data rather than assumed. FINRA caps
    the field at 1000, and 4,133 of the 22,482 rows in the August 14, 2026
    report sit at that cap: they are OTC foreign ordinaries with almost no
    volume, where dividing by a near-zero denominator produces a number that
    means nothing. And a name that trades under a million shares a day
    cannot be exited by anyone in any number of days, so its ratio is
    arithmetic rather than a trade. Without both floors the list is entirely
    untradeable tickers; with them it is names like NFE and OCGN.

    This is the one place the aggregate layer names securities. It names no
    fund, and days to cover is a figure from the report, not an inference."""
    rows = []
    for r in (short_interest_rows or {}).values():
        dtc, sh, adv = r.get("days_to_cover"), r.get("short"), r.get("adv")
        if dtc is None or sh is None or adv is None:
            continue
        if dtc >= DTC_CAP or adv < min_adv or sh < min_short:
            continue
        rows.append({"symbol": r["symbol"], "name": r.get("name"), "days_to_cover": dtc,
                     "short": sh, "adv": adv, "change_pct": r.get("change_pct"),
                     "settlement": r.get("settlement"), "class": S.REGULATORY})
    return sorted(rows, key=lambda r: -r["days_to_cover"])[:top]


# ── what changed since last week ────────────────────────────────────────────

def changed_since(now: dict, prior: dict | None) -> dict:
    """Every verdict whose answer is different from the stored prior week.

    This is what makes the board a record rather than a snapshot, and it is
    the only place a previous conclusion is allowed to matter."""
    if not prior:
        return {"available": False, "note": "No prior week is stored yet — this is the first reading."}
    changes = []
    for key in ("exposure", "leverage", "longs", "shorts"):
        a, b = (prior.get(key) or {}).get("verdict"), (now.get(key) or {}).get("verdict")
        if a and b and a != b:
            changes.append({"what": (now[key] or {}).get("question") or key, "from": a, "to": b})
    pa = {r["sector"]: r["verdict"] for r in ((prior.get("sectors") or {}).get("rows") or [])}
    for r in ((now.get("sectors") or {}).get("rows") or []):
        was = pa.get(r["sector"])
        if was and was != r["verdict"]:
            changes.append({"what": f"{r['sector']} sector", "from": was, "to": r["verdict"]})
    ca = {r["key"] for r in ((prior.get("crowding") or {}).get("crowded") or [])}
    cb = {r["key"] for r in ((now.get("crowding") or {}).get("crowded") or [])}
    for k in sorted(cb - ca):
        changes.append({"what": f"{k} crowding", "from": "not crowded", "to": CROWDED})
    for k in sorted(ca - cb):
        changes.append({"what": f"{k} crowding", "from": CROWDED, "to": "no longer crowded"})
    return {"available": True, "since": prior.get("week"), "since_date": prior.get("as_of"),
            "changes": changes, "n": len(changes)}


# ── the whole board ─────────────────────────────────────────────────────────

def build(cftc: dict, *, etf_flows=None, tide=None, short_interest=None,
          short_volume=None, sector_si=None, ofr=None, prior=None,
          prime_broker=None) -> dict:
    """Every weekly question, answered from what was actually available.

    `prime_broker` is Phase 3's channel — quoted claims attributed to a bank
    and an outlet. It is accepted here so the wiring exists, and it can only
    ever corroborate: the parameter is folded in as supporting evidence, and
    a verdict with no other input stays NO DATA."""
    ex = exposure(cftc, etf_flows)
    lev = leverage(cftc, ofr)
    longs, shorts = longs_and_shorts(cftc, short_interest, short_volume)
    sec = sectors(cftc, etf_flows, tide, sector_si)
    crowd = crowding(cftc, crowded_shorts(short_interest.get("rows") if short_interest else None))
    for quote in (prime_broker or []):
        target = {"exposure": ex, "leverage": lev, "longs": longs, "shorts": shorts}.get(quote.get("about"))
        if target is not None and target.get("verdict") != NO_DATA:
            target.setdefault("inputs", []).append(make_input(
                f"pb_{quote.get('bank', 'bank')}", quote.get("label") or "Prime broker note",
                S.PRIME_BROKER, None, quote.get("direction"), as_of=quote.get("as_of"),
                public_on=quote.get("public_on"), weight=0.5, note=quote.get("text")))
            target["confidence"] = confidence(target["inputs"])
    out = {"version": HF_PULSE_VERSION, "exposure": ex, "leverage": lev,
           "longs": longs, "shorts": shorts, "sectors": sec, "crowding": crowd}
    out["changed"] = changed_since(out, prior)
    out["questions"] = [ex, lev, longs, shorts]
    out["evidence_classes"] = sorted({i["class"] for q in out["questions"] for i in q.get("inputs") or []})
    return out
