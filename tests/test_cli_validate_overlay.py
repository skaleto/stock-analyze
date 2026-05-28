from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from stock_analyze.cli import main


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


def _seed_repo(root: Path, agent: str, overlay: dict) -> None:
    (root / "configs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "competition_a_share.yaml").write_text(
        json.dumps(BASELINE_CONFIG), encoding="utf-8"
    )
    (root / "configs" / "agents" / f"{agent}_a_share.yaml").write_text(
        json.dumps(overlay), encoding="utf-8"
    )


class _ChdirContext:
    """Tiny test-only chdir helper since Python 3.10 lacks contextlib.chdir."""

    def __init__(self, target: Path) -> None:
        self.target = target
        self.previous: str | None = None

    def __enter__(self) -> "Path":
        self.previous = os.getcwd()
        os.chdir(self.target)
        return self.target

    def __exit__(self, *exc: object) -> None:
        if self.previous is not None:
            os.chdir(self.previous)


class ValidateOverlayCliTests(unittest.TestCase):
    def test_exit_0_on_valid_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            overlay = {
                "agent_id": "claude",
                "factors": {"pe": {"weight": 0.5, "direction": "low"}},
            }
            _seed_repo(root, "claude", overlay)
            with _ChdirContext(root):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = main(["validate-overlay", "--agent", "claude"])
            self.assertEqual(rc, 0)
            self.assertIn("OK", buf.getvalue())

    def test_exit_1_on_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            overlay = {
                "agent_id": "claude",
                "rogue": True,  # top-level key not in whitelist
                "factors": {"pe": {"weight": 0.5}},
            }
            _seed_repo(root, "claude", overlay)
            with _ChdirContext(root):
                err_buf = io.StringIO()
                with redirect_stderr(err_buf):
                    rc = main(["validate-overlay", "--agent", "claude"])
            self.assertEqual(rc, 1)
            self.assertIn("守卫检查失败", err_buf.getvalue())

    def test_exit_1_on_invalid_weight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            overlay = {
                "agent_id": "claude",
                "factors": {"pe": {"weight": 1.5, "direction": "low"}},
            }
            _seed_repo(root, "claude", overlay)
            with _ChdirContext(root):
                err_buf = io.StringIO()
                with redirect_stderr(err_buf):
                    rc = main(["validate-overlay", "--agent", "claude"])
            self.assertEqual(rc, 1)
            self.assertIn("守卫检查失败", err_buf.getvalue())

    def test_exit_1_on_unknown_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root, "claude", {"agent_id": "claude", "factors": {}})
            with _ChdirContext(root):
                err_buf = io.StringIO()
                with redirect_stderr(err_buf):
                    rc = main(["validate-overlay", "--agent", "missing"])
            self.assertEqual(rc, 1)
            self.assertIn("未知 agent", err_buf.getvalue())

    def test_exit_1_on_malformed_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs" / "agents").mkdir(parents=True, exist_ok=True)
            (root / "configs" / "competition_a_share.yaml").write_text(
                json.dumps(BASELINE_CONFIG), encoding="utf-8"
            )
            (root / "configs" / "agents" / "claude_a_share.yaml").write_text(
                "{not json", encoding="utf-8"
            )
            with _ChdirContext(root):
                err_buf = io.StringIO()
                with redirect_stderr(err_buf):
                    rc = main(["validate-overlay", "--agent", "claude"])
            self.assertEqual(rc, 1)
            self.assertIn("解析失败", err_buf.getvalue())


class CliHelpTests(unittest.TestCase):
    def test_help_lists_new_subcommand_and_drops_old_ones(self) -> None:
        buf = io.StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(buf):
                main(["--help"])
        text = buf.getvalue()
        self.assertIn("validate-overlay", text)
        self.assertNotIn("agent-judge-proposals", text)
        self.assertNotIn("agent-apply-approved-proposals", text)
        self.assertIn("agent-rollback", text)


# Touch sys to satisfy lint when CI strips IO context.
assert sys is not None


if __name__ == "__main__":
    unittest.main()
