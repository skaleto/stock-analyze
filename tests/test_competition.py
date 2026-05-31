from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from stock_analyze import competition
from stock_analyze.competition import (
    CompetitionBaselineLocked,
    UnknownAgent,
    list_agents,
    list_agents_for_market,
    load,
    resolve_agent_paths,
)


BASELINE_CONFIG = {
    "competition_id": "test_competition",
    "version": 1,
    "start_date": "2026-05-26",
    "initial_cash": 1000000,
    "accounts": [
        {"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500000, "top_n": 10},
        {"id": "zz500", "scope": "zz500", "benchmark": "000905", "cash": 500000, "top_n": 10},
    ],
    "schedule": {"rebalance": "weekly_after_close", "signal_day": "last_trading_day_of_week", "execution": "next_trading_day_open"},
    "trading": {
        "lot_size": 100,
        "commission_rate": 0.0003,
        "min_commission": 5,
        "stamp_tax_rate": 0.0005,
        "slippage_rate": 0.0005,
        "max_single_weight": 0.10,
    },
    "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
}


class _RepoFixture:
    """Builds a minimal repo layout under a tmp dir for competition tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "configs" / "agents").mkdir(parents=True, exist_ok=True)
        (root / "configs" / "competition_a_share.yaml").write_text(json.dumps(BASELINE_CONFIG), encoding="utf-8")

    def write_overlay(self, agent_id: str, overlay: dict) -> None:
        path = self.root / "configs" / "agents" / f"{agent_id}_a_share.yaml"
        payload = {"agent_id": agent_id, "strategy_id": f"{agent_id}_v1", **overlay}
        path.write_text(json.dumps(payload), encoding="utf-8")


class CompetitionLoaderTests(unittest.TestCase):
    def test_agent_mode_rejects_explicit_config(self) -> None:
        from stock_analyze.cli import _resolve_runtime, build_parser

        args = build_parser().parse_args(["--agent", "codex", "--config", "configs/strategy_v1.yaml", "init"])
        with self.assertRaises(CompetitionBaselineLocked) as ctx:
            _resolve_runtime(args)
        self.assertEqual(ctx.exception.field, "agent_config_override")

    def test_locked_initial_cash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _RepoFixture(Path(tmp))
            fixture.write_overlay("codex", {"initial_cash": 2000000, "factors": {"pe": {"weight": 1.0, "direction": "low"}}})
            with self.assertRaises(CompetitionBaselineLocked) as ctx:
                load("codex", repo_root=tmp)
            self.assertEqual(ctx.exception.field, "overlay_top_level:initial_cash")

    def test_locked_trading_commission_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _RepoFixture(Path(tmp))
            fixture.write_overlay("codex", {"factors": {"pe": {"weight": 1.0, "direction": "low"}}})
            # Manually inject a disallowed top-level key bypassing helper.
            path = Path(tmp) / "configs" / "agents" / "codex_a_share.yaml"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["trading"] = {"commission_rate": 0}
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(CompetitionBaselineLocked):
                load("codex", repo_root=tmp)

    def test_factors_overlay_is_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _RepoFixture(Path(tmp))
            fixture.write_overlay(
                "claude",
                {
                    "factors": {"pe": {"weight": 0.5, "direction": "low"}, "roe": {"weight": 0.5, "direction": "high"}},
                    "factor_processing": {"enabled": True, "winsorize_lower": 0.02},
                    "portfolio_controls": {"max_industry_weight": 0.25},
                    "filters": {"min_market_cap_yi": 40},
                },
            )
            merged = load("claude", repo_root=tmp)
            self.assertEqual(merged["agent_id"], "claude")
            self.assertEqual(merged["initial_cash"], 1000000)
            self.assertEqual(merged["factors"]["pe"]["weight"], 0.5)
            self.assertEqual(merged["factor_processing"]["winsorize_lower"], 0.02)
            self.assertEqual(merged["portfolio_controls"]["max_industry_weight"], 0.25)
            self.assertEqual(merged["filters"]["min_market_cap_yi"], 40)

    def test_overlay_top_level_must_be_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _RepoFixture(Path(tmp))
            fixture.write_overlay("codex", {"factors": {"pe": {"weight": 1.0, "direction": "low"}}})
            path = Path(tmp) / "configs" / "agents" / "codex_a_share.yaml"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["accounts"] = [{"id": "hs300", "cash": 600000}]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(CompetitionBaselineLocked):
                load("codex", repo_root=tmp)

    def test_unknown_agent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _RepoFixture(Path(tmp))
            with self.assertRaises(UnknownAgent):
                resolve_agent_paths("unknown", repo_root=tmp)

    def test_list_agents_finds_overlay_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _RepoFixture(Path(tmp))
            fixture.write_overlay("claude", {"factors": {}})
            fixture.write_overlay("codex", {"factors": {}})
            agents = list_agents(tmp)
            self.assertEqual(agents, ["claude", "codex"])

    def test_list_agents_for_market_uses_market_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs" / "agents").mkdir(parents=True, exist_ok=True)
            (root / "configs" / "agents" / "claude_a_share.yaml").write_text("{}", encoding="utf-8")
            (root / "configs" / "agents" / "codex_a_share.yaml").write_text("{}", encoding="utf-8")
            (root / "configs" / "agents" / "claude_hk.yaml").write_text("{}", encoding="utf-8")
            (root / "configs" / "agents" / "codex_hk.yaml").write_text("{}", encoding="utf-8")
            (root / "configs" / "agents" / "claude_us.yaml").write_text("{}", encoding="utf-8")

            self.assertEqual(list_agents_for_market("a_share", root), ["claude", "codex"])
            self.assertEqual(list_agents_for_market("hk", root), ["claude", "codex"])
            self.assertEqual(list_agents_for_market("us", root), ["claude"])

    def test_resolve_agent_paths_returns_expected_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _RepoFixture(Path(tmp))
            fixture.write_overlay("claude", {"factors": {}})
            paths = resolve_agent_paths("claude", repo_root=tmp)
            self.assertEqual(paths.config_path, Path(tmp) / "configs" / "agents" / "claude_a_share.yaml")
            self.assertEqual(paths.data_dir, Path(tmp) / "data" / "a_share" / "claude")
            self.assertEqual(paths.reports_dir, Path(tmp) / "reports" / "a_share" / "claude")
            self.assertEqual(paths.shared_cache_dir, Path(tmp) / "data" / "shared" / "cache")
            self.assertEqual(paths.competition_data_dir, Path(tmp) / "data" / "competition")


class CompetitionInitSmokeTests(unittest.TestCase):
    """Smoke-style: run cli._command_competition_init in a clean cwd."""

    def test_init_creates_directories_and_state(self) -> None:
        from stock_analyze import cli

        with tempfile.TemporaryDirectory() as tmp:
            fixture = _RepoFixture(Path(tmp))
            fixture.write_overlay("claude", {"factors": {"pe": {"weight": 1.0, "direction": "low"}}})
            fixture.write_overlay("codex", {"factors": {"roe": {"weight": 1.0, "direction": "high"}}})
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                rc = cli._command_competition_init()
            finally:
                os.chdir(cwd)
            self.assertEqual(rc, 0)
            # data/<market>/<agent>/ for A-share after Phase 1 migration;
            # data/shared/ and data/competition/ stay at the top level (cross-market).
            for sub in ("shared", "competition"):
                self.assertTrue((Path(tmp) / "data" / sub).is_dir())
            for agent in ("claude", "codex"):
                self.assertTrue((Path(tmp) / "data" / "a_share" / agent).is_dir())
            for sub in ("claude", "codex", "competition"):
                self.assertTrue((Path(tmp) / "reports" / "a_share" / sub).is_dir() if sub != "competition"
                                else (Path(tmp) / "reports" / sub).is_dir())
            claude_state = json.loads((Path(tmp) / "data" / "a_share" / "claude" / "state.json").read_text())
            self.assertEqual(claude_state["accounts"]["hs300"]["cash"], 500000.0)
            metadata = json.loads((Path(tmp) / "data" / "competition" / "competition_metadata.json").read_text())
            self.assertEqual(metadata["competition_id"], "test_competition")
            self.assertEqual(metadata["start_date"], "2026-05-26")
            self.assertEqual(metadata["agents"], ["claude", "codex"])
            self.assertTrue(metadata["baseline_hash"])


class ValidateOverlayPureMemoryTests(unittest.TestCase):
    def test_validate_overlay_does_not_touch_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _RepoFixture(root)
            fixture.write_overlay(
                "claude",
                {"factors": {"pe": {"weight": 1.0, "direction": "low"}}},
            )
            overlay_path = root / "configs" / "agents" / "claude_a_share.yaml"
            mtime_before = overlay_path.stat().st_mtime_ns
            history_dir = root / "configs" / "agents" / "_history"
            history_before = sorted(history_dir.glob("*")) if history_dir.exists() else []

            proposed = {
                "agent_id": "claude",
                "strategy_id": "claude_v1",
                "factors": {
                    "pe": {"weight": 0.4, "direction": "low"},
                    "roe": {"weight": 0.6, "direction": "high"},
                },
            }
            merged = competition.validate_overlay("claude", proposed, repo_root=root)

            self.assertEqual(overlay_path.stat().st_mtime_ns, mtime_before)
            self.assertEqual(merged["initial_cash"], 1000000)
            self.assertAlmostEqual(merged["factors"]["roe"]["weight"], 0.6)
            history_after = sorted(history_dir.glob("*")) if history_dir.exists() else []
            self.assertEqual(history_before, history_after)

    def test_validate_overlay_rejects_locked_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _RepoFixture(root)
            fixture.write_overlay("codex", {"factors": {"pe": {"weight": 1.0, "direction": "low"}}})
            overlay_path = root / "configs" / "agents" / "codex_a_share.yaml"
            mtime_before = overlay_path.stat().st_mtime_ns
            with self.assertRaises(CompetitionBaselineLocked):
                competition.validate_overlay(
                    "codex",
                    {"initial_cash": 9_999_999, "factors": {"pe": {"weight": 1.0, "direction": "low"}}},
                    repo_root=root,
                )
            self.assertEqual(overlay_path.stat().st_mtime_ns, mtime_before)


if __name__ == "__main__":
    unittest.main()
