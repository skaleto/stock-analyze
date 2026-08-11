from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.markets.a_share.simulator import (
    _controlled_candidate_pool,
    build_target_orders,
    generate_rebalance_orders as generate_a_share_orders,
)
from stock_analyze.markets.cn_qdii_etf.run import (
    generate_rebalance_orders as generate_qdii_orders,
)
from stock_analyze.markets.cn_qdii_etf.simulator import initialize as initialize_qdii
from stock_analyze.store import PortfolioStore


def _correlated_returns(codes: list[str]) -> pd.DataFrame:
    periods = 80
    shared = np.sin(np.arange(periods) / 4.0) * 0.02
    diversifier = np.cos(np.arange(periods) / 5.0) * 0.008
    independent = [
        np.sin(np.arange(periods) / divisor) * (0.009 + index * 0.0005)
        for index, divisor in enumerate((7.0, 9.0, 11.0))
    ]
    columns = {
        codes[0]: shared,
        codes[1]: shared * 0.99,
        codes[2]: diversifier,
    }
    columns.update(
        {
            code: values
            for code, values in zip(codes[3:], independent)
        }
    )
    return pd.DataFrame(columns)


class _PortfolioProvider:
    def __init__(self, returns: pd.DataFrame, price: float = 10.0) -> None:
        self.returns = returns
        self.price = price

    def next_trading_day(self, value: str) -> str:
        return value

    def return_history(self, codes, *, as_of: str, days: int) -> pd.DataFrame:
        return self.returns.reindex(columns=list(codes)).tail(days)

    def price_snapshot(self, code: str, as_of: str | None = None):
        return SimpleNamespace(
            code=code,
            name=code,
            close=self.price,
            avg_amount_20=100_000_000.0,
        )


def _a_share_config() -> dict:
    return {
        "agent_id": "codex",
        "strategy_id": "trend",
        "accounts": [
            {
                "id": "acc",
                "scope": "custom:test",
                "benchmark": "000300",
                "cash": 100_000.0,
                "top_n": 2,
            }
        ],
        "trading": {
            "lot_size": 100,
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.0005,
            "slippage_rate": 0.0,
            "max_single_weight": 0.70,
        },
        "portfolio_controls": {
            "max_industry_weight": 1.0,
            "turnover_penalty": 0.0,
            "min_trade_weight": 0.0,
            "max_turnover": 1.0,
            "candidate_pool_multiple": 3,
            "max_liquidity_participation": 0.05,
        },
        "factors": {},
    }


def _a_share_candidates() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": f"{index:06d}",
                "name": f"A{index}",
                "latest_price": 10.0,
                "score": 1.02 - index * 0.02,
                "low_volatility_60": 0.20,
                "industry": "科技",
                "avg_amount_20": 100_000_000.0,
            }
            for index in range(1, 7)
        ]
    )


def _qdii_config() -> dict:
    return {
        "agent_id": "codex",
        "strategy_id": "trend",
        "competition_id": "portfolio-contract",
        "accounts": [
            {
                "id": "global",
                "scope": "global",
                "benchmark": "513100.SH",
                "cash": 100_000.0,
                "top_n": 2,
            }
        ],
        "trading": {"max_single_weight": 0.70},
        "portfolio_controls": {
            "turnover_penalty": 0.0,
            "min_trade_weight": 0.0,
            "max_turnover": 1.0,
            "candidate_pool_multiple": 3,
            "max_liquidity_participation": 0.05,
            "max_index_weight": 1.0,
            "max_country_weight": 1.0,
        },
    }


def _qdii_candidates() -> list[dict]:
    codes = [
        "513100.SH",
        "513500.SH",
        "159941.SZ",
        "513300.SH",
        "513650.SH",
        "159920.SZ",
    ]
    return [
        {
            "code": code,
            "name": code,
            "account_id": "global",
            "score": 1.02 - index * 0.02,
            "low_volatility_60": 0.20,
            "avg_amount_20": 100_000_000.0,
            "index_key": f"index-{index}",
            "country": "美国",
        }
        for index, code in enumerate(codes, start=1)
    ]


