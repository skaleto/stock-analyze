from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_analyze.evolution_writer import write_evolution
from stock_analyze.markets.a_share.backtest.types import BacktestMetrics


def _baseline(path: Path, *, qdii: bool) -> None:
    payload = {
        "competition_id": "test",
        "start_date": "2026-01-01",
        "initial_cash": 1_000_000,
        "accounts": [
            {
                "id": "us_exposure" if qdii else "hs300",
                "scope": "us_exposure" if qdii else "hs300",
                "benchmark": "513100.SH" if qdii else "000300",
                "cash": 1_000_000,
                "top_n": 5 if qdii else 50,
            }
        ],
        "schedule": {"execution": "weekly", "signal_day": "friday"},
        "trading": {
            "lot_size": 100,
            "commission_rate": 0.0003,
            "min_commission": 5,
            "stamp_tax_rate": 0,
            "slippage_rate": 0.0005,
            "max_single_weight": 0.2,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class EvolutionWriterMultiMarketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "configs" / "agents").mkdir(parents=True)
        _baseline(self.root / "configs" / "competition_a_share.yaml", qdii=False)
        _baseline(self.root / "configs" / "competition_cn_qdii_etf.yaml", qdii=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_qdii_evolution_uses_market_paths_and_skips_a_share_gate(self) -> None:
        old = {
            "agent_id": "claude",
            "strategy_id": "old-etf",
            "name": "Old ETF",
            "factors": {"momentum_20": {"weight": 1.0, "direction": "high"}},
        }
        new = {
            **old,
            "strategy_id": "defensive-etf-v1",
            "name": "稳健防守 · 低波均衡",
            "factors": {"low_volatility_60": {"weight": 1.0, "direction": "low"}},
        }
        qdii_path = self.root / "configs" / "agents" / "claude_cn_qdii_etf.yaml"
        qdii_path.write_text(json.dumps(old), encoding="utf-8")
        a_share_path = self.root / "configs" / "agents" / "claude_a_share.yaml"
        a_share_path.write_text(json.dumps({"sentinel": "a-share"}), encoding="utf-8")

        with patch(
            "stock_analyze.markets.a_share.backtest.gate.validate_overlay_via_backtest"
        ) as gate:
            result = write_evolution(
                "claude",
                old,
                new,
                "# QDII reasoning",
                repo_root=self.root,
                month="2026-07-takeover",
                market="cn_qdii_etf",
            )

        gate.assert_not_called()
        self.assertEqual(json.loads(qdii_path.read_text()), new)
        self.assertEqual(json.loads(a_share_path.read_text()), {"sentinel": "a-share"})
        self.assertIn("data/cn_qdii_etf/claude", result["log_path"])
        diff = json.loads(Path(result["diff_path"]).read_text(encoding="utf-8"))
        self.assertEqual(diff["market"], "cn_qdii_etf")
        self.assertEqual(diff["backtest_status"], "not_available")

    def test_a_share_evolution_keeps_the_existing_backtest_gate(self) -> None:
        old = {
            "agent_id": "codex",
            "strategy_id": "old-a",
            "name": "Old A",
            "factors": {"pe": {"weight": 1.0, "direction": "low"}},
        }
        new = {**old, "strategy_id": "new-a", "name": "New A"}
        path = self.root / "configs" / "agents" / "codex_a_share.yaml"
        path.write_text(json.dumps(old), encoding="utf-8")
        metrics = BacktestMetrics(0.1, 0.08, 1.0, -0.05, 0.7)

        with patch(
            "stock_analyze.markets.a_share.backtest.gate.validate_overlay_via_backtest",
            return_value=metrics,
        ) as gate:
            result = write_evolution(
                "codex",
                old,
                new,
                "# A reasoning",
                repo_root=self.root,
                month="2026-07-takeover",
                market="a_share",
            )

        gate.assert_called_once()
        diff = json.loads(Path(result["diff_path"]).read_text(encoding="utf-8"))
        self.assertEqual(diff["market"], "a_share")
        self.assertEqual(diff["backtest_status"], "passed")


if __name__ == "__main__":
    unittest.main()
