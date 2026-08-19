import os
import subprocess
import tempfile
import unittest
from pathlib import Path


UNIT_DIR = Path("deploy/systemd")


class PredictionSystemdTest(unittest.TestCase):
    def test_daily_research_runs_after_market_data_with_lock_and_without_secrets(self):
        service = (UNIT_DIR / "stock-analyze-research.service").read_text(encoding="utf-8")
        market_data = (UNIT_DIR / "stock-analyze-market-data.service").read_text(encoding="utf-8")

        self.assertIn("After=stock-analyze-market-data.service", service)
        self.assertNotIn("stock-analyze-intelligence.service", service)
        self.assertNotIn("stock-analyze-intelligence.service", market_data)
        self.assertIn("flock", service)
        self.assertIn("prepare-research-data --offline", service)
        self.assertIn("run-prediction-research --offline", service)
        self.assertIn(" predict --offline", service)
        self.assertNotIn(
            'stock_analyze.cli --market "$market" run-model-iteration --offline',
            service,
        )
        self.assertNotIn("stock-analyze-model-iteration.service", service)
        self.assertNotIn("run-model-shadow --offline", service)
        self.assertNotIn("EnvironmentFile=/etc/stock-analyze/secrets.env", service)
        self.assertIn("EnvironmentFile=/etc/stock-analyze/secrets.env", market_data)
        self.assertIn("prepare-qdii-market-data", market_data)
        self.assertLess(
            market_data.index("prepare-qdii-market-data"),
            market_data.index("--market cn_qdii_etf --agent codex prepare-research-data"),
        )
        self.assertIn("stock-analyze-research.service", market_data)
        self.assertIn("OnFailure=stock-analyze-pipeline-failure@%n.service", service)
        self.assertIn(
            "ExecStartPost=/bin/systemctl start --no-block stock-analyze-claude-cn-qdii-etf-daily.service",
            service,
        )
        self.assertIn(
            "ExecStartPost=/bin/systemctl start --no-block stock-analyze-codex-cn-qdii-etf-daily.service",
            service,
        )
        self.assertIn(
            "Persistent=true",
            (UNIT_DIR / "stock-analyze-market-data.timer").read_text(
                encoding="utf-8"
            ),
        )

    def test_model_iteration_failure_is_isolated_from_formal_daily_services(self):
        research = (UNIT_DIR / "stock-analyze-research.service").read_text(encoding="utf-8")
        model_iteration = (UNIT_DIR / "stock-analyze-model-iteration.service").read_text(encoding="utf-8")

        self.assertIn("run-model-iteration --offline", model_iteration)
        self.assertIn('--as-of "$(date +%F)" run-paper-candidates --repo-root /opt/stock-analyze/app --scope all --offline', model_iteration)
        self.assertIn("for market in a_share cn_qdii_etf", model_iteration)
        self.assertIn("OnFailure=stock-analyze-pipeline-failure@%n.service", model_iteration)
        self.assertIn("result=", model_iteration)
        self.assertIn("stock-analyze-claude-daily.service", research)
        self.assertIn("stock-analyze-codex-daily.service", research)
        finalizer = (
            UNIT_DIR / "stock-analyze-daily-finalize.service"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "systemctl start --no-block stock-analyze-model-iteration.service",
            finalizer,
        )
        self.assertIn('if [ "$result" -eq 0 ]', finalizer)
        self.assertNotIn("stock-analyze-claude-daily.service", model_iteration)
        self.assertNotIn("stock-analyze-codex-daily.service", model_iteration)
        self.assertIn("MemoryMax=1250M", model_iteration)

    def test_optional_prediction_fallback_does_not_block_formal_daily_services(self):
        research = (UNIT_DIR / "stock-analyze-research.service").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'if ! /opt/stock-analyze/venv/bin/python -m stock_analyze.cli '
            '--market "$market" --agent "$agent" predict --offline',
            research,
        )
        self.assertIn("optional prediction unavailable", research)
        self.assertNotIn(
            'for agent in claude codex; do /opt/stock-analyze/venv/bin/python '
            '-m stock_analyze.cli --market "$market" --agent "$agent" predict '
            '--offline; done',
            research,
        )

    def test_optional_intelligence_evaluation_does_not_block_formal_services(self):
        research = (UNIT_DIR / "stock-analyze-research.service").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "if ! /opt/stock-analyze/venv/bin/python -m stock_analyze.cli "
            "intelligence-evaluate",
            research,
        )
        self.assertIn("optional intelligence evaluation unavailable", research)

    def test_downstream_oneshots_do_not_restart_completed_upstream_stages(self):
        research = (UNIT_DIR / "stock-analyze-research.service").read_text(
            encoding="utf-8"
        )
        model_iteration = (
            UNIT_DIR / "stock-analyze-model-iteration.service"
        ).read_text(encoding="utf-8")

        self.assertIn("After=stock-analyze-market-data.service", research)
        self.assertNotIn(
            "Requires=stock-analyze-market-data.service",
            research,
        )
        self.assertIn("After=stock-analyze-research.service", model_iteration)
        self.assertNotIn(
            "Requires=stock-analyze-research.service",
            model_iteration,
        )

    def test_monthly_training_only_prepares_checked_local_training_bundles(self):
        service = (UNIT_DIR / "stock-analyze-model-training.service").read_text(encoding="utf-8")
        timer = (UNIT_DIR / "stock-analyze-model-training.timer").read_text(encoding="utf-8")
        script = Path("scripts/prepare-model-training-bundles.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("prepare-model-training-bundles.sh", service)
        self.assertIn("refresh-research-labels --offline", script)
        self.assertIn("research-training-bundle-export", script)
        self.assertIn("for market in a_share cn_qdii_etf", script)
        self.assertIn("continue", script)
        self.assertIn("MODEL_TRAIN_KEEP_RUNS", script)
        self.assertIn("prune_runs", script)
        self.assertNotIn("run-prediction-research --offline", service)
        self.assertNotIn("run-baseline-first-research --offline", service)
        self.assertNotIn("run-classical-tournament --offline", service)
        self.assertNotIn("train-prediction-models --offline", service)
        self.assertNotIn("active", service.lower())
        self.assertIn("flock", service)
        self.assertIn("OnCalendar=*-*-01 02:30:00 Asia/Shanghai", timer)
        self.assertIn("Persistent=true", timer)

    def test_monthly_bundle_preparation_retains_only_bounded_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exchange = root / "exchange"
            for market in ("a_share", "cn_qdii_etf"):
                for index in range(10):
                    manifest = (
                        exchange / f"{market}-202607{index:02d}"
                        / "input/manifest.json"
                    )
                    manifest.parent.mkdir(parents=True)
                    manifest.write_text("{}", encoding="utf-8")
                    (manifest.parents[1] / ".complete").touch()
            environment = {
                **os.environ,
                "MODEL_TRAIN_REPO_ROOT": str(root / "repo"),
                "MODEL_TRAIN_PYTHON": "/usr/bin/true",
                "MODEL_TRAIN_EXCHANGE_ROOT": str(exchange),
                "MODEL_TRAIN_RUN_ID": "20260814-test",
                "MODEL_TRAIN_KEEP_RUNS": "8",
            }

            subprocess.run(
                ["scripts/prepare-model-training-bundles.sh", "2026-08-14"],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            counts = {
                market: len([
                    path
                    for path in exchange.glob(f"{market}-*/input/manifest.json")
                    if (path.parents[1] / ".complete").is_file()
                ])
                for market in ("a_share", "cn_qdii_etf")
            }

        self.assertEqual(counts, {"a_share": 8, "cn_qdii_etf": 8})

    def test_local_research_marks_complete_only_after_all_required_imports(self):
        script = Path("scripts/run-local-baseline-first-research.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("mark_complete()", script)
        self.assertIn(
            'if [[ ${#REPORTS[@]} -eq 0 ]]; then\n  mark_complete\n',
            script,
        )
        self.assertIn(
            'done\n\nmark_complete\n\nprintf \'Local baseline-first research complete:',
            script,
        )
        self.assertLess(
            script.index("research-model-bundle-import"),
            script.rindex("mark_complete"),
        )

    def test_tabular_forward_observer_is_daily_and_research_only(self):
        service = (
            UNIT_DIR / "stock-analyze-tabular-forward.service"
        ).read_text(encoding="utf-8")
        timer = (
            UNIT_DIR / "stock-analyze-tabular-forward.timer"
        ).read_text(encoding="utf-8")

        self.assertIn("run-regime-tabular-forward --offline", service)
        self.assertIn("--market a_share --agent codex", service)
        self.assertIn("flock --nonblock", service)
        self.assertIn("After=stock-analyze-market-data.service", service)
        self.assertNotIn("run-daily", service)
        self.assertNotIn("run-model-iteration", service)
        self.assertIn("MemoryMax=1600M", service)
        self.assertIn("OnCalendar=Mon..Fri *-*-* 19:20:00 Asia/Shanghai", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