class PortfolioDecisionContractTests(unittest.TestCase):
    def test_active_model_candidate_pool_preserves_rule_core_head(self) -> None:
        scored = pd.DataFrame([
            {
                "code": f"{index:06d}",
                "score": float(9 - index),
                "base_score": float(index),
                "prediction_applied": True,
            }
            for index in range(1, 9)
        ])
        config = _a_share_config()

        pool, _ = _controlled_candidate_pool(
            scored,
            {"positions": {}},
            config,
            top_n=1,
        )

        self.assertTrue({"000001", "000002", "000003"}.issubset(set(pool["code"])))
        self.assertTrue({"000006", "000007", "000008"}.issubset(set(pool["code"])))

    def test_a_share_formal_path_jointly_selects_from_three_x_pool(self) -> None:
        config = _a_share_config()
        candidates = _a_share_candidates()
        returns = _correlated_returns(candidates["code"].tolist())
        factor_table = candidates[["code"]].assign(
            factor="momentum_20",
            contribution=0.1,
            valid=True,
        )
        candidates = candidates.assign(
            prediction_applied=True,
            prediction_confidence=0.90,
            expected_excess_return=0.04,
            prediction_horizons="3,5",
            prediction_model_versions="ranker-3-v2,ranker-5-v4",
            prediction_fallback_reason="",
        )
        signal = SimpleNamespace(
            candidates=candidates,
            factor_table=factor_table,
            warnings=[],
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            store.initialize(config)
            with patch(
                "stock_analyze.markets.a_share.simulator.build_signals",
                return_value=signal,
            ):
                batches = generate_a_share_orders(
                    config,
                    store,
                    _PortfolioProvider(returns),
                    as_of="2026-07-24",
                    run_id="a-contract",
                )
            factor_snapshot = store.read_factor_run("a-contract")

        batch = batches[0]
        selected = set(batch["selected_codes"])
        self.assertEqual(batch["optimizer_diagnostics"]["candidate_pool_size"], 6)
        self.assertEqual(len(selected), 2)
        self.assertIn("000003", selected)
        self.assertFalse({"000001", "000002"}.issubset(selected))
        self.assertTrue(
            {order["code"] for order in batch["orders"]}
            .issubset(set(batch["target_weights"]))
        )
        self.assertTrue(factor_snapshot["prediction_applied"].fillna(False).all())
        self.assertEqual(
            set(factor_snapshot["prediction_model_versions"].dropna()),
            {"ranker-3-v2,ranker-5-v4"},
        )
        risk = batch["optimizer_diagnostics"]["risk_contributions"]
        self.assertAlmostEqual(
            sum(float(value) for value in risk.values()),
            float(batch["optimizer_diagnostics"]["volatility"]),
            places=7,
        )

    def test_a_share_order_materialization_obeys_max_positions_and_turnover(self) -> None:
        config = _a_share_config()
        config["portfolio_controls"]["max_turnover"] = 0.40
        diagnostics: dict[str, object] = {}
        candidates = _a_share_candidates().iloc[:3].copy()
        returns = _correlated_returns(candidates["code"].tolist())

        orders = build_target_orders(
            config,
            {"cash": 100_000.0, "positions": {}},
            candidates,
            max_positions=2,
            return_history=returns,
            optimizer_diagnostics=diagnostics,
        )

        self.assertLessEqual(len(diagnostics["selected_codes"]), 2)
        self.assertLessEqual(float(diagnostics["turnover"]), 0.40 + 1e-8)
        self.assertTrue(
            {order["code"] for order in orders}
            .issubset(set(diagnostics["target_weights"]))
        )
        self.assertEqual(
            set(diagnostics["selected_codes"]),
            {
                code
                for code, weight in diagnostics["target_weights"].items()
                if float(weight) > 0.0
            },
        )

    def test_a_share_filtered_holding_remains_inside_optimizer_problem(self) -> None:
        config = _a_share_config()
        config["portfolio_controls"]["max_turnover"] = 0.20
        candidates = _a_share_candidates()
        returns = _correlated_returns(
            [*candidates["code"].tolist(), "000099"]
        )
        signal = SimpleNamespace(
            candidates=candidates,
            factor_table=pd.DataFrame(),
            warnings=[],
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            state = store.initialize(config)
            state["accounts"]["acc"]["cash"] = 90_000.0
            state["accounts"]["acc"]["positions"] = {
                "000099": {
                    "name": "filtered holding",
                    "shares": 1_000,
                    "available_shares": 1_000,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "market_value": 10_000.0,
                    "industry": "其他",
                }
            }
            store.save_state(state)
            with patch(
                "stock_analyze.markets.a_share.simulator.build_signals",
                return_value=signal,
            ):
                batch = generate_a_share_orders(
                    config,
                    store,
                    _PortfolioProvider(returns),
                    as_of="2026-07-24",
                )[0]

        diagnostics = batch["optimizer_diagnostics"]
        self.assertEqual(diagnostics["candidate_pool_size"], 7)
        self.assertIn("000099", diagnostics["liquidity_caps"])
        self.assertIn("000099", diagnostics["risk_contributions"])
        self.assertLessEqual(float(diagnostics["turnover"]), 0.20 + 1e-8)
        self.assertTrue(
            {order["code"] for order in batch["orders"]}
            .issubset(set(batch["target_weights"]))
        )

    def test_a_share_empty_signal_still_models_existing_holding(self) -> None:
        config = _a_share_config()
        signal = SimpleNamespace(
            candidates=pd.DataFrame(),
            factor_table=pd.DataFrame(),
            warnings=[],
        )
        returns = pd.DataFrame(
            {"000099": np.sin(np.arange(80) / 4.0) * 0.01}
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            state = store.initialize(config)
            state["accounts"]["acc"]["cash"] = 90_000.0
            state["accounts"]["acc"]["positions"] = {
                "000099": {
                    "name": "existing holding",
                    "shares": 1_000,
                    "available_shares": 1_000,
                    "avg_cost": 10.0,
                    "last_price": 10.0,
                    "market_value": 10_000.0,
                    "industry": "其他",
                }
            }
            store.save_state(state)
            with patch(
                "stock_analyze.markets.a_share.simulator.build_signals",
                return_value=signal,
            ):
                batch = generate_a_share_orders(
                    config,
                    store,
                    _PortfolioProvider(returns),
                    as_of="2026-07-24",
                )[0]

        diagnostics = batch["optimizer_diagnostics"]
        self.assertEqual(diagnostics["candidate_pool_size"], 1)
        self.assertIn("000099", diagnostics["liquidity_caps"])
        self.assertIn("000099", diagnostics["risk_contributions"])

    def test_qdii_formal_path_persists_allocation_and_optimizer_diagnostics(self) -> None:
        config = _qdii_config()
        candidates = _qdii_candidates()
        returns = _correlated_returns([row["code"] for row in candidates])

        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize_qdii(config, store)
            with patch(
                "stock_analyze.markets.cn_qdii_etf.run.build_signals",
                return_value=candidates,
            ):
                orders = generate_qdii_orders(
                    config,
                    store,
                    _PortfolioProvider(returns, price=2.0),
                    as_of=date(2026, 7, 24),
                    run_id="qdii-contract",
                )
            snapshot = json.loads(
                (store.data_dir / "selection_snapshot.json").read_text(
                    encoding="utf-8"
                )
            )
            pending = store.read_pending()

        scope = snapshot["scopes"]["global"]
        selected_codes = set(scope["selected_codes"])
        self.assertEqual(scope["optimizer_diagnostics"]["candidate_pool_size"], 6)
        self.assertEqual(len(selected_codes), 2)
        self.assertIn("159941.SZ", selected_codes)
        self.assertFalse({"513100.SH", "513500.SH"}.issubset(selected_codes))
        self.assertEqual(
            selected_codes,
            {
                code
                for code, weight in scope["target_weights"].items()
                if float(weight) > 0.0
            },
        )
        self.assertTrue({order["code"] for order in orders}.issubset(selected_codes))
        for order in pending:
            self.assertEqual(set(order["allocation_selected_codes"]), selected_codes)
            self.assertEqual(order["allocation_target_weights"], scope["target_weights"])
            self.assertIn("stress_losses", order["optimizer_diagnostics"])
        self.assertLessEqual(
            sum(float(value) for value in scope["target_weights"].values()),
            1.0 + 1e-8,
        )
        self.assertLessEqual(
            max(float(value) for value in scope["target_weights"].values()),
            config["trading"]["max_single_weight"] + 1e-8,
        )
        self.assertGreaterEqual(
            float(scope["optimizer_diagnostics"]["cash_weight"]),
            0.02 - 1e-8,
        )
        self.assertAlmostEqual(
            sum(float(value) for value in scope["target_weights"].values())
            + float(scope["optimizer_diagnostics"]["cash_weight"]),
            1.0,
            places=7,
        )

    def test_qdii_filtered_holding_remains_inside_optimizer_problem(self) -> None:
        config = _qdii_config()
        candidates = _qdii_candidates()
        holding = "513900.SH"
        returns = _correlated_returns(
            [row["code"] for row in candidates] + [holding]
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            state = initialize_qdii(config, store)
            state["accounts"]["global"]["cash"] = 90_000.0
            state["accounts"]["global"]["positions"] = {
                holding: {
                    "name": "filtered holding",
                    "shares": 5_000,
                    "avg_cost": 2.0,
                    "last_price": 2.0,
                    "avg_amount_20": 100_000_000.0,
                }
            }
            store.save_state(state)
            with patch(
                "stock_analyze.markets.cn_qdii_etf.run.build_signals",
                return_value=candidates,
            ):
                generate_qdii_orders(
                    config,
                    store,
                    _PortfolioProvider(returns, price=2.0),
                    as_of=date(2026, 7, 24),
                )
            snapshot = json.loads(
                (store.data_dir / "selection_snapshot.json").read_text(
                    encoding="utf-8"
                )
            )

        diagnostics = snapshot["scopes"]["global"]["optimizer_diagnostics"]
        self.assertEqual(diagnostics["candidate_pool_size"], 7)
        self.assertIn(holding, diagnostics["liquidity_caps"])
        self.assertIn(holding, diagnostics["risk_contributions"])


if __name__ == "__main__":
    unittest.main()
