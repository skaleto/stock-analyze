import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from stock_analyze.cli import build_parser, main
from stock_analyze.research.local_training import manifest_source_fingerprint


class CLIResearchTest(unittest.TestCase):
    def test_cli_dispatches_a_share_materializer(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.a_share_materializer.materialize_a_share_research_data",
            return_value={"status": "complete", "historical_union_count": 3},
        ) as materialize:
            code = main(
                [
                    "materialize-a-share-research-data",
                    "--start", "2020-01-02",
                    "--end", "2020-02-03",
                    "--as-of", "20200203",
                    "--cache-root", f"{tmp}/cache",
                    "--repo-root", tmp,
                ]
            )

        self.assertEqual(code, 0)
        materialize.assert_called_once_with(
            repo_root=Path(tmp),
            cache_root=Path(tmp) / "cache",
            start=date(2020, 1, 2),
            end=date(2020, 2, 3),
            as_of="20200203",
        )

    def test_cli_returns_nonzero_when_materialization_fails(self):
        with patch(
            "stock_analyze.research.a_share_materializer.materialize_a_share_research_data",
            side_effect=ValueError("materialization_source_missing:daily"),
        ):
            code = main(
                [
                    "materialize-a-share-research-data",
                    "--start", "2020-01-02",
                    "--end", "2020-02-03",
                    "--as-of", "20200203",
                    "--cache-root", "/tmp/cache",
                    "--repo-root", "/tmp/repo",
                ]
            )

        self.assertEqual(code, 2)

    def test_research_cli_bounds_default_a_share_history_sample(self):
        args = build_parser().parse_args(["prepare-research-data"])
        self.assertEqual(args.max_full_history_instruments, 500)

    def test_cli_dispatches_resumable_moneyflow_backfill(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.moneyflow.backfill_moneyflow_history",
            return_value={
                "status": "complete",
                "target_codes": 1,
                "completed_codes": 1,
                "failed_codes": 0,
                "rows": 2,
            },
        ) as backfill:
            code = main([
                "--as-of", "2026-01-05",
                "backfill-a-share-moneyflow",
                "--repo-root", tmp,
                "--start-date", "20260102",
                "--code", "000009",
                "--max-workers", "2",
            ])

        self.assertEqual(code, 0)
        backfill.assert_called_once_with(
            Path(tmp),
            codes=["000009"],
            start_date="20260102",
            end_date="20260105",
            max_workers=2,
            retries=3,
            requests_per_minute=180,
            force=False,
        )

    def test_parser_accepts_all_research_commands(self):
        parser = build_parser()
        for command in (
            "prepare-research-data",
            "run-prediction-research",
            "refresh-research-labels",
            "train-prediction-models",
            "run-classical-tournament",
            "run-unified-model-arena",
            "run-baseline-first-research",
            "run-strategy-campaign",
            "run-cross-sectional-alpha-repair",
            "run-regime-tabular-alpha",
            "predict",
        ):
            args = parser.parse_args(["--market", "a_share", "--agent", "codex", command, "--offline"])
            self.assertEqual(args.command, command)
            self.assertTrue(args.offline)

    def test_cli_dispatches_strategy_recovery_campaign(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.strategy_campaign.run_strategy_campaign",
            return_value={"status": "complete", "campaign_id": "strategy-recovery-20260814-v1"},
        ) as run_campaign:
            first = Path(tmp) / "a-share" / "manifest.json"
            second = Path(tmp) / "qdii" / "manifest.json"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")

            code = main([
                "--as-of", "2026-08-14",
                "run-strategy-campaign",
                "--offline",
                "--repo-root", tmp,
                "--campaign", "strategy-recovery-20260814-v1",
                "--input-bundle", str(first),
                "--input-bundle", str(second),
                "--stage", "transparent",
            ])

        self.assertEqual(code, 0)
        run_campaign.assert_called_once_with(
            repo_root=Path(tmp),
            campaign_id="strategy-recovery-20260814-v1",
            as_of="2026-08-14",
            stage="transparent",
            input_manifests=(first, second),
        )

    def test_cli_dispatches_unified_model_arena(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.run_unified_model_arena",
            return_value={"status": "complete", "scopes": []},
        ) as arena:
            code = main(
                [
                    "--market", "cn_qdii_etf", "--agent", "codex",
                    "--as-of", "2026-08-13", "run-unified-model-arena",
                    "--offline", "--force", "--repo-root", tmp,
                ]
            )

        self.assertEqual(code, 0)
        arena.assert_called_once_with(horizon=None, force=True)

    def test_cli_dispatches_rule_core_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.rule_core_diagnostic.run_rule_core_diagnostic",
            return_value={
                "status": "stopped",
                "decision": {
                    "a_share": "data_blocked",
                    "cn_qdii_etf": "negative_hypothesis",
                },
            },
        ) as diagnostic:
            code = main(
                [
                    "--as-of", "2026-08-07",
                    "run-rule-core-diagnostic", "--offline", "--repo-root", tmp,
                ]
            )

        self.assertEqual(code, 0)
        diagnostic.assert_called_once()
        self.assertEqual(diagnostic.call_args.kwargs["as_of"], "2026-08-07")

    def test_rule_core_accepts_documented_subcommand_as_of_order(self):
        args = build_parser().parse_args(
            ["run-rule-core-diagnostic", "--offline", "--as-of", "20260807"]
        )

        self.assertEqual(args.command, "run-rule-core-diagnostic")
        self.assertEqual(args.as_of, "20260807")

    def test_cli_dispatches_preregistered_earnings_drift_study(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.earnings_drift_study.run_earnings_drift_study",
            return_value={
                "status": "insufficient_data",
                "model_training_allowed": False,
            },
        ) as study:
            code = main([
                "run-earnings-drift-study",
                "--repo-root", tmp,
                "--snapshot-date", "20260814",
            ])

        self.assertEqual(code, 0)
        study.assert_called_once()
        self.assertEqual(study.call_args.kwargs["snapshot_date"], "20260814")

    def test_cli_dispatches_account_scoped_classical_tournament(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.run_classical_tournament",
            return_value={"status": "no_pass", "candidates": []},
        ) as tournament:
            code = main(
                [
                    "--market", "a_share", "--agent", "codex",
                    "--as-of", "2026-08-08", "run-classical-tournament",
                    "--offline", "--repo-root", tmp,
                    "--account-scope", "hs300", "--horizon", "3",
                ]
            )

        self.assertEqual(code, 0)
        tournament.assert_called_once_with(
            account_scope="hs300",
            horizon=3,
            force=False,
        )

    def test_cli_dispatches_development_only_cross_sectional_repair(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.run_cross_sectional_alpha_repair",
            return_value={"status": "development_pass", "results": []},
        ) as repair:
            code = main(
                [
                    "--market", "a_share", "--agent", "codex",
                    "--as-of", "2026-08-07", "run-cross-sectional-alpha-repair",
                    "--offline", "--repo-root", tmp,
                    "--account-scope", "zz500", "--horizon", "20",
                ]
            )

        self.assertEqual(code, 0)
        repair.assert_called_once_with(account_scope="zz500", horizon=20)

    def test_cli_dispatches_provider_market_baseline_first_research(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.run_baseline_first_research",
            return_value={"status": "baseline_wins", "results": []},
        ) as evaluate:
            code = main(
                [
                    "--market", "cn_qdii_etf", "--agent", "codex",
                    "--as-of", "2026-08-07", "run-baseline-first-research",
                    "--offline", "--repo-root", tmp,
                    "--account-scope", "us_exposure",
                ]
            )

        self.assertEqual(code, 0)
        evaluate.assert_called_once_with(
            account_scope="us_exposure",
            horizon=None,
        )

    def test_cli_binds_baseline_research_to_verified_training_bundle(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.run_baseline_first_research",
            return_value={"status": "baseline_wins", "results": []},
        ) as evaluate:
            bundle = Path(tmp) / "input"
            relative = Path("data/research/training-input.txt")
            payload = bundle / "payload" / relative
            installed = Path(tmp) / relative
            payload.parent.mkdir(parents=True)
            installed.parent.mkdir(parents=True)
            payload.write_bytes(b"input")
            installed.write_bytes(b"input")
            manifest = {
                "schema_version": 1,
                "kind": "research_training_input",
                "market": "a_share",
                "as_of": "2026-08-08",
                "snapshot_date": "20260807",
                "read_only_input": True,
                "files": [{
                    "path": str(relative),
                    "sha256": hashlib.sha256(b"input").hexdigest(),
                    "size": 5,
                }],
            }
            manifest["source_fingerprint"] = manifest_source_fingerprint(
                manifest
            )
            (bundle / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            code = main([
                "--market", "a_share", "--agent", "codex",
                "--as-of", "2026-08-08", "run-baseline-first-research",
                "--offline", "--repo-root", tmp,
                "--training-input-bundle", str(bundle),
            ])

        self.assertEqual(code, 0)
        evaluate.assert_called_once_with(
            account_scope=None,
            horizon=None,
            training_input={
                "market": "a_share",
                "snapshot_date": "20260807",
                "source_fingerprint": manifest["source_fingerprint"],
                "files": [str(relative)],
            },
        )

    def test_cli_dispatches_latest_snapshot_label_refresh(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.refresh_labels",
            return_value={"status": "complete", "snapshot_date": "20260807"},
        ) as refresh:
            code = main([
                "--market", "cn_qdii_etf", "--agent", "codex",
                "--as-of", "2026-08-09", "refresh-research-labels",
                "--offline", "--repo-root", tmp,
            ])

        self.assertEqual(code, 0)
        refresh.assert_called_once_with()

    def test_cli_dispatches_frozen_regime_tabular_alpha_config(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.run_regime_tabular_alpha",
            return_value={"status": "research", "development_gate": {"passed": False}},
        ) as evaluate:
            code = main(
                [
                    "--market", "a_share", "--agent", "codex",
                    "--as-of", "2026-08-07", "run-regime-tabular-alpha",
                    "--offline", "--repo-root", tmp,
                    "--config", "configs/research/classical_model.yaml",
                ]
            )

        self.assertEqual(code, 0)
        evaluate.assert_called_once_with(
            config_path=Path("configs/research/classical_model.yaml")
        )

    def test_cli_dispatches_forward_freeze_and_daily_observer(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.freeze_regime_tabular_forward",
            return_value={"status": "frozen"},
        ) as freeze:
            freeze_code = main([
                "--market", "a_share", "--agent", "codex",
                "--as-of", "2026-08-07", "freeze-regime-tabular-forward",
                "--offline", "--repo-root", tmp,
                "--source-report", "reports/research/best.json",
                "--observation-start", "20260810",
            ])
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.run_regime_tabular_forward",
            return_value={"status": "observing"},
        ) as observe:
            observe_code = main([
                "--market", "a_share", "--agent", "codex",
                "--as-of", "2026-08-11", "run-regime-tabular-forward",
                "--offline", "--repo-root", tmp,
            ])

        self.assertEqual(freeze_code, 0)
        freeze.assert_called_once_with(
            config_path=Path("configs/research/classical_model.yaml"),
            source_report=Path("reports/research/best.json"),
            observation_start="20260810",
        )
        self.assertEqual(observe_code, 0)
        observe.assert_called_once_with(
            config_path=Path("configs/research/classical_model.yaml")
        )

    def test_cli_dispatches_prepare_with_explicit_root(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.prepare_data",
            return_value={"status": "built", "rows": 12},
        ) as prepare:
            code = main(
                [
                    "--market", "a_share", "--agent", "codex", "--as-of", "2026-07-10",
                    "prepare-research-data", "--offline", "--repo-root", tmp,
                ]
            )

        self.assertEqual(code, 0)
        prepare.assert_called_once_with(force=False)

    def test_predict_fallback_returns_nonzero_for_systemd(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.predict",
            return_value={
                "status": "fallback",
                "predictions": 0,
                "error": "feature snapshot missing",
            },
        ):
            code = main(
                [
                    "--market", "a_share", "--agent", "codex",
                    "--as-of", "2026-08-08", "predict", "--offline",
                    "--repo-root", tmp,
                ]
            )

        self.assertEqual(code, 2)

    def test_parser_accepts_local_training_transfer_commands(self):
        parser = build_parser()

        export_args = parser.parse_args([
            "--market", "a_share", "--as-of", "2026-08-08",
            "research-training-bundle-export", "--output", "/tmp/input",
        ])
        import_args = parser.parse_args([
            "research-model-bundle-import", "--bundle", "/tmp/output",
            "--training-input-bundle", "/tmp/input",
        ])

        self.assertEqual(export_args.command, "research-training-bundle-export")
        self.assertEqual(import_args.command, "research-model-bundle-import")


if __name__ == "__main__":
    unittest.main()
