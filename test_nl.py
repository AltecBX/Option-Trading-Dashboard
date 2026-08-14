"""Tests for nl_engine.py (v4.00 "Ask AI"): OpenAI translation with a
stubbed wire, the validator's whitelist/clamps, grammar fallback, the
universal latest-bar scanner (including parity with the Backtest Lab's
condition checker), saved strategies, and alert diff/dedupe.

No network anywhere: the OpenAI HTTP layer is replaced with a fake, and
bar data comes from synthetic fixtures.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("JERRY_DATA_DIR", tempfile.mkdtemp(prefix="jerry_nl_"))

import backtest as _bt  # noqa: E402
import nl_engine as nl  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────

def make_bars(n=120, base=100.0, drift=0.0, vol=1_000_000, d0_offset_days=0):
    """Flat-ish daily bars; deterministic, no randomness. `d0_offset_days`
    shifts the start so two series can be built to END on the same date."""
    out = []
    d0 = datetime(2026, 1, 2) + timedelta(days=d0_offset_days)
    px = base
    for i in range(n):
        px = px * (1 + drift)
        out.append({"date": (d0 + timedelta(days=i)).strftime("%Y-%m-%d"),
                    "open": round(px * 0.999, 4), "high": round(px * 1.01, 4),
                    "low": round(px * 0.99, 4), "close": round(px, 4),
                    "volume": vol})
    return out


def with_last(bars, *, chg_pct=None, vol_mult=None, open_rel=None):
    """Clone bars and reshape the LAST bar relative to the prior close."""
    bars = [dict(b) for b in bars]
    prev = bars[-2]["close"]
    last = bars[-1]
    if chg_pct is not None:
        last["close"] = round(prev * (1 + chg_pct / 100.0), 4)
    if vol_mult is not None:
        last["volume"] = int(bars[-2]["volume"] * vol_mult)
    if open_rel is not None:
        last["open"] = round(last["close"] * open_rel, 4)
    last["high"] = max(last["open"], last["close"]) * 1.001
    last["low"] = min(last["open"], last["close"]) * 0.999
    return bars


TAGS = {"Gold-Metals": ["GOLD", "AEM"], "AI & Chips": ["NVDA", "AMD", "AVGO"]}


def _configure(bars_map=None, push_log=None, tags=None):
    nl.configure(
        data_dir=os.environ["JERRY_DATA_DIR"],
        universe_fn=lambda: {"starred": ["AAA", "BBB"], "all": ["AAA", "BBB", "CCC"]},
        tags_fn=lambda: (TAGS if tags is None else tags),
        bars_fn=lambda sym, days: (bars_map or {}).get(sym, []),
        push_fn=(lambda t, m: push_log.append((t, m))) if push_log is not None else None,
        et_tz=None,
    )


def _ai_envelope(obj):
    """A minimal OpenAI chat.completions success body."""
    return {"choices": [{"message": {"content": json.dumps(obj)}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}


class _Wire:
    """Fake _http_post_json: scripted (status, body) responses, records calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({"url": url, "payload": json.loads(json.dumps(payload))})
        status, body = (self.responses.pop(0) if self.responses
                        else (500, {"error": {"message": "script exhausted"}}))
        return status, body, json.dumps(body) if body is not None else ""


class _NLBase(unittest.TestCase):
    def setUp(self):
        _configure()
        self._env = {k: os.environ.get(k) for k in
                     ("OPENAI_API_KEY", "OPENAI_MODEL", "JERRY_NO_NET")}
        os.environ.pop("JERRY_NO_NET", None)
        os.environ.pop("OPENAI_MODEL", None)
        self._orig_http = nl._http_post_json
        nl._RL_STAMPS.clear()
        for p in (nl._cache_path(), nl._board_path(), nl._strat_path()):
            if p is not None and p.exists():
                p.unlink()

    def tearDown(self):
        nl._http_post_json = self._orig_http
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── translate: fallback + AI paths ──────────────────────────────────────────

