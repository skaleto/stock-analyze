from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze import competition
from stock_analyze.config import config_hash
from stock_analyze.evolution_writer import (
    EVOLUTION_COLUMNS,
    compute_diff,
    summarise_diff,
    write_evolution,
)
from stock_analyze.overlay_guard import OverlayBaselineLocked


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


def _seed_repo(root: Path, agent: str = "claude") -> dict:
    (root / "configs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "competition_a_share.yaml").write_text(
        json.dumps(BASELINE_CONFIG), encoding="utf-8"
    )
    overlay = {
        "agent_id": agent,
        "strategy_id": f"{agent}_test",
        "factors": {
            "pe": {"weight": 0.30, "direction": "low"},
            "roe": {"weight": 0.35, "direction": "high"},
            "pb": {"weight": 0.35, "direction": "low"},
        },
        "factor_processing": {"enabled": True, "winsorize_lower": 0.01, "winsorize_upper": 0.99},
        "portfolio_controls": {"max_industry_weight": 0.30},
        "filters": {"min_avg_amount_20": 50000000},
    }
    (root / "configs" / "agents" / f"{agent}_a_share.yaml").write_text(
        json.dumps(overlay), encoding="utf-8"
    )
    (root / "data" / "a_share" / agent).mkdir(parents=True, exist_ok=True)
    return overlay


class WriteEvolutionTests(unittest.TestCase):
    def test_happy_path_writes_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_overlay = _seed_repo(root, agent="claude")
            from_hash = config_hash(competition.load("claude", repo_root=root))
            new_overlay = {
                **old_overlay,
                "factors": {
                    **old_overlay["factors"],
                    "pe": {"weight": 0.40, "direction": "low"},
                    "roe": {"weight": 0.30, "direction": "high"},
                    "pb": {"weight": 0.30, "direction": "low"},
                },
            }
            result = write_evolution(
                agent_id="claude",
                old_overlay=old_overlay,
                new_overlay=new_overlay,
                reasoning_md="# 2026-06 claude 演化\n\n本月加 pe 权重。",
                repo_root=root,
                month="2026-06",
            )
            # 1. Live overlay updated
            updated = json.loads(
                (root / "configs" / "agents" / "claude_a_share.yaml").read_text(encoding="utf-8")
            )
            self.assertAlmostEqual(updated["factors"]["pe"]["weight"], 0.40)
            # 2. History backup created at from_hash
            history = root / "configs" / "agents" / "_history" / f"{from_hash}.yaml"
            self.assertTrue(history.exists())
            backup = json.loads(history.read_text(encoding="utf-8"))
            self.assertAlmostEqual(backup["factors"]["pe"]["weight"], 0.30)
            # 3. evolution_log markdown written
            log_path = root / "data" / "a_share" / "claude" / "evolution_log" / "2026-06.md"
            self.assertTrue(log_path.exists())
            self.assertIn("本月加 pe 权重", log_path.read_text(encoding="utf-8"))
            # 4. evolution_diff JSON written
            diff_path = root / "data" / "a_share" / "claude" / "evolution_diff" / "2026-06.json"
            self.assertTrue(diff_path.exists())
            diff_payload = json.loads(diff_path.read_text(encoding="utf-8"))
            self.assertEqual(diff_payload["agent_id"], "claude")
            self.assertEqual(diff_payload["month"], "2026-06")
            self.assertEqual(diff_payload["from_config_hash"], from_hash)
            self.assertIn("factors.pe.weight", diff_payload["diff"])
            self.assertAlmostEqual(diff_payload["diff"]["factors.pe.weight"]["from"], 0.30)
            self.assertAlmostEqual(diff_payload["diff"]["factors.pe.weight"]["to"], 0.40)
            # 5. config_evolution.csv has new row
            csv_path = root / "data" / "a_share" / "claude" / "config_evolution.csv"
            self.assertTrue(csv_path.exists())
            with csv_path.open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["month"], "2026-06")
            self.assertEqual(rows[0]["from_hash"], from_hash)
            self.assertTrue(rows[0]["reasoning_file"].endswith("2026-06.md"))
            self.assertTrue(rows[0]["diff_file"].endswith("2026-06.json"))
            # Returned summary
            self.assertEqual(result["status"], "evolved")
            self.assertEqual(result["from_hash"], from_hash)

    def test_guard_failure_aborts_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_overlay = _seed_repo(root, agent="claude")
            overlay_path = root / "configs" / "agents" / "claude_a_share.yaml"
            mtime_before = overlay_path.stat().st_mtime_ns
            # New overlay tries to override accounts.*.cash → baseline lock
            new_overlay = {
                "agent_id": "claude",
                "factors": old_overlay["factors"],
                # `accounts` is not in the allowed top-level set, so this
                # raises OverlayUnknownTopLevelKey before the lock check.
                # To test atomicity on the lock check specifically we have
                # to construct an overlay that the guard rejects but that
                # the top-level whitelist accepts. The cleanest path is
                # to set an unknown factor:
            }
            new_overlay["factors"] = {
                **old_overlay["factors"],
                "magic_alpha": {"weight": 0.05, "direction": "high"},
            }
            from stock_analyze.overlay_guard import OverlayUnknownFactor
            with self.assertRaises(OverlayUnknownFactor):
                write_evolution(
                    agent_id="claude",
                    old_overlay=old_overlay,
                    new_overlay=new_overlay,
                    reasoning_md="should not write",
                    repo_root=root,
                    month="2026-06",
                )
            # Overlay file unchanged
            self.assertEqual(overlay_path.stat().st_mtime_ns, mtime_before)
            # No log / diff / csv created
            self.assertFalse((root / "data" / "a_share" / "claude" / "evolution_log" / "2026-06.md").exists())
            self.assertFalse((root / "data" / "a_share" / "claude" / "evolution_diff" / "2026-06.json").exists())
            self.assertFalse((root / "data" / "a_share" / "claude" / "config_evolution.csv").exists())
            self.assertFalse((root / "configs" / "agents" / "_history").exists())

    def test_csv_columns_include_reasoning_and_diff_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_overlay = _seed_repo(root, agent="claude")
            new_overlay = {**old_overlay, "filters": {"min_avg_amount_20": 60000000}}
            write_evolution(
                agent_id="claude",
                old_overlay=old_overlay,
                new_overlay=new_overlay,
                reasoning_md="bump min_avg_amount_20",
                repo_root=root,
                month="2026-06",
            )
            csv_path = root / "data" / "a_share" / "claude" / "config_evolution.csv"
            with csv_path.open(encoding="utf-8-sig") as handle:
                reader = csv.reader(handle)
                header = next(reader)
            self.assertIn("reasoning_file", header)
            self.assertIn("diff_file", header)
            self.assertEqual(header, EVOLUTION_COLUMNS)

    def test_history_idempotent_when_hash_already_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_overlay = _seed_repo(root, agent="claude")
            from_hash = config_hash(competition.load("claude", repo_root=root))
            history = root / "configs" / "agents" / "_history" / f"{from_hash}.yaml"
            history.parent.mkdir(parents=True)
            history.write_text(json.dumps({"sentinel": True}), encoding="utf-8")

            new_overlay = {**old_overlay, "filters": {"min_avg_amount_20": 70000000}}
            write_evolution(
                agent_id="claude",
                old_overlay=old_overlay,
                new_overlay=new_overlay,
                reasoning_md="x",
                repo_root=root,
                month="2026-06",
            )
            # Sentinel content preserved — write_evolution did not overwrite
            preserved = json.loads(history.read_text(encoding="utf-8"))
            self.assertEqual(preserved, {"sentinel": True})

    def test_migrates_legacy_csv_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_overlay = _seed_repo(root, agent="claude")
            # Plant a legacy-schema csv from the deleted proposal_apply era.
            csv_path = root / "data" / "a_share" / "claude" / "config_evolution.csv"
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_columns = [
                "event", "event_at", "agent_id", "month",
                "source_proposal", "decision_path",
                "from_hash", "to_hash", "patch_paths", "reviewer",
            ]
            with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=legacy_columns)
                writer.writeheader()
                writer.writerow({
                    "event": "apply", "event_at": "2026-05-01T00:00:00",
                    "agent_id": "claude", "month": "2026-05",
                    "source_proposal": "data/claude/proposals/2026-05-strategy.json",
                    "decision_path": "data/competition/decisions/2026-05-claude.json",
                    "from_hash": "old1", "to_hash": "old2",
                    "patch_paths": "factors.pe.weight", "reviewer": "referee",
                })
            new_overlay = {**old_overlay, "filters": {"min_avg_amount_20": 80000000}}
            write_evolution(
                agent_id="claude",
                old_overlay=old_overlay,
                new_overlay=new_overlay,
                reasoning_md="x",
                repo_root=root,
                month="2026-06",
            )
            with csv_path.open(encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                header = list(reader.fieldnames or [])
                rows = list(reader)
            self.assertEqual(header, EVOLUTION_COLUMNS)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["month"], "2026-05")
            self.assertEqual(rows[1]["month"], "2026-06")
            # legacy row's source_proposal column is dropped, blank reasoning_file
            self.assertEqual(rows[0]["reasoning_file"], "")
            self.assertNotEqual(rows[1]["reasoning_file"], "")


