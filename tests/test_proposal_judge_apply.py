from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze import competition
from stock_analyze.config import config_hash
from stock_analyze.proposal_apply import apply_approved_proposals, rollback_agent
from stock_analyze.proposal_judge import (
    DECISION_APPROVED,
    DECISION_NEEDS_HUMAN,
    DECISION_REJECTED,
    decision_path,
    judge_proposal,
)


BASELINE_CONFIG = {
    "competition_id": "test_competition",
    "version": 1,
    "start_date": "2026-05-26",
    "initial_cash": 1000000,
    "accounts": [
        {"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500000, "top_n": 50},
    ],
    "schedule": {"rebalance": "weekly_after_close", "signal_day": "last_trading_day_of_week", "execution": "next_trading_day_open"},
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
    (root / "configs" / "competition.yaml").write_text(json.dumps(BASELINE_CONFIG), encoding="utf-8")
    overlay = {
        "agent_id": "codex",
        "strategy_id": "codex_test",
        "factors": {
            "pe": {"weight": 0.30, "direction": "low"},
            "roe": {"weight": 0.35, "direction": "high"},
            "pb": {"weight": 0.35, "direction": "low"},
        },
        "factor_processing": {"enabled": True, "winsorize_lower": 0.01, "winsorize_upper": 0.99},
        "portfolio_controls": {"max_industry_weight": 0.30},
        "filters": {"min_avg_amount_20": 50000000},
    }
    (root / "configs" / "agents" / "codex.yaml").write_text(json.dumps(overlay), encoding="utf-8")
    (root / "data" / "codex" / "proposals").mkdir(parents=True, exist_ok=True)
    (root / "data" / "competition" / "monthly_reviews").mkdir(parents=True, exist_ok=True)
    (root / "data" / "competition" / "monthly_reviews" / "2026-06.json").write_text(
        json.dumps({"review_period": "2026-06", "agents": {"codex": {"nav_points": 40}}}),
        encoding="utf-8",
    )


def _write_proposal(root: Path, payload: dict) -> Path:
    path = root / "data" / "codex" / "proposals" / "2026-06-strategy.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class ProposalJudgeTests(unittest.TestCase):
    def test_small_data_backed_patch_is_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            current_hash = config_hash(competition.load("codex", repo_root=root))
            _write_proposal(
                root,
                {
                    "agent_id": "codex",
                    "based_on_config_hash": current_hash,
                    "proposed_at": "2026-07-01",
                    "rationale": "最近月度低估值与 ROE 表现稳定，因此只做小幅权重微调。",
                    "expected_effect": "保持收益同时控制风格漂移。",
                    "risks": ["样本仍短，继续观察"],
                    "no_change": False,
                    "patch": {
                        "factors": {
                            "pe": {"weight": 0.33, "direction": "low"},
                            "roe": {"weight": 0.32, "direction": "high"},
                        }
                    },
                },
            )
            result = judge_proposal("codex", "2026-06", repo_root=root)
            self.assertEqual(result["decision"], DECISION_APPROVED)
            self.assertTrue(decision_path("codex", "2026-06", root).exists())

    def test_locked_or_unknown_patch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _write_proposal(
                root,
                {
                    "agent_id": "codex",
                    "rationale": "试图改本金是不允许的。",
                    "expected_effect": "提高收益",
                    "risks": ["破坏公平性"],
                    "no_change": False,
                    "patch": {"initial_cash": 2000000},
                },
            )
            result = judge_proposal("codex", "2026-06", repo_root=root)
            self.assertEqual(result["decision"], DECISION_REJECTED)
            self.assertIn("patch_top_level_not_allowed:initial_cash", result["violations"])

    def test_known_new_factor_can_be_added_cautiously(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            current_hash = config_hash(competition.load("codex", repo_root=root))
            _write_proposal(
                root,
                {
                    "agent_id": "codex",
                    "based_on_config_hash": current_hash,
                    "rationale": "月度报告显示利润增长因子有观察价值，因此只小幅加入。",
                    "expected_effect": "增加成长暴露但保持原有价值质量框架。",
                    "risks": ["增长数据覆盖率可能不足"],
                    "no_change": False,
                    "patch": {
                        "factors": {
                            "pb": {"weight": 0.32},
                            "net_profit_growth": {"weight": 0.03, "direction": "high"},
                        }
                    },
                },
            )
            result = judge_proposal("codex", "2026-06", repo_root=root)
            self.assertEqual(result["decision"], DECISION_APPROVED)

    def test_unknown_factor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _write_proposal(
                root,
                {
                    "agent_id": "codex",
                    "rationale": "这个因子在代码里不存在，不能直接加入。",
                    "expected_effect": "提高收益",
                    "risks": ["执行时无数据"],
                    "no_change": False,
                    "patch": {"factors": {"magic_alpha": {"weight": 0.03, "direction": "high"}}},
                },
            )
            result = judge_proposal("codex", "2026-06", repo_root=root)
            self.assertEqual(result["decision"], DECISION_REJECTED)
            self.assertIn("unknown_factor:magic_alpha", result["violations"])

    def test_large_weight_change_needs_human(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _write_proposal(
                root,
                {
                    "agent_id": "codex",
                    "rationale": "最近一个月 ROE 看起来比较强，但这个调整幅度偏大。",
                    "expected_effect": "提高收益",
                    "risks": ["可能过拟合"],
                    "no_change": False,
                    "patch": {
                        "factors": {
                            "pe": {"weight": 0.2, "direction": "low"},
                            "roe": {"weight": 0.8, "direction": "high"},
                        }
                    },
                },
            )
            result = judge_proposal("codex", "2026-06", repo_root=root)
            self.assertEqual(result["decision"], DECISION_NEEDS_HUMAN)
            self.assertTrue(any("factor_weight_delta_too_large" in item for item in result["warnings"]))


class ProposalApplyTests(unittest.TestCase):
    def test_apply_approved_patch_archives_and_rollback_restores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            from_hash = config_hash(competition.load("codex", repo_root=root))
            _write_proposal(
                root,
                {
                    "agent_id": "codex",
                    "based_on_config_hash": from_hash,
                    "proposed_at": "2026-07-01",
                    "rationale": "最近月度低估值与 ROE 表现稳定，因此只做小幅权重微调。",
                    "expected_effect": "保持收益同时控制风格漂移。",
                    "risks": ["样本仍短"],
                    "no_change": False,
                    "patch": {
                        "factors": {
                            "pe": {"weight": 0.33, "direction": "low"},
                            "roe": {"weight": 0.32, "direction": "high"},
                        }
                    },
                },
            )
            judge_proposal("codex", "2026-06", repo_root=root)
            results = apply_approved_proposals(month="2026-06", agents=["codex"], repo_root=root)
            self.assertEqual(results[0]["status"], "applied")
            merged = competition.load("codex", repo_root=root)
            self.assertAlmostEqual(merged["factors"]["pe"]["weight"], 0.33)
            self.assertTrue((root / "configs" / "agents" / "_history" / f"{from_hash}.yaml").exists())
            self.assertTrue((root / "data" / "codex" / "config_evolution.csv").exists())

            rollback = rollback_agent("codex", from_hash, repo_root=root)
            self.assertEqual(rollback["status"], "rolled_back")
            restored = competition.load("codex", repo_root=root)
            self.assertAlmostEqual(restored["factors"]["pe"]["weight"], 0.30)

    def test_rollback_unknown_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            with self.assertRaises(FileNotFoundError):
                rollback_agent("codex", "missing", repo_root=root)


if __name__ == "__main__":
    unittest.main()