class TestTranslateFallback(_NLBase):
    def setUp(self):
        super().setUp()
        os.environ.pop("OPENAI_API_KEY", None)

    def test_no_key_uses_grammar(self):
        r = nl.translate("buy stocks down 3% today, exit at 5% profit")
        self.assertEqual(r["source"], "grammar")
        self.assertTrue(any("OPENAI_API_KEY" in w for w in r["warnings"]))
        self.assertTrue(any(c["type"] == "day_change_pct" for c in r["rules"]["entry"]))

    def test_intent_heuristics_without_ai(self):
        self.assertEqual(nl.translate("alert me when RSI below 30")["intent"], "alert")
        self.assertEqual(nl.translate("stocks with RSI below 30")["intent"], "scan")
        self.assertEqual(nl.translate("backtest buying RSI below 30")["intent"], "backtest")

    def test_no_net_notes_offline(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        os.environ["JERRY_NO_NET"] = "1"
        r = nl.translate("stocks down 5%")
        self.assertEqual(r["source"], "grammar")
        self.assertTrue(any("JERRY_NO_NET" in w for w in r["warnings"]))


class TestTranslateAI(_NLBase):
    def setUp(self):
        super().setUp()
        os.environ["OPENAI_API_KEY"] = "sk-test"

    def test_happy_path_validates_and_caches(self):
        wire = _Wire([(200, _ai_envelope({
            "intent": "scan",
            "rules": {"universe": {"source": "tag", "tag": "gold-metals"},
                      "entry": [{"type": "day_change_pct", "op": "<=", "value": -3},
                                {"type": "rel_volume", "mult": 2, "lookback": 20}]},
            "assumptions": ["double volume -> 2x/20d"], "unsupported": []}))])
        nl._http_post_json = wire
        r = nl.translate("my gold names down 3% on double volume")
        self.assertEqual(r["source"], "ai")
        self.assertEqual(r["intent"], "scan")
        self.assertEqual(r["rules"]["universe"], {"source": "tag", "tag": "Gold-Metals",
                                                  "symbols": []})
        self.assertEqual(len(r["rules"]["entry"]), 2)
        self.assertIn("Gold-Metals", r["restate"])
        self.assertEqual(r["tokens"], {"in": 1000, "out": 200})
        # identical text → served from cache, wire untouched
        r2 = nl.translate("my gold names down 3% on double volume")
        self.assertEqual(r2["source"], "cache")
        self.assertEqual(len(wire.calls), 1)

    def test_bad_key_falls_back_with_reason(self):
        wire = _Wire([(401, {"error": {"message": "Incorrect API key provided"}})])
        nl._http_post_json = wire
        r = nl.translate("stocks down 4% today")
        self.assertEqual(r["source"], "grammar")
        self.assertTrue(any("AI translation failed" in w for w in r["warnings"]))
        self.assertEqual(len(wire.calls), 1)  # 401 aborts the whole chain

    def test_non_json_content_falls_back(self):
        wire = _Wire([(200, {"choices": [{"message": {"content": "sure! here are"}}]}),
                      (200, {"choices": [{"message": {"content": "still prose"}}]}),
                      (200, {"choices": [{"message": {"content": "nope"}}]})])
        nl._http_post_json = wire
        r = nl.translate("stocks down 4% today")
        self.assertEqual(r["source"], "grammar")

    def test_json_inside_prose_is_extracted(self):
        obj = {"intent": "scan", "rules": {"entry": [{"type": "rsi", "op": "<=",
                                                      "value": 30, "period": 14}]},
               "assumptions": [], "unsupported": []}
        wire = _Wire([(200, {"choices": [{"message": {
            "content": "Here you go:\n" + json.dumps(obj)}}]})])
        nl._http_post_json = wire
        r = nl.translate("oversold stocks")
        self.assertEqual(r["source"], "ai")
        self.assertEqual(r["rules"]["entry"][0]["type"], "rsi")

    def test_unsupported_param_stripped_and_retried(self):
        wire = _Wire([
            (400, {"error": {"message": "Unsupported parameter: 'reasoning_effort'"}}),
            (200, _ai_envelope({"intent": "scan", "rules": {"entry": [
                {"type": "rsi", "op": "<=", "value": 30, "period": 14}]},
                "assumptions": [], "unsupported": []}))])
        nl._http_post_json = wire
        r = nl.translate("rsi under 30")
        self.assertEqual(r["source"], "ai")
        self.assertEqual(len(wire.calls), 2)
        self.assertIn("reasoning_effort", wire.calls[0]["payload"])
        self.assertNotIn("reasoning_effort", wire.calls[1]["payload"])

    def test_unknown_model_steps_down_chain(self):
        os.environ["OPENAI_MODEL"] = "gpt-9-ultra"
        wire = _Wire([
            (404, {"error": {"message": "The model `gpt-9-ultra` does not exist"}}),
            (200, _ai_envelope({"intent": "scan", "rules": {"entry": [
                {"type": "rsi", "op": "<=", "value": 30, "period": 14}]},
                "assumptions": [], "unsupported": []}))])
        nl._http_post_json = wire
        r = nl.translate("rsi under 30")
        self.assertEqual(r["source"], "ai")
        self.assertEqual(wire.calls[0]["payload"]["model"], "gpt-9-ultra")
        self.assertEqual(wire.calls[1]["payload"]["model"], "gpt-4.1-mini")

    def test_rate_limit_uses_grammar(self):
        nl._RL_STAMPS.extend([time.time()] * nl._RL_PER_MIN)
        wire = _Wire([])
        nl._http_post_json = wire
        r = nl.translate("stocks down 2% today")
        self.assertEqual(r["source"], "grammar")
        self.assertEqual(len(wire.calls), 0)

    def test_refinement_includes_base_rules(self):
        base = {"entry": [{"type": "rsi", "op": "<=", "value": 30, "period": 14}]}
        wire = _Wire([(200, _ai_envelope({"intent": "scan", "rules": {
            "entry": [{"type": "rsi", "op": "<=", "value": 25, "period": 14}]},
            "assumptions": [], "unsupported": []}))])
        nl._http_post_json = wire
        r = nl.translate("make it rsi 25", base_rules=base)
        self.assertEqual(r["rules"]["entry"][0]["value"], 25)
        user_msgs = [m for m in wire.calls[0]["payload"]["messages"]
                     if m["role"] == "user"]
        self.assertIn("CURRENT RULES", user_msgs[-1]["content"])


# ── validator ───────────────────────────────────────────────────────────────

class TestValidator(_NLBase):
    def _v(self, obj):
        return nl._validate_ai(obj, TAGS)

    def test_unknown_condition_type_moved_to_unsupported(self):
        r = self._v({"intent": "scan", "rules": {"entry": [
            {"type": "iv_rank", "op": ">=", "value": 50},
            {"type": "rsi", "op": "<=", "value": 30, "period": 14}]}})
        self.assertEqual(len(r["rules"]["entry"]), 1)
        self.assertEqual(r["rules"]["entry"][0]["type"], "rsi")
        self.assertTrue(any("iv_rank" in u["reason"] for u in r["unsupported"]))

    def test_numeric_clamps(self):
        r = self._v({"intent": "scan", "rules": {"entry": [
            {"type": "rsi", "op": "<=", "value": 150, "period": 500}]}})
        c = r["rules"]["entry"][0]
        self.assertEqual(c["value"], 99.0)
        self.assertEqual(c["period"], 50)

    def test_sma_cross_fast_slow_ordering_fixed(self):
        r = self._v({"intent": "scan", "rules": {"entry": [
            {"type": "sma_cross", "fast": 50, "slow": 50, "direction": "up"}]}})
        c = r["rules"]["entry"][0]
        self.assertLess(c["fast"], c["slow"])

    def test_unknown_tag_falls_back_to_starred_with_warning(self):
        r = self._v({"intent": "scan", "rules": {
            "universe": {"source": "tag", "tag": "Space Lasers"},
            "entry": [{"type": "rsi", "op": "<=", "value": 30, "period": 14}]}})
        self.assertEqual(r["rules"]["universe"]["source"], "starred")
        self.assertTrue(any("Space Lasers" in w for w in r["warnings"]))

    def test_tag_fuzzy_match_resolves_exact_name(self):
        r = self._v({"intent": "scan", "rules": {
            "universe": {"source": "tag", "tag": "ai & chips"},
            "entry": [{"type": "rsi", "op": "<=", "value": 30, "period": 14}]}})
        self.assertEqual(r["rules"]["universe"]["tag"], "AI & Chips")

    def test_option_structure_delta_normalized(self):
        r = self._v({"intent": "backtest", "rules": {"options": {
            "structure": "short_put", "target_delta": 30, "dte": 45,
            "management": {"profit_take_pct": 50, "exit_dte": 21}}}})
        o = r["rules"]["options"]
        self.assertEqual(r["rules"]["instrument"], "option")
        self.assertAlmostEqual(o["target_delta"], 0.30)
        self.assertEqual(o["management"]["profit_take_pct"], 50)
        self.assertNotIn("hold_to_expiry", o["management"])

    def test_unknown_structure_rejected(self):
        r = self._v({"intent": "backtest", "rules": {"options": {
            "structure": "quantum_condor"}}})
        self.assertIsNone(r["rules"]["options"])
        self.assertTrue(any("structure" in u["reason"] for u in r["unsupported"]))

    def test_backtest_without_exit_gets_default(self):
        r = self._v({"intent": "backtest", "rules": {"entry": [
            {"type": "rsi", "op": "<=", "value": 30, "period": 14}]}})
        self.assertTrue(any(x["type"] == "time_days" for x in r["rules"]["exit"]))

    def test_bad_symbols_dropped(self):
        r = self._v({"intent": "chart", "symbols": ["NVDA", "not a ticker", "1", "TSM"]})
        self.assertEqual(r["symbols"], ["NVDA", "TSM"])

    def test_named_symbols_become_universe_for_scans(self):
        r = self._v({"intent": "scan", "symbols": ["DELL", "TEVA"],
                     "rules": {"entry": [{"type": "rsi", "op": "<=", "value": 30,
                                          "period": 14}]}})
        self.assertEqual(r["rules"]["universe"],
                         {"source": "symbols", "symbols": ["DELL", "TEVA"]})


# ── scanner ─────────────────────────────────────────────────────────────────

class TestScan(_NLBase):
    def test_match_near_miss_and_values(self):
        bars_map = {
            "AAA": with_last(make_bars(), chg_pct=-5, vol_mult=3),   # both conds hit
            "BBB": with_last(make_bars(), chg_pct=-5, vol_mult=1),   # volume misses
            "CCC": make_bars(20),                                     # too little data
        }
        _configure(bars_map=bars_map)
        rules = {"universe": {"source": "all"},
                 "entry": [{"type": "day_change_pct", "op": "<=", "value": -3},
                           {"type": "rel_volume", "mult": 2, "lookback": 20}]}
        r = nl.run_scan(rules)
        self.assertEqual([m["symbol"] for m in r["matches"]], ["AAA"])
        self.assertEqual([m["symbol"] for m in r["near_misses"]], ["BBB"])
        self.assertIn("volume", r["near_misses"][0]["missed"])
        self.assertEqual(r["n_no_data"], 1)
        vals = {c["label"]: c["value"] for c in r["matches"][0]["checks"]}
        self.assertTrue(any(v and "×" in v for v in vals.values()))
        self.assertTrue(any(v and "%" in v for v in vals.values()))

    def test_condition_parity_with_backtest_ctx(self):
        """The scanner's checker IS the Lab's checker for daily conditions —
        same object for every non-intraday type at the last bar."""
        bars = with_last(make_bars(drift=0.001), chg_pct=-4, vol_mult=2.5)
        i = len(bars) - 1
        lab, scan = _bt._Ctx(bars, {}), nl._ScanCtx(bars, {})
        for cond in [
            {"type": "day_change_pct", "op": "<=", "value": -3},
            {"type": "rel_volume", "mult": 2, "lookback": 20},
            {"type": "rsi", "op": "<=", "value": 99, "period": 14},
            {"type": "drawdown_from_high", "pct": 2, "lookback": 60},
            {"type": "price_vs_sma", "op": ">=", "period": 50},
            {"type": "new_low", "lookback": 20},
            {"type": "consec_up", "n": 3},
            {"type": "price_abs", "op": ">=", "value": 5},
            {"type": "move_pct", "days": 5, "op": "<=", "value": 0},
        ]:
            self.assertEqual(lab.check(cond, i), scan.check(cond, i),
                             f"parity broke for {cond['type']}")

    def test_market_regime_pulls_spy(self):
        asked = []
        # SPY ends on the SAME date as AAA with 460 bars of history, so the
        # 200-day SMA regime is warmed up at the stock's latest bar.
        bars_map = {"AAA": make_bars(drift=0.002),
                    "SPY": make_bars(460, drift=0.002, d0_offset_days=120 - 460)}

        def bars_fn(sym, days):
            asked.append(sym)
            return bars_map.get(sym, [])
        nl.configure(data_dir=os.environ["JERRY_DATA_DIR"],
                     universe_fn=lambda: {"starred": ["AAA"], "all": ["AAA"]},
                     tags_fn=lambda: {}, bars_fn=bars_fn)
        r = nl.run_scan({"universe": {"source": "starred"},
                         "entry": [{"type": "market_regime", "regime": "uptrend"}]})
        self.assertIn("SPY", asked)
        self.assertEqual([m["symbol"] for m in r["matches"]], ["AAA"])

    def test_empty_rules_and_empty_universe_error(self):
        _configure(bars_map={})
        self.assertIn("error", nl.run_scan({"entry": []}))
        nl.configure(data_dir=os.environ["JERRY_DATA_DIR"],
                     universe_fn=lambda: {"starred": [], "all": []},
                     tags_fn=lambda: {}, bars_fn=lambda s, d: [])
        r = nl.run_scan({"entry": [{"type": "rsi", "op": "<=", "value": 30,
                                    "period": 14}]})
        self.assertIn("error", r)

    def test_scan_job_persists_board_and_guards(self):
        bars_map = {"AAA": with_last(make_bars(), chg_pct=-5),
                    "BBB": make_bars()}
        _configure(bars_map=bars_map)
        rules = {"universe": {"source": "starred"},
                 "entry": [{"type": "day_change_pct", "op": "<=", "value": -3}]}
        os.environ["JERRY_NO_NET"] = "1"
        self.assertFalse(nl.start_scan(rules)["started"])
        os.environ.pop("JERRY_NO_NET", None)
        self.assertTrue(nl.start_scan(rules)["started"])
        for _ in range(100):
            st = nl.get_board()["status"]
            if not st["scanning"]:
                break
            time.sleep(0.05)
        board = nl.get_board()["board"]
        self.assertEqual([m["symbol"] for m in board["matches"]], ["AAA"])
        self.assertEqual(board["rules"], rules)

    def test_tag_universe_resolution(self):
        _configure(bars_map={})
        syms, desc = nl.resolve_universe({"source": "tag", "tag": "AI & Chips"})
        self.assertEqual(syms, ["NVDA", "AMD", "AVGO"])
        self.assertIn("AI & Chips", desc)


# ── strategies + alerts ─────────────────────────────────────────────────────

RULES_DOWN3 = {"universe": {"source": "starred"},
               "entry": [{"type": "day_change_pct", "op": "<=", "value": -3}]}


class TestStrategiesAndAlerts(_NLBase):
    def test_crud_round_trip(self):
        r = nl.save_strategy("My dip scan", "stocks down 3%", RULES_DOWN3,
                             "scan", restated="Scan starred — changes ≤ -3%")
        sid = r["id"]
        self.assertEqual(nl.list_strategies()["items"][0]["name"], "My dip scan")
        nl.update_strategy(sid, name="Dips v2", alert_enabled=True)
        it = nl.get_strategy(sid)
        self.assertEqual(it["name"], "Dips v2")
        self.assertTrue(it["alert"]["enabled"])
        self.assertEqual(nl.delete_strategy(sid), {"ok": True})
        self.assertEqual(nl.list_strategies()["items"], [])
        self.assertIn("error", nl.delete_strategy("nope"))

    def test_alert_pushes_new_matches_once(self):
        push_log = []
        bars_hit = {"AAA": with_last(make_bars(), chg_pct=-5),
                    "BBB": make_bars()}
        _configure(bars_map=bars_hit, push_log=push_log)
        sid = nl.save_strategy("Dip alert", "", RULES_DOWN3, "alert",
                               alert=True)["id"]
        r1 = nl.check_alerts()
        self.assertEqual(r1["checked"], 1)
        self.assertEqual(r1["pushed"], 1)
        self.assertEqual(len(push_log), 1)
        self.assertIn("AAA", push_log[0][1])
        # same matches again → no re-push
        r2 = nl.check_alerts()
        self.assertEqual(r2["pushed"], 0)
        self.assertEqual(len(push_log), 1)
        # BBB starts matching too → only BBB is pushed
        bars_hit["BBB"] = with_last(make_bars(), chg_pct=-6)
        r3 = nl.check_alerts()
        self.assertEqual(r3["pushed"], 1)
        self.assertIn("BBB", push_log[1][1])
        self.assertNotIn("AAA", push_log[1][1])
        it = nl.get_strategy(sid)
        self.assertEqual(sorted(it["alert"]["last_matches"]), ["AAA", "BBB"])
        self.assertIsNotNone(it["last_run"])

    def test_alert_dedupe_window_blocks_flapping(self):
        push_log = []
        bars_hit = {"AAA": with_last(make_bars(), chg_pct=-5), "BBB": make_bars()}
        _configure(bars_map=bars_hit, push_log=push_log)
        nl.save_strategy("Dip alert", "", RULES_DOWN3, "alert", alert=True)
        nl.check_alerts()
        self.assertEqual(len(push_log), 1)
        # AAA stops matching, then matches again the next day → still inside
        # the dedupe window, so no second push.
        bars_hit["AAA"] = make_bars()
        nl.check_alerts()
        bars_hit["AAA"] = with_last(make_bars(), chg_pct=-5)
        nl.check_alerts()
        self.assertEqual(len(push_log), 1)

    def test_disabled_alerts_fetch_nothing(self):
        calls = []
        nl.configure(data_dir=os.environ["JERRY_DATA_DIR"],
                     universe_fn=lambda: {"starred": ["AAA"], "all": ["AAA"]},
                     tags_fn=lambda: {},
                     bars_fn=lambda s, d: calls.append(s) or [])
        nl.save_strategy("Off", "", RULES_DOWN3, "scan", alert=False)
        r = nl.check_alerts()
        self.assertEqual(r, {"checked": 0, "pushed": 0})
        self.assertEqual(calls, [])


# ── restate ─────────────────────────────────────────────────────────────────

class TestRestate(_NLBase):
    def test_scan_restate_mentions_everything(self):
        parsed = {"intent": "scan", "symbols": [], "rules": {
            "universe": {"source": "tag", "tag": "Gold-Metals"},
            "entry": [{"type": "rsi", "op": "<=", "value": 30, "period": 14},
                      {"type": "drawdown_from_high", "pct": 20, "lookback": 252}]}}
        s = nl.restate(parsed, TAGS)
        for frag in ("Gold-Metals", "RSI(14)", "30", "20", "high"):
            self.assertIn(frag, s)

    def test_structure_restate(self):
        parsed = {"intent": "backtest", "symbols": [], "rules": {
            "universe": {"source": "starred"}, "instrument": "option",
            "period_days": 1095, "entry": [], "exit": [],
            "options": {"structure": "short_put", "target_delta": 0.30, "dte": 45,
                        "management": {"profit_take_pct": 50, "exit_dte": 21}}}}
        s = nl.restate(parsed, TAGS)
        self.assertIn("short put", s)
        self.assertIn("30Δ", s)
        self.assertIn("45dte", s)
        self.assertIn("TP 50%", s)
        self.assertIn("~3y", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
