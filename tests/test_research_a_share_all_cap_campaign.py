from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.config import load_config
from stock_analyze.research.a_share_all_cap_campaign import (
    TRIAL_IDS,
    CampaignInputs,
    build_trial_evaluations,
    replay_next_open,
    run_development_campaign,
)
from stock_analyze.research.a_share_all_cap_contract import (
    load_all_cap_contract,
    parse_all_cap_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "configs/research/a_share_all_cap_v2.yaml"


def _evaluation_rows(periods: int = 150) -> pd.DataFrame:
    dates = pd.bdate_range("2018-01-02", periods=periods)
    rows: list[dict[str, object]] = []
    for day_index, day in enumerate(dates):
        trade_date = day.strftime("%Y%m%d")
        entry_date = (day + pd.offsets.BDay(1)).strftime("%Y%m%d")
        label_end_date = (day + pd.offsets.BDay(2)).strftime("%Y%m%d")
        for sleeve_index, sleeve in enumerate(("large", "mid", "small", "micro")):
            for code_index in range(2):
                code = f"{sleeve_index * 10 + code_index + 1:06d}"
                entry_price = 10.0 + code_index + day_index * 0.01
                rows.append(
                    {
                        "trade_date": trade_date,
                        "entry_date": entry_date,
                        "label_end_date": label_end_date,
                        "code": code,
                        "stable_sleeve": sleeve,
                        "industry": "bank" if code_index == 0 else "tech",
                        "total_mv": 2_000.0 - code_index * 500.0,
                        "official_weight": 0.7 if code_index == 0 else 0.3,
                        "legacy_eligible": sleeve in {"large", "mid"},
                        "entry_price": entry_price,
                        "entry_open": entry_price,
                        "entry_up_limit": entry_price + 1.0,
                        "entry_down_limit": entry_price - 1.0,
                        "entry_status_complete": True,
                        "entry_status_conflict": False,
                        "entry_suspended": False,
                        "benchmark_entry_price": 100.0 + day_index * 0.02,
                        "avg_amount_20": 100_000_000.0,
                        "realized_volatility_20": 0.20,
                        "return_1": 0.001,
                        "pe": 10.0 + code_index,
                        "pb": 1.0 + code_index,
                        "roe": 0.12 - code_index * 0.01,
                        "debt_ratio": 0.40 + code_index * 0.05,
                        "low_volatility_60": 0.15 + code_index * 0.02,
                        "dividend_yield": 0.03 - code_index * 0.005,
                        "momentum_20": 0.05 - code_index * 0.01,
                        "momentum_60": 0.08 - code_index * 0.01,
                        "net_profit_growth": 0.10 - code_index * 0.01,
                        "gross_margin": 0.30 - code_index * 0.01,
                    }
                )
    return pd.DataFrame(rows)


def _inputs(frame: pd.DataFrame | None = None) -> CampaignInputs:
    return CampaignInputs(
        evaluation=_evaluation_rows() if frame is None else frame,
        portfolio_contract={
            "initial_cash": 1_000_000.0,
            "top_n": 2,
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.0005,
                "slippage_rate": 0.0005,
                "max_single_weight": 0.50,
            },
            "allocation_policy": {
                "version": "benchmark-aware-topn-v1",
                "risk_aversion": 1.0,
                "cost_aversion": 1.0,
                "max_rebalance_turnover": 0.25,
            },
        },
        repo_root=REPO_ROOT,
    )


class AShareAllCapCampaignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_all_cap_contract(CONTRACT_PATH)

    def test_declares_only_the_six_frozen_trial_ids(self) -> None:
        self.assertEqual(
            TRIAL_IDS,
            (
                "official_sleeve_index",
                "pit_sleeve_cap_weight",
                "pit_sleeve_equal_weight",
                "legacy_transparent_scope",
                "sleeve_router_only",
                "all_cap_v2",
            ),
        )

    def test_rejects_drift_in_frozen_baseline_set(self) -> None:
        payload = load_config(CONTRACT_PATH, apply_migrations=False)
        payload["baselines"] = payload["baselines"][:-1]
        changed = parse_all_cap_contract(payload)

        with self.assertRaisesRegex(
            ValueError,
            "all_cap_campaign_trial_set",
        ):
            build_trial_evaluations(_inputs(), changed)

    def test_trial_frames_share_rows_dates_costs_and_four_purged_folds(self) -> None:
        prepared = build_trial_evaluations(_inputs(), self.contract)

        self.assertEqual(set(prepared), set(TRIAL_IDS))
        self.assertEqual(len({trial.row_keys for trial in prepared.values()}), 1)
        self.assertEqual(
            len({trial.evaluation_dates for trial in prepared.values()}),
            1,
        )
        self.assertEqual(
            len({trial.cost_signature for trial in prepared.values()}),
            1,
        )
        self.assertEqual({len(trial.folds) for trial in prepared.values()}, {4})
        for trial in prepared.values():
            for fold in trial.folds:
                self.assertLess(fold.train_label_end, fold.validation_start)
            self.assertTrue(
                trial.evaluation["trade_date"].astype(str).le("20241231").all()
            )

    def test_all_cap_v2_keeps_overlay_weights_and_scores_within_sleeve(self) -> None:
        candidate = build_trial_evaluations(
            _inputs(),
            self.contract,
        )["all_cap_v2"]

        self.assertEqual(
            candidate.factor_weights["claude"],
            {
                "pe": 0.25,
                "pb": 0.15,
                "roe": 0.20,
                "debt_ratio": 0.10,
                "low_volatility_60": 0.15,
                "dividend_yield": 0.15,
            },
        )
        self.assertEqual(
            candidate.factor_weights["codex"],
            {
                "momentum_20": 0.27,
                "momentum_60": 0.25,
                "net_profit_growth": 0.18,
                "roe": 0.13,
                "gross_margin": 0.07,
                "low_volatility_60": 0.05,
                "debt_ratio": 0.05,
            },
        )
        for weights in candidate.factor_weights.values():
            self.assertAlmostEqual(sum(weights.values()), 1.0)
        grouped = candidate.evaluation.groupby(
            ["agent", "stable_sleeve", "trade_date"],
            sort=False,
        )
        self.assertTrue(all(group["score"].notna().all() for _, group in grouped))

    def test_frozen_decision_intervals_are_used_for_every_trial(self) -> None:
        prepared = build_trial_evaluations(_inputs(), self.contract)
        candidate = prepared["all_cap_v2"].evaluation
        counts = candidate.groupby(["agent", "stable_sleeve"])[
            "trade_date"
        ].nunique()

        self.assertEqual(counts[("claude", "large")], 8)
        self.assertEqual(counts[("claude", "micro")], 8)
        self.assertEqual(counts[("codex", "large")], 30)
        self.assertEqual(counts[("codex", "small")], 15)
        self.assertEqual(counts[("codex", "micro")], 8)
        expected_rows = candidate[
            ["agent", "stable_sleeve", "trade_date", "code"]
        ].to_records(index=False).tolist()
        for trial in prepared.values():
            self.assertEqual(
                trial.evaluation[
                    ["agent", "stable_sleeve", "trade_date", "code"]
                ].to_records(index=False).tolist(),
                expected_rows,
            )

    def test_liquidity_cap_uses_each_sleeves_fixed_capital(self) -> None:
        frame = _evaluation_rows()
        frame["avg_amount_20"] = 1_000_000.0

        candidate = build_trial_evaluations(
            _inputs(frame),
            self.contract,
        )["all_cap_v2"].evaluation

        expected = {
            "large": 1_000_000.0 * 0.02 / 350_000.0,
            "mid": 1_000_000.0 * 0.02 / 300_000.0,
            "small": 1_000_000.0 * 0.02 / 250_000.0,
            "micro": 1_000_000.0 * 0.02 / 100_000.0,
        }
        for sleeve, cap in expected.items():
            observed = candidate.loc[
                candidate["stable_sleeve"].eq(sleeve),
                "liquidity_cap",
            ]
            self.assertTrue(np.isclose(observed, cap).all())

    def test_development_rejects_any_2025_or_later_observation_before_replay(
        self,
    ) -> None:
        frame = _evaluation_rows()
        frame.loc[0, "entry_date"] = "20250102"

        with patch(
            "stock_analyze.research.a_share_all_cap_campaign.replay_rule_portfolio"
        ) as replay:
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_campaign_development_window",
            ):
                run_development_campaign(_inputs(frame), self.contract)

        replay.assert_not_called()

    def test_campaign_reuses_portfolio_replay_for_every_trial_account(self) -> None:
        prepared = build_trial_evaluations(_inputs(), self.contract)

        class StubReplay:
            metrics = {"simulator_version": "paper-parity-daily-v1"}
            periods = pd.DataFrame()
            trades = pd.DataFrame()
            nav = pd.DataFrame()
            decisions = pd.DataFrame()

        with patch(
            "stock_analyze.research.a_share_all_cap_campaign.replay_rule_portfolio",
            return_value=StubReplay(),
        ) as replay:
            result = run_development_campaign(_inputs(), self.contract)

        expected_calls = sum(
            trial.evaluation["account_id"].nunique()
            for trial in prepared.values()
        )
        self.assertEqual(replay.call_count, expected_calls)
        self.assertEqual(set(result.trials), set(TRIAL_IDS))
        self.assertTrue(all(trial.replays for trial in result.trials.values()))

    def test_campaign_composes_the_existing_target_weight_optimizer(self) -> None:
        from stock_analyze.research import portfolio_replay

        with patch.object(
            portfolio_replay,
            "risk_adjusted_target_weights",
            wraps=portfolio_replay.risk_adjusted_target_weights,
        ) as optimizer:
            run_development_campaign(_inputs(), self.contract)

        self.assertTrue(optimizer.call_args_list)
        self.assertTrue(
            any(
                call.kwargs["max_turnover"] == 0.25
                for call in optimizer.call_args_list
            )
        )

    def test_limit_locked_and_suspended_open_never_fill(self) -> None:
        base = {
            "side": "buy",
            "requested_shares": 1_000,
            "entry_open": 10.0,
            "up_limit": 10.0,
            "down_limit": 9.0,
            "status_complete": True,
            "status_conflict": False,
            "suspended": False,
            "trade_date": "20240103",
            "avg_daily_amount": 10_000_000.0,
            "volatility": 0.20,
            "trading": _inputs().portfolio_contract["trading"],
        }

        locked = replay_next_open(base)
        suspended = replay_next_open(
            {**base, "up_limit": 11.0, "suspended": True}
        )

        self.assertEqual((locked.filled_shares, locked.status), (0, "limit_locked"))
        self.assertEqual((suspended.filled_shares, suspended.status), (0, "suspended"))

    def test_next_open_fill_applies_lot_costs_impact_and_base_adv_cap(self) -> None:
        result = replay_next_open(
            {
                "side": "sell",
                "requested_shares": 10_050,
                "available_shares": 10_050,
                "entry_open": 10.0,
                "up_limit": 11.0,
                "down_limit": 9.0,
                "status_complete": True,
                "status_conflict": False,
                "suspended": False,
                "trade_date": "20240103",
                "acquired_date": "20240102",
                "avg_daily_amount": 1_000_000.0,
                "volatility": 0.20,
                "trading": _inputs().portfolio_contract["trading"],
                "base_max_adv_fraction": 0.02,
                "hard_max_adv_fraction": 0.05,
            }
        )

        self.assertEqual(result.status, "partial_participation_fill")
        self.assertEqual(result.filled_shares, 2_000)
        self.assertEqual(result.filled_shares % 100, 0)
        self.assertLessEqual(result.participation_rate, 0.02)
        self.assertGreater(result.commission, 0.0)
        self.assertGreater(result.stamp_tax, 0.0)
        self.assertGreater(result.slippage, 0.0)
        self.assertGreater(result.impact_bps, 5.0)

    def test_next_open_fill_enforces_t_plus_one_and_missing_inputs(self) -> None:
        order = {
            "side": "sell",
            "requested_shares": 1_000,
            "available_shares": 1_000,
            "entry_open": 10.0,
            "up_limit": 11.0,
            "down_limit": 9.0,
            "status_complete": True,
            "status_conflict": False,
            "suspended": False,
            "trade_date": "20240103",
            "acquired_date": "20240103",
            "avg_daily_amount": 10_000_000.0,
            "volatility": 0.20,
            "trading": _inputs().portfolio_contract["trading"],
        }

        same_day = replay_next_open(order)
        missing_status = replay_next_open(
            {**order, "acquired_date": "20240102", "status_complete": None}
        )
        missing_liquidity = replay_next_open(
            {
                **order,
                "side": "buy",
                "acquired_date": None,
                "avg_daily_amount": None,
            }
        )

        self.assertEqual(same_day.status, "t_plus_one")
        self.assertEqual(missing_status.status, "missing_critical_input")
        self.assertEqual(missing_liquidity.status, "missing_liquidity")
        self.assertEqual(missing_liquidity.filled_shares, 0)


if __name__ == "__main__":
    unittest.main()
