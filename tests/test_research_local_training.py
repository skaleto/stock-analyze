from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.local_training import (
    export_model_bundle,
    export_research_result_bundle,
    export_training_bundle,
    import_model_bundle,
    import_research_result_bundle,
    install_training_bundle,
    manifest_source_fingerprint,
    verify_transfer_bundle,
)
from stock_analyze.research.storage import ResearchStore


class ResearchLocalTrainingTest(unittest.TestCase):
    TRAINING_FILE_CONTENT = b"training-input"
    TRAINING_FILE = {
        "path": "data/research/training-input.txt",
        "sha256": hashlib.sha256(TRAINING_FILE_CONTENT).hexdigest(),
        "size": len(TRAINING_FILE_CONTENT),
    }
    TRAINING_MANIFEST_BASE = {
        "schema_version": 1,
        "kind": "research_training_input",
        "market": "a_share",
        "as_of": "2026-08-07",
        "snapshot_date": "20260807",
        "read_only_input": True,
        "files": [TRAINING_FILE],
    }
    TRAINING_FINGERPRINT = manifest_source_fingerprint(TRAINING_MANIFEST_BASE)

    @staticmethod
    def _write_qualified_snapshot(
        repo: Path,
        snapshot_date: str,
        *,
        signal: float = 1.0,
    ) -> None:
        store = ResearchStore(repo / "data/research")
        store.write_feature_snapshot(
            "a_share",
            snapshot_date,
            pd.DataFrame([
                {
                    "code": "000001",
                    "trade_date": "20180102",
                    "benchmark_code": "000300",
                    "signal": signal,
                },
                {
                    "code": "000001",
                    "trade_date": snapshot_date,
                    "benchmark_code": "000300",
                    "signal": signal,
                },
            ]),
        )
        store.write_label_snapshot(
            "a_share",
            snapshot_date,
            pd.DataFrame([
                {
                    "code": "000001",
                    "trade_date": "20180102",
                    "horizon": 3,
                    "label_contract_version": "next-open-v3-adjusted",
                },
                {
                    "code": "000001",
                    "trade_date": snapshot_date,
                    "horizon": 3,
                    "label_contract_version": "next-open-v3-adjusted",
                },
            ]),
        )
        manifest = (
            repo / "data/research/raw/a_share" / snapshot_date
            / "materialization_manifest.json"
        )
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({
            "status": "complete",
            "schema_version": "a-share-materialization-v1",
            "market": "a_share",
            "as_of": snapshot_date,
            "start": "2018-01-01",
            "end": (
                f"{snapshot_date[:4]}-{snapshot_date[4:6]}-"
                f"{snapshot_date[6:8]}"
            ),
            "historical_union_count": 1,
        }), encoding="utf-8")

    @classmethod
    def _write_training_input_bundle(
        cls,
        destination: Path,
        *,
        content: bytes | None = None,
    ) -> Path:
        destination.mkdir(parents=True)
        payload_content = content or cls.TRAINING_FILE_CONTENT
        relative = Path("data/research/training-input.txt")
        payload = destination / "payload" / relative
        payload.parent.mkdir(parents=True)
        payload.write_bytes(payload_content)
        file_record = {
            "path": str(relative),
            "sha256": hashlib.sha256(payload_content).hexdigest(),
            "size": len(payload_content),
        }
        manifest = {
            "schema_version": 1,
            "kind": "research_training_input",
            "market": "a_share",
            "as_of": "2026-08-07",
            "snapshot_date": "20260807",
            "read_only_input": True,
            "files": [file_record],
        }
        manifest["source_fingerprint"] = manifest_source_fingerprint(manifest)
        (destination / "manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        return destination

    def test_shadow_model_export_merges_without_replacing_ecs_champion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            model_relative = Path("data/research/models/a_share/hs300/3")
            tournament = model_relative / "tournaments/20260807"
            artifact_relative = tournament / "candidates/ridge/model-v1.joblib"
            artifact = source / artifact_relative
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"model")
            artifact.with_suffix(".metadata.json").write_text(
                '{"model_version":"model-v1","account_scope":"hs300"}',
                encoding="utf-8",
            )
            report_path = source / tournament / "report.json"
            report_path.write_text(json.dumps({
                "market": "a_share",
                "account_scope": "hs300",
                "horizon": 3,
                "as_of": "2026-08-07",
                "formal_strategy_activated": False,
                "training_input": {
                    "market": "a_share",
                    "snapshot_date": "20260807",
                    "source_fingerprint": self.TRAINING_FINGERPRINT,
                },
                "candidates": [{
                    "model_version": "model-v1",
                    "status": "shadow",
                    "artifact": str(artifact),
                }],
            }), encoding="utf-8")
            registry_path = source / model_relative / "registry.json"
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(json.dumps({
                "champion_model_version": "old-local",
                "models": {
                    "model-v1": {
                        "status": "shadow",
                        "artifact": str(artifact),
                        "role_status": {"ranker": "shadow", "portfolio": "shadow"},
                    }
                },
            }), encoding="utf-8")
            target_registry = target / model_relative / "registry.json"
            target_registry.parent.mkdir(parents=True, exist_ok=True)
            target_registry.write_text(json.dumps({
                "champion_model_version": "ecs-active",
                "models": {"ecs-active": {"status": "active"}},
            }), encoding="utf-8")

            input_bundle = self._write_training_input_bundle(base / "training-input")
            exported = export_model_bundle(source, report_path, base / "model-bundle")
            imported = import_model_bundle(
                target,
                base / "model-bundle",
                training_input_bundle=input_bundle,
            )
            state = json.loads(target_registry.read_text(encoding="utf-8"))

        self.assertEqual(exported["kind"], "research_model_output")
        self.assertEqual(
            exported["training_input_fingerprint"],
            self.TRAINING_FINGERPRINT,
        )
        self.assertEqual(imported["status"], "imported")
        self.assertEqual(state["champion_model_version"], "ecs-active")
        self.assertIn("model-v1", state["models"])
        self.assertEqual(state["models"]["model-v1"]["status"], "shadow")

    def test_training_snapshot_bundle_round_trips_with_hash_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            for repo in (source, target):
                (repo / "configs").mkdir(parents=True)
                (repo / "configs/competition_a_share.yaml").write_text(
                    '{"competition_id":"fixture"}', encoding="utf-8"
                )
                (repo / "configs/intelligence_factors.json").write_text(
                    '{"factors":{}}', encoding="utf-8"
                )
            self._write_qualified_snapshot(source, "20260807")
            window_manifest = (
                source / "data/research/baseline_first/a_share/hs300"
                / "window_manifest.json"
            )
            window_manifest.parent.mkdir(parents=True)
            window_manifest.write_text('{"payload":{"frozen":true}}', encoding="utf-8")

            manifest = export_training_bundle(
                source,
                market="a_share",
                as_of="2026-08-07",
                destination=base / "bundle",
            )
            verified = verify_transfer_bundle(base / "bundle")
            installed = install_training_bundle(target, base / "bundle")

            target_features = ResearchStore(target / "data/research").read_feature_snapshot(
                "a_share", "2026-08-07"
            )
            target_window_manifest = (
                target / "data/research/baseline_first/a_share/hs300"
                / "window_manifest.json"
            ).is_file()

        self.assertEqual(manifest["kind"], "research_training_input")
        self.assertEqual(
            manifest["snapshot_qualification"]["contract"],
            "full-history-training-input-v1",
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(installed["status"], "installed")
        self.assertEqual(target_features.iloc[0]["code"], "000001")
        self.assertTrue(target_window_manifest)

    def test_training_bundle_rejects_same_destination_after_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            (root / "configs").mkdir(parents=True)
            (root / "configs/competition_a_share.yaml").write_text(
                '{"competition_id":"fixture"}', encoding="utf-8"
            )
            (root / "configs/intelligence_factors.json").write_text(
                '{"factors":{}}', encoding="utf-8"
            )
            self._write_qualified_snapshot(root, "20260807")
            destination = Path(tmp) / "bundle"
            export_training_bundle(
                root,
                market="a_share",
                as_of="2026-08-07",
                destination=destination,
            )
            self._write_qualified_snapshot(root, "20260807", signal=2.0)

            with self.assertRaisesRegex(
                ValueError,
                "training_bundle_destination_stale",
            ):
                export_training_bundle(
                    root,
                    market="a_share",
                    as_of="2026-08-07",
                    destination=destination,
                )

    def test_training_bundle_skips_newer_truncated_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            (root / "configs").mkdir(parents=True)
            (root / "configs/competition_a_share.yaml").write_text(
                '{"competition_id":"fixture"}', encoding="utf-8"
            )
            (root / "configs/intelligence_factors.json").write_text(
                '{"factors":{}}', encoding="utf-8"
            )
            self._write_qualified_snapshot(root, "20260807")
            store = ResearchStore(root / "data/research")
            store.write_feature_snapshot(
                "a_share",
                "2026-08-08",
                pd.DataFrame([{
                    "code": "000001",
                    "trade_date": "20260808",
                    "benchmark_code": "000300",
                }]),
            )
            store.write_label_snapshot(
                "a_share",
                "2026-08-08",
                pd.DataFrame([{
                    "code": "000001",
                    "trade_date": "20260808",
                    "horizon": 3,
                    "label_contract_version": "next-open-v3-adjusted",
                }]),
            )

            manifest = export_training_bundle(
                root,
                market="a_share",
                as_of="2026-08-08",
                destination=Path(tmp) / "bundle",
            )

        self.assertEqual(manifest["snapshot_date"], "20260807")
        self.assertEqual(
            manifest["rejected_newer_snapshots"][0]["snapshot_date"],
            "20260808",
        )
        self.assertIn(
            "training_snapshot_history_shortfall",
            manifest["rejected_newer_snapshots"][0]["reason"],
        )

    def test_model_import_never_overwrites_same_version_active_champion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            model_relative = Path("data/research/models/a_share/hs300/3")
            tournament = model_relative / "tournaments/20260807"
            artifact_relative = tournament / "candidates/ridge/model-v1.joblib"
            artifact = source / artifact_relative
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"model")
            report_path = source / tournament / "report.json"
            report_path.write_text(json.dumps({
                "market": "a_share",
                "account_scope": "hs300",
                "horizon": 3,
                "as_of": "2026-08-07",
                "formal_strategy_activated": False,
                "training_input": {
                    "market": "a_share",
                    "snapshot_date": "20260807",
                    "source_fingerprint": self.TRAINING_FINGERPRINT,
                },
                "candidates": [{
                    "model_version": "model-v1",
                    "status": "shadow",
                    "artifact": str(artifact),
                }],
            }), encoding="utf-8")
            registry_path = source / model_relative / "registry.json"
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(json.dumps({
                "champion_model_version": None,
                "models": {
                    "model-v1": {
                        "status": "shadow",
                        "artifact": str(artifact),
                        "role_status": {"ranker": "shadow"},
                    }
                },
            }), encoding="utf-8")
            export_model_bundle(source, report_path, base / "model-bundle")
            target_registry = target / model_relative / "registry.json"
            target_registry.parent.mkdir(parents=True, exist_ok=True)
            target_registry.write_text(json.dumps({
                "champion_model_version": "model-v1",
                "champion_model_versions": {"ranker": "model-v1"},
                "models": {
                    "model-v1": {
                        "status": "active",
                        "artifact": "/protected/active.joblib",
                        "role_status": {"ranker": "active"},
                        "gate_history": [{"passed": True}],
                    }
                },
            }), encoding="utf-8")

            input_bundle = self._write_training_input_bundle(base / "training-input")
            imported = import_model_bundle(
                target,
                base / "model-bundle",
                training_input_bundle=input_bundle,
            )
            state = json.loads(target_registry.read_text(encoding="utf-8"))

        self.assertEqual(imported["existing_model_versions"], ["model-v1"])
        self.assertEqual(state["models"]["model-v1"]["status"], "active")
        self.assertEqual(
            state["models"]["model-v1"]["artifact"],
            "/protected/active.joblib",
        )
        self.assertEqual(state["models"]["model-v1"]["gate_history"], [{"passed": True}])

    def test_model_bundle_rejects_same_destination_after_artifact_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            model_relative = Path("data/research/models/a_share/hs300/3")
            tournament = model_relative / "tournaments/20260807"
            artifact_relative = tournament / "candidates/ridge/model-v1.joblib"
            artifact = source / artifact_relative
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"model-v1")
            report_path = source / tournament / "report.json"
            report_path.write_text(json.dumps({
                "market": "a_share",
                "account_scope": "hs300",
                "horizon": 3,
                "as_of": "2026-08-07",
                "formal_strategy_activated": False,
                "training_input": {
                    "market": "a_share",
                    "snapshot_date": "20260807",
                    "source_fingerprint": self.TRAINING_FINGERPRINT,
                },
                "candidates": [{
                    "model_version": "model-v1",
                    "status": "shadow",
                    "artifact": str(artifact),
                }],
            }), encoding="utf-8")
            registry_path = source / model_relative / "registry.json"
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(json.dumps({
                "champion_model_version": None,
                "models": {
                    "model-v1": {
                        "status": "shadow",
                        "artifact": str(artifact),
                        "role_status": {"ranker": "shadow"},
                    }
                },
            }), encoding="utf-8")
            destination = base / "model-bundle"
            export_model_bundle(source, report_path, destination)
            artifact.write_bytes(b"changed-model-v1")

            with self.assertRaisesRegex(
                ValueError,
                "model_bundle_destination_stale",
            ):
                export_model_bundle(source, report_path, destination)

    def test_model_import_rejects_mismatched_training_input_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            model_relative = Path("data/research/models/a_share/hs300/3")
            tournament = model_relative / "tournaments/20260807"
            artifact = source / tournament / "candidates/ridge/model-v1.joblib"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"model")
            report_path = source / tournament / "report.json"
            report_path.write_text(json.dumps({
                "market": "a_share",
                "account_scope": "hs300",
                "horizon": 3,
                "as_of": "2026-08-07",
                "formal_strategy_activated": False,
                "training_input": {
                    "market": "a_share",
                    "snapshot_date": "20260807",
                    "source_fingerprint": self.TRAINING_FINGERPRINT,
                },
                "candidates": [{
                    "model_version": "model-v1",
                    "status": "shadow",
                    "artifact": str(artifact),
                }],
            }), encoding="utf-8")
            registry = source / model_relative / "registry.json"
            registry.parent.mkdir(parents=True, exist_ok=True)
            registry.write_text(json.dumps({
                "models": {
                    "model-v1": {
                        "status": "shadow",
                        "artifact": str(artifact),
                    }
                }
            }), encoding="utf-8")
            export_model_bundle(source, report_path, base / "model-bundle")
            mismatched = self._write_training_input_bundle(
                base / "mismatched-input",
                content=b"different-training-input",
            )

            with self.assertRaisesRegex(
                ValueError,
                "model_bundle_training_input_fingerprint_mismatch",
            ):
                import_model_bundle(
                    target,
                    base / "model-bundle",
                    training_input_bundle=mismatched,
                )

    def test_research_result_bundle_updates_reports_without_registry_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            target = base / "target"
            reports = source / "reports/research"
            reports.mkdir(parents=True)
            report_json = reports / "baseline_first_20260807_hs300.json"
            report_md = reports / "baseline_first_20260807_hs300.md"
            report_json.write_text('{"status":"baseline_wins"}', encoding="utf-8")
            report_md.write_text("# Baseline wins\n", encoding="utf-8")
            derived = (
                source
                / "data/research/baseline_first_derived/a_share/hs300/window.json"
            )
            derived.parent.mkdir(parents=True)
            derived.write_text(json.dumps({
                "schema_version": 1,
                "declaration_id": "window-v1",
                "payload": {"development_start": "20200101"},
            }), encoding="utf-8")
            result = base / "result.json"
            result.write_text(json.dumps({
                "market": "a_share",
                "snapshot_date": "20260807",
                "results": [{
                    "json_path": str(report_json),
                    "report_path": str(report_md),
                    "window_manifest": {
                        "source_path": str(derived),
                        "target_path": (
                            "data/research/baseline_first/a_share/hs300/"
                            "window_manifest.json"
                        ),
                    },
                }],
            }), encoding="utf-8")
            registry = target / "data/research/models/a_share/hs300/20/registry.json"
            registry.parent.mkdir(parents=True)
            registry.write_text('{"champion_model_version":"active"}', encoding="utf-8")
            before = registry.read_text(encoding="utf-8")
            input_bundle = self._write_training_input_bundle(base / "training-input")

            exported = export_research_result_bundle(
                source,
                result,
                input_bundle,
                base / "result-bundle",
            )
            imported = import_research_result_bundle(
                target,
                base / "result-bundle",
                training_input_bundle=input_bundle,
            )
            registry_after = registry.read_text(encoding="utf-8")
            report_installed = (
                target / "reports/research/baseline_first_20260807_hs300.json"
            ).is_file()
            window_installed = (
                target
                / "data/research/baseline_first/a_share/hs300/window_manifest.json"
            ).is_file()

        self.assertEqual(exported["kind"], "research_evaluation_output")
        self.assertFalse(imported["registry_mutated"])
        self.assertEqual(registry_after, before)
        self.assertTrue(report_installed)
        self.assertTrue(window_installed)

    def test_tampered_bundle_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            payload = bundle / "payload/data.txt"
            payload.parent.mkdir(parents=True)
            payload.write_text("before", encoding="utf-8")
            manifest = {
                "schema_version": 1,
                "kind": "research_training_input",
                "market": "a_share",
                "as_of": "2026-08-07",
                "snapshot_date": "20260807",
                "read_only_input": True,
                "files": [{
                    "path": "data.txt",
                    "sha256": "invalid",
                    "size": 6,
                }],
            }
            manifest["source_fingerprint"] = manifest_source_fingerprint(
                manifest
            )
            (bundle / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "transfer_bundle_hash_mismatch"):
                verify_transfer_bundle(bundle)

    def test_empty_or_self_declared_transfer_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            empty = {
                "schema_version": 1,
                "kind": "research_training_input",
                "files": [],
            }
            empty["source_fingerprint"] = manifest_source_fingerprint(empty)
            (bundle / "manifest.json").write_text(
                json.dumps(empty),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "transfer_bundle_files_empty"):
                verify_transfer_bundle(bundle)

            payload = bundle / "payload/data.txt"
            payload.parent.mkdir(parents=True)
            payload.write_text("content", encoding="utf-8")
            tampered = {
                "schema_version": 1,
                "kind": "research_training_input",
                "files": [{
                    "path": "data.txt",
                    "sha256": hashlib.sha256(b"content").hexdigest(),
                    "size": 7,
                }],
                "source_fingerprint": "b" * 64,
            }
            (bundle / "manifest.json").write_text(
                json.dumps(tampered),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "transfer_bundle_source_fingerprint_mismatch",
            ):
                verify_transfer_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
