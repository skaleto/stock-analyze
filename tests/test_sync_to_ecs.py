from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync-to-ecs.sh"


class SyncToEcsScriptTests(unittest.TestCase):
    def test_pushes_market_namespaced_sentiment_and_overlays(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "a_share" / "claude" / "alt_factors").mkdir(parents=True)
            (root / "data" / "a_share" / "claude" / "alt_factors" / "market_sentiment.csv").write_text(
                "week_end_date,sentiment_score,confidence,key_drivers,sources,llm_model,prompt_version,recorded_at\n",
                encoding="utf-8",
            )
            (root / "data" / "hk" / "codex" / "alt_factors").mkdir(parents=True)
            (root / "data" / "hk" / "codex" / "alt_factors" / "sector_sentiment.csv").write_text(
                "week_end,industry,score,confidence,llm_model,prompt_version,recorded_at\n",
                encoding="utf-8",
            )
            (root / "configs" / "agents").mkdir(parents=True)
            (root / "configs" / "agents" / "claude_a_share.yaml").write_text("{}", encoding="utf-8")
            (root / "configs" / "agents" / "codex_hk.yaml").write_text("{}", encoding="utf-8")
            (root / "data" / "cn_qdii_etf" / "codex" / "alt_factors").mkdir(parents=True)
            (root / "data" / "cn_qdii_etf" / "codex" / "alt_factors" / "market_sentiment.csv").write_text(
                "week_end_date,sentiment_score,confidence,key_drivers,sources,llm_model,prompt_version,recorded_at\n",
                encoding="utf-8",
            )
            (root / "configs" / "agents" / "codex_cn_qdii_etf.yaml").write_text("{}", encoding="utf-8")

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            calls_log = root / "rsync-calls.log"
            fake_rsync = fake_bin / "rsync"
            fake_rsync.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$SYNC_TEST_RSYNC_LOG\"\n",
                encoding="utf-8",
            )
            fake_rsync.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                    "SA_ECS_LOCAL_REPO": str(root),
                    "SA_ECS_REMOTE": "fakehost:/remote/app",
                    "SA_ECS_AFTER_SYNC": "0",
                    "SYNC_TEST_RSYNC_LOG": str(calls_log),
                }
            )
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            calls = calls_log.read_text(encoding="utf-8") if calls_log.exists() else ""
            self.assertIn("data/a_share/claude/alt_factors/", calls)
            self.assertIn("fakehost:/remote/app/data/a_share/claude/alt_factors/", calls)
            self.assertIn("data/hk/codex/alt_factors/", calls)
            self.assertIn("fakehost:/remote/app/data/hk/codex/alt_factors/", calls)
            self.assertIn("data/cn_qdii_etf/codex/alt_factors/", calls)
            self.assertIn("fakehost:/remote/app/data/cn_qdii_etf/codex/alt_factors/", calls)
            self.assertIn("configs/agents/claude_a_share.yaml", calls)
            self.assertIn("configs/agents/codex_hk.yaml", calls)
            self.assertIn("configs/agents/codex_cn_qdii_etf.yaml", calls)

    def test_remote_refresh_explicitly_uses_all_market_dashboard(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "us" / "claude").mkdir(parents=True)

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_rsync = fake_bin / "rsync"
            fake_rsync.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_rsync.chmod(0o755)
            calls_log = root / "ssh-calls.log"
            fake_ssh = fake_bin / "ssh"
            fake_ssh.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$SYNC_TEST_SSH_LOG\"\n",
                encoding="utf-8",
            )
            fake_ssh.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                    "SA_ECS_LOCAL_REPO": str(root),
                    "SA_ECS_REMOTE": "fakehost:/remote/app",
                    "SYNC_TEST_SSH_LOG": str(calls_log),
                }
            )
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            calls = calls_log.read_text(encoding="utf-8") if calls_log.exists() else ""
            self.assertIn("fakehost", calls)
            self.assertIn("competition-dashboard --market all", calls)

    def test_pushes_local_owned_overseas_run_artifacts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data" / "hk" / "codex"
            data_dir.mkdir(parents=True)
            (data_dir / "runs.csv").write_text(
                "run_id,command,as_of,started_at,finished_at,status\n"
                "run-weekly-x,run-weekly,2026-05-29,2026-05-31T20:00:00,2026-05-31T20:01:00,success\n",
                encoding="utf-8",
            )
            (data_dir / "pending_orders.json").write_text('{"orders":[]}', encoding="utf-8")
            (data_dir / "daily_nav.csv").write_text("date,total_value\n2026-05-29,150000\n", encoding="utf-8")
            reports_dir = root / "reports" / "hk" / "codex"
            reports_dir.mkdir(parents=True)
            (reports_dir / "weekly_report.md").write_text("# weekly\n", encoding="utf-8")
            (reports_dir / "dashboard.html").write_text("<html></html>\n", encoding="utf-8")

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            calls_log = root / "rsync-calls.log"
            fake_rsync = fake_bin / "rsync"
            fake_rsync.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$SYNC_TEST_RSYNC_LOG\"\n",
                encoding="utf-8",
            )
            fake_rsync.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                    "SA_ECS_LOCAL_REPO": str(root),
                    "SA_ECS_REMOTE": "fakehost:/remote/app",
                    "SA_ECS_AFTER_SYNC": "0",
                    "SYNC_TEST_RSYNC_LOG": str(calls_log),
                }
            )
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            calls = calls_log.read_text(encoding="utf-8") if calls_log.exists() else ""
            self.assertIn("data/hk/codex/runs.csv", calls)
            self.assertIn("fakehost:/remote/app/data/hk/codex/runs.csv", calls)
            self.assertIn("data/hk/codex/pending_orders.json", calls)
            self.assertIn("data/hk/codex/daily_nav.csv", calls)
            self.assertIn("reports/hk/codex/", calls)
            self.assertIn("fakehost:/remote/app/reports/hk/codex/", calls)


if __name__ == "__main__":
    unittest.main()
