from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.earnings_drift_study import (
    build_lightweight_event_price_panel,
    build_event_return_panel,
    evaluate_panel,
    load_contract,
)


class EarningsDriftStudyTest(unittest.TestCase):
    def _contract(self, root: Path, **updates):
        payload = {
            "protocol_version": "earnings-drift-preregistered-v1",
            "market": "a_share",
            "development_start": "20180101",
            "development_end": "20241231",
            "historical_diagnostic_start": "20250101",
            "live_oos_start": "20260818",
            "event_types": ["earnings_forecast", "earnings_flash"],
            "horizons": [5, 20, 60],
            "minimum_confidence": 0.7,
            "minimum_strength": 0.25,
            "maximum_entry_lag_calendar_days": 7,
            "round_trip_cost": 0.0021,
            "stress_cost_multiple": 1.5,
            "bootstrap_samples": 200,
            "bootstrap_seed": 7,
            "minimum_total_events": 3,
            "minimum_positive_events": 3,
            "minimum_unique_securities": 3,
            "minimum_event_years": 3,
            "minimum_scope_events": 1,
            "minimum_positive_year_fraction": 0.66,
            "minimum_bootstrap_probability": 0.5,
            "maximum_year_contribution_share": 0.8,
        }
        payload.update(updates)
        path = root / "contract.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_contract(path)

    def test_rejects_horizon_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "earnings_drift_horizons"):
                self._contract(Path(tmp), horizons=[5, 20])

    def test_next_open_panel_does_not_use_same_day_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._contract(Path(tmp))
            events = pd.DataFrame([{
                "event_id": "e1", "event_type": "earnings_forecast",
                "direction": 1.0, "strength": 1.0, "confidence": 1.0,
                "available_at": "2020-01-02T10:00:00+00:00",
                "entity_type": "security", "entity_id": "000001.SZ",
            }])
            dates = pd.date_range("2020-01-02", periods=70, freq="B")
            prices = pd.DataFrame({
                "code": "000001", "trade_date": dates.strftime("%Y%m%d"),
                "account_id": "hs300", "open": [10.0] + [20.0] * 69,
                "close": [10.0] + [22.0] * 69,
            })
            benchmark = pd.DataFrame({
                "trade_date": dates.strftime("%Y%m%d"),
                "open": 100.0, "close": 100.0,
            })
            panel = build_event_return_panel(
                events, prices, {"hs300": benchmark}, contract
            )

        first = panel.loc[panel["horizon"].eq(5)].iloc[0]
        self.assertEqual(first["entry_date"], "20200103")
        self.assertAlmostEqual(first["security_return"], 0.10)

    def test_insufficient_evidence_blocks_claim_and_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._contract(
                Path(tmp), minimum_total_events=60, minimum_positive_events=30
            )
            panel = pd.DataFrame([{
                "event_id": "e1", "code": "000001", "event_year": "2020",
                "account_scope": "hs300", "signal_side": "positive",
                "horizon": 60, "active_return": 0.1,
                "net_active_return": 0.09, "stress_net_active_return": 0.08,
            }])
            result = evaluate_panel(panel, contract)

        self.assertEqual(result["status"], "insufficient_data")
        self.assertFalse(result["model_training_allowed"])
        self.assertTrue(result["formal_strategy_unchanged"])

    def test_one_scope_cannot_satisfy_the_evidence_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._contract(Path(tmp))
            rows = []
            for index, year in enumerate(("2020", "2021", "2022")):
                rows.append({
                    "event_id": f"e{index}",
                    "code": f"00000{index + 1}",
                    "event_year": year,
                    "account_scope": "hs300",
                    "signal_side": "positive",
                    "horizon": 60,
                    "active_return": 0.02,
                    "net_active_return": 0.0179,
                    "stress_net_active_return": 0.01685,
                })

            result = evaluate_panel(pd.DataFrame(rows), contract)

        self.assertEqual(result["status"], "insufficient_data")
        self.assertFalse(result["evidence_checks"]["scope_events"])

    def test_mature_negative_evidence_falsifies_hypothesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract = self._contract(Path(tmp))
            rows = []
            for scope_index, scope in enumerate(("hs300", "zz500")):
                for index, year in enumerate(("2020", "2021", "2022")):
                    for horizon in (5, 20, 60):
                        rows.append({
                            "event_id": f"{scope}-e{index}",
                            "code": f"{scope_index + 1}{index + 1:05d}",
                            "event_year": year,
                            "account_scope": scope,
                            "signal_side": "positive",
                            "horizon": horizon,
                            "active_return": -0.01,
                            "net_active_return": -0.0121,
                            "stress_net_active_return": -0.01315,
                        })
            result = evaluate_panel(pd.DataFrame(rows), contract)

        self.assertEqual(result["status"], "falsified")
        self.assertFalse(result["model_training_allowed"])

    def test_lightweight_panel_uses_materialized_history_and_pit_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "data" / "shared" / "cache"
            raw = root / "data" / "research" / "raw" / "a_share" / "20241231"
            cache.mkdir(parents=True)
            raw.mkdir(parents=True)
            history = cache / "history_000001_20241231_4.csv"
            pd.DataFrame({
                "code": ["000001"] * 4,
                "trade_date": ["20180102", "20180103", "20180104", "20180105"],
                "open": [10.0, 10.1, 10.2, 10.3],
                "close": [10.1, 10.2, 10.3, 10.4],
                "adj_factor": [1.0] * 4,
            }).to_csv(history, index=False)
            relative = history.relative_to(root).as_posix()
            second_history = cache / "history_000002_20241231_4.csv"
            pd.DataFrame({
                "code": ["000002"] * 4,
                "trade_date": ["20180102", "20180103", "20180104", "20180105"],
                "open": [20.0, 20.1, 20.2, 20.3],
                "close": [20.1, 20.2, 20.3, 20.4],
                "adj_factor": [1.0] * 4,
            }).to_csv(second_history, index=False)
            second_relative = second_history.relative_to(root).as_posix()
            (raw / "materialization_manifest.json").write_text(json.dumps({
                "status": "complete",
                "outputs": {
                    relative: {"path": relative},
                    second_relative: {"path": second_relative},
                },
            }), encoding="utf-8")
            pd.DataFrame({
                "index_code": ["000300.SH", "000905.SH"],
                "con_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["20171229", "20171229"],
                "weight": [1.0, 1.0],
            }).to_parquet(raw / "index_weight.parquet", index=False)

            panel = build_lightweight_event_price_panel(
                root,
                snapshot_date="20241231",
                development_start="20180101",
                development_end="20181231",
            )

        self.assertEqual(len(panel), 8)
        self.assertEqual(set(panel["account_id"]), {"hs300", "zz500"})
        self.assertIn("adjusted_open", panel)


if __name__ == "__main__":
    unittest.main()
