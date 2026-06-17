#!/usr/bin/env python3
"""test_v117_broker.py — verifies the Schwab position normalizer added
in v1.17 phase 1. Tests run against synthetic Schwab payloads since
real broker calls require live OAuth + an account."""

import sys
import importlib.util


def _load_module():
    spec = importlib.util.spec_from_file_location("sc", "schwab_client.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    passed = 0
    failed = 0
    fails = []

    def assert_(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            fails.append((name, detail))
            print(f"  FAIL  {name}{(' · ' + detail) if detail else ''}")

    m = _load_module()
    SchwabClient = m.SchwabClient

    # Empty payload returns empty list, no throw.
    out = SchwabClient.normalize_positions({})
    assert_("empty_payload_returns_empty", out == [], f"got {out}")
    out = SchwabClient.normalize_positions(None)
    assert_("none_payload_returns_empty", out == [], f"got {out}")

    # Long stock position.
    payload = {
        "securitiesAccount": {
            "positions": [
                {
                    "longQuantity": 100,
                    "shortQuantity": 0,
                    "averagePrice": 175.50,
                    "instrument": {
                        "assetType": "EQUITY",
                        "symbol": "AAPL",
                        "cusip": "037833100",
                    }
                }
            ]
        }
    }
    out = SchwabClient.normalize_positions(payload)
    assert_("long_stock_normalizes",
            len(out) == 1 and out[0]["ticker"] == "AAPL"
            and out[0]["type"] == "stock" and out[0]["qty"] == 100,
            f"got {out}")

    # Short call option (covered call), discrete expiration fields.
    payload = {
        "securitiesAccount": {
            "positions": [
                {
                    "longQuantity": 0,
                    "shortQuantity": 1,
                    "averagePrice": 2.50,
                    "instrument": {
                        "assetType": "OPTION",
                        "symbol": "AAPL  250620C00200000",
                        "putCall": "CALL",
                        "underlyingSymbol": "AAPL",
                        "strikePrice": 200.0,
                        "expirationYear": 2025,
                        "expirationMonth": 6,
                        "expirationDay": 20,
                    }
                }
            ]
        }
    }
    out = SchwabClient.normalize_positions(payload)
    assert_("short_call_normalizes",
            len(out) == 1 and out[0]["type"] == "call"
            and out[0]["qty"] == -1 and out[0]["strike"] == 200.0
            and out[0]["expiration"] == "2025-06-20",
            f"got {out}")

    # Long put option, ISO expirationDate fallback.
    payload = {
        "securitiesAccount": {
            "positions": [
                {
                    "longQuantity": 2,
                    "shortQuantity": 0,
                    "averagePrice": 3.10,
                    "instrument": {
                        "assetType": "OPTION",
                        "symbol": "MSFT  250718P00400000",
                        "putCall": "PUT",
                        "underlyingSymbol": "MSFT",
                        "strikePrice": 400.0,
                        "expirationDate": "2025-07-18T00:00:00.000Z",
                    }
                }
            ]
        }
    }
    out = SchwabClient.normalize_positions(payload)
    assert_("long_put_iso_expiration_normalizes",
            len(out) == 1 and out[0]["type"] == "put"
            and out[0]["qty"] == 2 and out[0]["expiration"] == "2025-07-18",
            f"got {out}")

    # Multiple positions in one payload.
    payload = {
        "securitiesAccount": {
            "positions": [
                {"longQuantity": 100, "shortQuantity": 0, "averagePrice": 100.0,
                 "instrument": {"assetType": "EQUITY", "symbol": "NVDA"}},
                {"longQuantity": 0, "shortQuantity": 1, "averagePrice": 5.50,
                 "instrument": {"assetType": "OPTION", "putCall": "CALL",
                                "underlyingSymbol": "NVDA", "strikePrice": 150.0,
                                "expirationDate": "2025-08-15T00:00:00.000Z",
                                "symbol": "NVDA  250815C00150000"}},
                {"longQuantity": 1, "shortQuantity": 0, "averagePrice": 1000.0,
                 "instrument": {"assetType": "MUTUAL_FUND", "symbol": "VTSAX"}},
            ]
        }
    }
    out = SchwabClient.normalize_positions(payload)
    assert_("multiple_positions_filtered",
            len(out) == 2 and out[0]["ticker"] == "NVDA" and out[1]["type"] == "call",
            f"got {out}")

    # Zero qty positions skipped.
    payload = {
        "securitiesAccount": {
            "positions": [
                {"longQuantity": 0, "shortQuantity": 0, "averagePrice": 100.0,
                 "instrument": {"assetType": "EQUITY", "symbol": "ZERO"}},
            ]
        }
    }
    out = SchwabClient.normalize_positions(payload)
    assert_("zero_qty_skipped", out == [], f"got {out}")

    # Missing strike on option skipped.
    payload = {
        "securitiesAccount": {
            "positions": [
                {"longQuantity": 0, "shortQuantity": 1, "averagePrice": 1.0,
                 "instrument": {"assetType": "OPTION", "putCall": "CALL",
                                "underlyingSymbol": "FOO",
                                "expirationYear": 2025, "expirationMonth": 6,
                                "expirationDay": 20}},
            ]
        }
    }
    out = SchwabClient.normalize_positions(payload)
    assert_("missing_strike_skipped", out == [], f"got {out}")

    # Missing expiration on option skipped.
    payload = {
        "securitiesAccount": {
            "positions": [
                {"longQuantity": 0, "shortQuantity": 1, "averagePrice": 1.0,
                 "instrument": {"assetType": "OPTION", "putCall": "CALL",
                                "underlyingSymbol": "FOO", "strikePrice": 100.0}},
            ]
        }
    }
    out = SchwabClient.normalize_positions(payload)
    assert_("missing_expiration_skipped", out == [], f"got {out}")

    # Source field always set to schwab.
    payload = {
        "securitiesAccount": {
            "positions": [
                {"longQuantity": 100, "shortQuantity": 0, "averagePrice": 50.0,
                 "instrument": {"assetType": "EQUITY", "symbol": "TEST"}},
            ]
        }
    }
    out = SchwabClient.normalize_positions(payload)
    assert_("source_field_set", out[0]["source"] == "schwab", f"got {out}")

    print()
    total = passed + failed
    print(f"{passed}/{total} passed, {failed} failed")
    if failed:
        for name, detail in fails:
            print(f"  · {name}  {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
