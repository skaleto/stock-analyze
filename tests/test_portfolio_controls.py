from __future__ import annotations

import unittest

import pandas as pd

from stock_analyze.config import migrate_strategy_config
from stock_analyze.markets.a_share.portfolio_controls import select_top_n_with_controls


def make_candidates(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["score"] = df["score"].astype(float)
    return df


class IndustryCapTests(unittest.TestCase):
    def test_single_industry_cap_skips_overflowing_picks(self) -> None:
        candidates = make_candidates(
            [
                {"code": str(i).zfill(6), "name": f"S{i}", "industry": industry, "score": 100 - i}
                for i, industry in enumerate(
                    ["金融"] * 4 + ["消费"] * 3 + ["工业"] * 3 + ["医药"] * 3 + ["科技"] * 3 + ["材料"] * 3
                )
            ]
        )
        config = {"portfolio_controls": {"max_industry_weight": 0.30}}
        selected, warnings = select_top_n_with_controls(candidates, {"positions": {}}, config, top_n=10)
        finance_count = (selected["industry"] == "金融").sum()
        self.assertLessEqual(int(finance_count), 3)
        self.assertNotIn("industry_cap_relaxed", warnings)

    def test_industry_cap_relaxed_when_universe_too_small(self) -> None:
        candidates = make_candidates(
            [
                {"code": "000001", "name": "A1", "industry": "金融", "score": 10},
                {"code": "000002", "name": "A2", "industry": "金融", "score": 9},
                {"code": "000003", "name": "B1", "industry": "消费", "score": 8},
                {"code": "000004", "name": "B2", "industry": "消费", "score": 7},
            ]
        )
        config = {"portfolio_controls": {"max_industry_weight": 0.10}}
        selected, warnings = select_top_n_with_controls(candidates, {"positions": {}}, config, top_n=4)
        self.assertEqual(len(selected), 4)
        self.assertIn("industry_cap_relaxed", warnings)


class HoldBufferTests(unittest.TestCase):
    def test_existing_holding_in_buffer_is_retained(self) -> None:
        candidates = make_candidates(
            [
                {"code": str(i + 1).zfill(6), "name": f"S{i+1}", "industry": str(i), "score": 100 - i}
                for i in range(15)
            ]
        )
        config = {"portfolio_controls": {"max_industry_weight": 1.0, "hold_buffer_pct": 0.5}}
        # Code 000013 currently held — rank 13, within buffer of top_n=10 × 1.5 = 15.
        positions = {"000013": {"shares": 100, "last_buy_date": "2026-04-01"}}
        selected, _ = select_top_n_with_controls(candidates, {"positions": positions}, config, top_n=10)
        codes = set(selected["code"].astype(str))
        self.assertIn("000013", codes)

    def test_existing_holding_outside_buffer_is_dropped(self) -> None:
        candidates = make_candidates(
            [
                {"code": str(i + 1).zfill(6), "name": f"S{i+1}", "industry": str(i), "score": 100 - i}
                for i in range(20)
            ]
        )
        config = {"portfolio_controls": {"max_industry_weight": 1.0, "hold_buffer_pct": 0.5}}
        positions = {"000018": {"shares": 100, "last_buy_date": "2026-04-01"}}
        selected, _ = select_top_n_with_controls(candidates, {"positions": positions}, config, top_n=10)
        codes = set(selected["code"].astype(str))
        self.assertNotIn("000018", codes)

    def test_max_holding_days_forces_reevaluation(self) -> None:
        candidates = make_candidates(
            [
                {"code": str(i + 1).zfill(6), "name": f"S{i+1}", "industry": "金融", "score": 100 - i}
                for i in range(20)
            ]
        )
        config = {"portfolio_controls": {"max_industry_weight": 1.0, "hold_buffer_pct": 1.0, "max_holding_days": 30}}
        positions = {"000016": {"shares": 100, "last_buy_date": "2026-03-01"}}
        selected, warnings = select_top_n_with_controls(
            candidates,
            {"positions": positions},
            config,
            top_n=10,
            run_date="2026-05-01",
        )
        codes = set(selected["code"].astype(str))
        # Held position is forced into re-evaluation, drops out because rank 16 > top_n.
        self.assertNotIn("000016", codes)
        self.assertIn("max_holding_days_reevaluation:000016", warnings)


class ConfigMigrationTests(unittest.TestCase):
    def test_market_cap_is_demoted_to_filters(self) -> None:
        config = {
            "factors": {"pe": {"weight": 0.5, "direction": "low"}, "market_cap_yi": {"weight": 0.5, "direction": "high"}},
        }
        applied = migrate_strategy_config(config)
        self.assertIn("config_v1_market_cap_demoted", applied)
        self.assertNotIn("market_cap_yi", config["factors"])
        self.assertEqual(config["filters"]["min_market_cap_yi"], 30)

    def test_defaults_are_injected(self) -> None:
        config = {"factors": {"pe": {"weight": 1.0, "direction": "low"}}}
        migrate_strategy_config(config)
        self.assertIn("factor_processing", config)
        self.assertIn("portfolio_controls", config)
        self.assertIn("performance", config)
        self.assertEqual(config["portfolio_controls"]["max_industry_weight"], 0.30)


if __name__ == "__main__":
    unittest.main()
