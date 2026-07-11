from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_analyze.markets.a_share.backtest.types import BacktestMetrics
from stock_analyze.strategy_registry import StrategyPairInvalid
from stock_analyze.strategy_release import apply_strategy_release


class StrategyReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "configs" / "agents").mkdir(parents=True)
        version_dir = self.root / "configs" / "strategy_versions" / "release"
        version_dir.mkdir(parents=True)
        registry = {
            "season_id": "test-season",
            "name": "测试赛季",
            "effective_date": "2026-07-11",
            "factor_distance_floor": 0.45,
            "slots": {
                "claude": {"label": "稳健防守", "description": "def", "color": "#d6a84b"},
                "codex": {"label": "趋势进攻", "description": "trend", "color": "#22d3ee"},
            },
        }
        (self.root / "configs" / "strategy_competition.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )
        self.entries = []
        for market in ("a_share", "cn_qdii_etf"):
            baseline = {
                "competition_id": market,
                "start_date": "2026-01-01",
                "initial_cash": 1_000_000,
                "accounts": [{
                    "id": "acc", "scope": "scope", "benchmark": "bench",
                    "cash": 1_000_000, "top_n": 5,
                }],
                "schedule": {"execution": "weekly", "signal_day": "friday"},
                "trading": {
                    "lot_size": 100, "commission_rate": 0.0003,
                    "min_commission": 5, "stamp_tax_rate": 0,
                    "slippage_rate": 0.0005, "max_single_weight": 0.2,
                },
            }
            (self.root / "configs" / f"competition_{market}.yaml").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            for agent in ("claude", "codex"):
                old_factor = "pe" if market == "a_share" else "momentum_20"
                old = {
                    "agent_id": agent,
                    "strategy_id": f"old-{agent}-{market}",
                    "name": f"Old {agent} {market}",
                    "factors": {old_factor: {"weight": 1.0, "direction": "low" if old_factor == "pe" else "high"}},
                }
                live = self.root / "configs" / "agents" / f"{agent}_{market}.yaml"
                live.write_text(json.dumps(old), encoding="utf-8")
                defensive_factor = "pe" if market == "a_share" else "low_volatility_60"
                trend_factor = "momentum_20"
                factor = defensive_factor if agent == "claude" else trend_factor
                direction = "low" if factor in {"pe", "low_volatility_60"} else "high"
                desired = {
                    "agent_id": agent,
                    "strategy_id": f"new-{agent}-{market}",
                    "name": "稳健防守" if agent == "claude" else "趋势进攻",
                    "factors": {factor: {"weight": 1.0, "direction": direction}},
                }
                overlay_name = f"{agent}_{market}.json"
                (version_dir / overlay_name).write_text(json.dumps(desired), encoding="utf-8")
                self.entries.append({
                    "market": market,
                    "agent_id": agent,
                    "overlay": overlay_name,
                    "reasoning": f"# {agent} {market}",
                })
        self.manifest = version_dir / "manifest.json"
        self.manifest.write_text(
            json.dumps({
                "release_id": "release",
                "month": "2026-07-takeover",
                "reviewer": "test",
                "entries": self.entries,
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_release_is_idempotent_across_both_markets(self) -> None:
        metrics = BacktestMetrics(0.1, 0.08, 1.0, -0.05, 0.7)
        with patch(
            "stock_analyze.markets.a_share.backtest.gate.validate_overlay_via_backtest",
            return_value=metrics,
        ) as gate:
            first = apply_strategy_release(self.manifest, self.root)
            second = apply_strategy_release(self.manifest, self.root)

        self.assertEqual([row["status"] for row in first["entries"]], ["evolved"] * 4)
        self.assertEqual([row["status"] for row in second["entries"]], ["unchanged"] * 4)
        self.assertEqual(gate.call_count, 2)
        for entry in self.entries:
            live = self.root / "configs" / "agents" / f"{entry['agent_id']}_{entry['market']}.yaml"
            desired = self.manifest.parent / entry["overlay"]
            self.assertEqual(json.loads(live.read_text()), json.loads(desired.read_text()))
            csv_path = self.root / "data" / entry["market"] / entry["agent_id"] / "config_evolution.csv"
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)

    def test_pair_guard_failure_writes_nothing(self) -> None:
        bad_path = self.manifest.parent / "codex_cn_qdii_etf.json"
        bad = json.loads((self.manifest.parent / "claude_cn_qdii_etf.json").read_text())
        bad["agent_id"] = "codex"
        bad["strategy_id"] = "different-id"
        bad["name"] = "different-name"
        bad_path.write_text(json.dumps(bad), encoding="utf-8")
        before = {
            path: path.read_text(encoding="utf-8")
            for path in (self.root / "configs" / "agents").glob("*.yaml")
        }

        with self.assertRaises(StrategyPairInvalid):
            apply_strategy_release(self.manifest, self.root)

        self.assertEqual(
            before,
            {path: path.read_text(encoding="utf-8") for path in before},
        )
        self.assertFalse((self.root / "data").exists())


if __name__ == "__main__":
    unittest.main()
