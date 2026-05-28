from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.overlay_guard import (
    AVAILABLE_FACTORS,
    OverlayBaselineLocked,
    OverlayInvalidDirection,
    OverlayInvalidWeight,
    OverlayInvalidYAML,
    OverlaySchemaError,
    OverlayUnknownFactor,
    OverlayUnknownTopLevelKey,
    validate,
)


BASELINE_CONFIG = {
    "competition_id": "test_competition",
    "version": 1,
    "start_date": "2026-05-26",
    "initial_cash": 1000000,
    "accounts": [
        {"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500000, "top_n": 50},
    ],
    "schedule": {
        "rebalance": "weekly_after_close",
        "signal_day": "last_trading_day_of_week",
        "execution": "next_trading_day_open",
    },
    "trading": {
        "lot_size": 100,
        "commission_rate": 0.0003,
        "min_commission": 5,
        "stamp_tax_rate": 0.0005,
        "slippage_rate": 0.0005,
        "max_single_weight": 0.06,
    },
    "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
}


def _seed_repo(root: Path) -> None:
    (root / "configs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "competition_a_share.yaml").write_text(
        json.dumps(BASELINE_CONFIG), encoding="utf-8"
    )


class OverlayGuardHappyPathTests(unittest.TestCase):
    def test_valid_overlay_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "claude",
                "strategy_id": "claude_v1",
                "name": "Claude · Test",
                "factors": {
                    "pe": {"weight": 0.20, "direction": "low"},
                    "roe": {"weight": 0.80, "direction": "high"},
                },
                "factor_processing": {"enabled": True},
                "portfolio_controls": {"max_industry_weight": 0.30},
                "filters": {"exclude_st": True},
            }
            self.assertIsNone(validate("claude", overlay, repo_root=root))

    def test_extreme_but_valid_weight_passes(self) -> None:
        """Per design §1: guard does not judge strategy aggressiveness."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "claude",
                "factors": {
                    "pe": {"weight": 0.95, "direction": "low"},
                    "roe": {"weight": 0.05, "direction": "high"},
                },
            }
            self.assertIsNone(validate("claude", overlay, repo_root=root))


class OverlayGuardRaiseTests(unittest.TestCase):
    def test_unknown_top_level_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "claude",
                "rogue_field": {"hi": "no"},
                "factors": {"pe": {"weight": 0.1}},
            }
            with self.assertRaises(OverlayUnknownTopLevelKey) as ctx:
                validate("claude", overlay, repo_root=root)
            self.assertIn("rogue_field", str(ctx.exception))

    def test_baseline_locked_field_raises(self) -> None:
        """`accounts` itself is not in the overlay top-level whitelist, so the
        guard rejects it as unknown-top-level. The deeper baseline-lock check
        is exercised by calling the internal helper directly with a baseline.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            from stock_analyze.overlay_guard import _validate_baseline_locks
            offending = {"accounts": [{"id": "hs300", "cash": 600000}]}
            with self.assertRaises(OverlayBaselineLocked) as ctx:
                _validate_baseline_locks(offending, repo_root=root, baseline=BASELINE_CONFIG)
            self.assertIn("accounts.hs300.cash", str(ctx.exception))

    def test_unknown_factor_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "claude",
                "factors": {"magic_alpha": {"weight": 0.10, "direction": "high"}},
            }
            with self.assertRaises(OverlayUnknownFactor) as ctx:
                validate("claude", overlay, repo_root=root)
            self.assertIn("magic_alpha", str(ctx.exception))
            self.assertIn("pe", str(ctx.exception))  # whitelist printed

    def test_factor_weight_above_one_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "claude",
                "factors": {"pe": {"weight": 1.5, "direction": "low"}},
            }
            with self.assertRaises(OverlayInvalidWeight) as ctx:
                validate("claude", overlay, repo_root=root)
            self.assertIn("pe", str(ctx.exception))

    def test_factor_weight_negative_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "claude",
                "factors": {"pe": {"weight": -0.01, "direction": "low"}},
            }
            with self.assertRaises(OverlayInvalidWeight):
                validate("claude", overlay, repo_root=root)

    def test_factor_weight_non_numeric_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "claude",
                "factors": {"pe": {"weight": "not_a_number", "direction": "low"}},
            }
            with self.assertRaises(OverlayInvalidWeight):
                validate("claude", overlay, repo_root=root)

    def test_factor_direction_must_be_high_or_low(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "claude",
                "factors": {"pe": {"weight": 0.1, "direction": "lower_is_better"}},
            }
            with self.assertRaises(OverlayInvalidDirection) as ctx:
                validate("claude", overlay, repo_root=root)
            self.assertIn("pe", str(ctx.exception))

    def test_invalid_yaml_string_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            broken = "{not: even-json"
            with self.assertRaises(OverlayInvalidYAML):
                validate("claude", broken, repo_root=root)

    def test_factors_must_be_mapping_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "claude",
                "factors": ["pe", "roe"],  # list, not dict — schema error
            }
            with self.assertRaises(OverlaySchemaError):
                validate("claude", overlay, repo_root=root)

    def test_agent_id_mismatch_raises_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            overlay = {
                "agent_id": "codex",
                "factors": {"pe": {"weight": 0.1}},
            }
            with self.assertRaises(OverlaySchemaError):
                validate("claude", overlay, repo_root=root)


class OverlayGuardWhitelistContractTests(unittest.TestCase):
    def test_known_factor_names_present(self) -> None:
        # Spec: factors visible to the LLM must match what data_provider
        # actually produces. Keep this list in sync with proposal_judge's
        # historical KNOWN_FACTORS.
        for name in [
            "pe", "pb", "roe", "gross_margin", "debt_ratio",
            "net_profit_growth", "momentum_20", "momentum_60",
            "low_volatility_60", "dividend_yield",
        ]:
            self.assertIn(name, AVAILABLE_FACTORS)


if __name__ == "__main__":
    unittest.main()