class DiffHelpersTests(unittest.TestCase):
    def test_compute_diff_picks_up_nested_changes(self) -> None:
        old = {"factors": {"pe": {"weight": 0.1}, "roe": {"weight": 0.4}}}
        new = {"factors": {"pe": {"weight": 0.2}, "roe": {"weight": 0.4}}}
        diff = compute_diff(old, new)
        self.assertEqual(set(diff.keys()), {"factors.pe.weight"})
        self.assertEqual(diff["factors.pe.weight"], {"from": 0.1, "to": 0.2})

    def test_compute_diff_picks_up_added_keys(self) -> None:
        old: dict = {}
        new = {"filters": {"min_listing_days": 365}}
        diff = compute_diff(old, new)
        self.assertEqual(diff["filters.min_listing_days"]["from"], None)
        self.assertEqual(diff["filters.min_listing_days"]["to"], 365)

    def test_summarise_diff_truncates(self) -> None:
        diff = {f"factors.f{i}.weight": {"from": 0.0, "to": 0.1} for i in range(10)}
        text = summarise_diff(diff, limit=3)
        self.assertIn("…+7 more", text)

    def test_summarise_diff_empty(self) -> None:
        self.assertEqual(summarise_diff({}), "no_change")


# Sanity import of OverlayBaselineLocked so unused-import lint stays clean.
assert OverlayBaselineLocked is not None


if __name__ == "__main__":
    unittest.main()
