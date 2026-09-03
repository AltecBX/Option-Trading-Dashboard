"""Sector from the SEC's SIC code (v4.76).

The watchlist table's "Sectors" view groups rows by a `sector` field that
came from one place: Yahoo's `.info` call. Yahoo throttles that call hard
during a 1,300-name sweep and answers with an empty dict, so a scan that
ran perfectly well produced rows with no sector, and the Sectors tab showed
nothing — not because the scan did not run (it runs at 9 AM and 6 PM ET
every weekday) but because the one source of a sector label had gone quiet.

The SEC assigns every filer a four-digit Standard Industrial Classification
code. It never changes, it is served without a rate limit anyone hits at
our pace, and the repo already fetches and caches it for thirty days
(`fundamentals.sic_metadata`). This module maps that code onto the eleven
sector labels the interface already uses — Yahoo's taxonomy, so a row
labelled from the SEC groups with the rows Yahoo labelled.

Precedence, in the scanners that use this: the user's own CSV label, then
Yahoo's answer, then the label the row carried last scan, then the SIC.
The SIC is the floor, never the override — where Yahoo and the SEC disagree
(Alphabet files as computer services; Yahoo calls it Communication
Services) the row keeps Yahoo's word as soon as Yahoo has given it once.
"""
from __future__ import annotations

import threading

try:
    import fundamentals as _fund
except Exception:                                    # pragma: no cover
    _fund = None

try:
    from storage import _stable_data_dir
except Exception:                                    # pragma: no cover
    _stable_data_dir = None

TECH = "Technology"
COMM = "Communication Services"
CYC = "Consumer Cyclical"
DEF = "Consumer Defensive"
ENERGY = "Energy"
FIN = "Financial Services"
HEALTH = "Healthcare"
IND = "Industrials"
MAT = "Basic Materials"
RE = "Real Estate"
UTIL = "Utilities"

SECTORS = (TECH, COMM, CYC, DEF, ENERGY, FIN, HEALTH, IND, MAT, RE, UTIL)

# Exact four-digit codes first, then ranges. The first match wins, so a
# specific code sits above the range that would otherwise swallow it.
_EXACT = {
    # mining and drilling
    "1220": ENERGY, "1221": ENERGY, "1311": ENERGY, "1381": ENERGY,
    "1382": ENERGY, "1389": ENERGY,
    # homebuilders are a consumer business, not construction
    "1520": CYC, "1531": CYC, "1540": CYC,
    # chemicals: drugs are healthcare, soap is a household staple
    "2833": HEALTH, "2834": HEALTH, "2835": HEALTH, "2836": HEALTH,
    "2840": DEF, "2842": DEF, "2844": DEF,
    # petroleum
    "2911": ENERGY, "2990": ENERGY,
    # rubber: tyres and footwear are consumer goods
    "3011": CYC, "3021": CYC,
    # computers and peripherals inside the machinery group
    "3570": TECH, "3571": TECH, "3572": TECH, "3575": TECH, "3576": TECH,
    "3577": TECH, "3578": TECH, "3579": TECH,
    # household appliances and audio inside the electronics group
    "3630": CYC, "3634": CYC, "3651": CYC,
    # vehicles
    "3711": CYC, "3713": CYC, "3714": CYC, "3715": CYC, "3716": CYC,
    "3751": CYC, "3790": CYC,
    # instruments: medical and lab are healthcare, the rest technology
    "3812": IND, "3826": HEALTH, "3829": IND, "3841": HEALTH, "3842": HEALTH,
    "3843": HEALTH, "3844": HEALTH, "3845": HEALTH, "3851": HEALTH,
    "3873": CYC,
    # gas and power
    "4922": ENERGY, "4953": IND, "4955": IND,
    # wholesale
    "5045": TECH, "5065": TECH, "5122": HEALTH, "5140": DEF, "5141": DEF,
    "5149": DEF, "5171": ENERGY, "5172": ENERGY,
    # retail: staples and pharmacies
    "5331": DEF, "5399": DEF, "5411": DEF, "5412": DEF, "5912": HEALTH,
    # finance: health plans are healthcare, REITs and land are real estate
    "6324": HEALTH, "6792": ENERGY, "6795": MAT, "6798": RE,
    # services
    "7011": CYC, "7310": COMM, "7311": COMM, "8200": DEF, "8300": HEALTH,
    "8731": HEALTH,
}

_RANGES = (
    # (low, high, sector) on the four-digit code, inclusive
    ("0100", "0799", DEF), ("0800", "0899", MAT), ("0900", "0999", DEF),
    ("1000", "1099", MAT), ("1200", "1299", ENERGY), ("1300", "1399", ENERGY),
    ("1400", "1499", MAT), ("1500", "1799", IND),
    ("2000", "2199", DEF), ("2200", "2399", CYC), ("2400", "2449", MAT),
    ("2450", "2459", CYC), ("2500", "2599", CYC), ("2600", "2699", MAT),
    ("2700", "2799", COMM), ("2800", "2829", MAT), ("2830", "2839", HEALTH),
    ("2840", "2849", DEF), ("2850", "2899", MAT), ("2900", "2999", ENERGY),
    ("3000", "3099", MAT), ("3100", "3199", CYC), ("3200", "3299", MAT),
    ("3300", "3399", MAT), ("3400", "3499", IND), ("3500", "3599", IND),
    ("3600", "3699", TECH), ("3700", "3799", IND), ("3800", "3819", IND),
    ("3820", "3829", TECH), ("3830", "3859", HEALTH), ("3860", "3869", TECH),
    ("3870", "3999", CYC),
    ("4000", "4799", IND), ("4800", "4899", COMM),
    ("4900", "4999", UTIL),
    ("5000", "5199", IND),
    ("5200", "5399", CYC), ("5400", "5499", DEF), ("5500", "5999", CYC),
    ("6000", "6499", FIN), ("6500", "6599", RE), ("6600", "6799", FIN),
    ("7000", "7299", CYC), ("7300", "7369", IND), ("7370", "7379", TECH),
    ("7380", "7699", IND), ("7800", "7999", COMM),
    ("8000", "8099", HEALTH), ("8100", "8199", IND), ("8200", "8299", DEF),
    ("8300", "8399", HEALTH), ("8400", "8999", IND),
)


def sector_for_sic(sic) -> str | None:
    """The Yahoo-taxonomy sector for a four-digit SIC code, or None when
    the code is missing or outside every range we know."""
    if sic is None:
        return None
    code = str(sic).strip()
    if not code.isdigit():
        return None
    code = code.zfill(4)[-4:]
    hit = _EXACT.get(code)
    if hit:
        return hit
    for lo, hi, sector in _RANGES:
        if lo <= code <= hi:
            return sector
    return None


_CONFIGURED = False
_CONFIG_LOCK = threading.Lock()


def _ensure_configured() -> None:
    """Point the SIC cache at the persistent data dir once, so a label
    fetched today survives the next redeploy. Idempotent and harmless when
    another module configured it first."""
    global _CONFIGURED
    if _CONFIGURED or _fund is None:
        return
    with _CONFIG_LOCK:
        if _CONFIGURED:
            return
        try:
            if getattr(_fund, "_DATA_DIR", None) is None and _stable_data_dir is not None:
                _fund.configure(data_dir=_stable_data_dir())
        except Exception:
            pass
        _CONFIGURED = True


def available() -> bool:
    return _fund is not None and bool(_fund.available())


def sector_hint(symbol: str) -> dict:
    """{sector, industry, sic} from the SEC's classification of `symbol`.
    Every field is None when the SEC has no filer for the ticker or cannot
    be reached. Never raises."""
    out = {"sector": None, "industry": None, "sic": None}
    if not available():
        return out
    _ensure_configured()
    try:
        meta = _fund.sic_metadata(symbol) or {}
    except Exception:
        return out
    sic = meta.get("sic")
    out["sic"] = sic
    out["sector"] = sector_for_sic(sic)
    desc = (meta.get("sic_description") or "").strip()
    out["industry"] = desc or None
    return out
