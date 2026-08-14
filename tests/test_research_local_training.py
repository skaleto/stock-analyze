from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.local_training import (
    export_model_bundle,
    export_training_bundle,
    import_model_bundle,
    install_training_bundle,
    verify_transfer_bundle,
)
from stock_analyze.research.storage import ResearchStore


class ResearchLocalTrainingTest(unittest.TestCase):
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

            exported = export_model_bundle(source, report_path, base / "model-bundle")
            imported = import_model_bundle(target, base / "model-bundle")
            state = json.loads(target_registry.read_text(encoding="utf-8"))

        self.assertEqual(exported["kind"], "research_model_output")
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
            store = ResearchStore(source / "data/research")
            store.write_feature_snapshot(
                "a_share",
                "2026-08-07",
                pd.DataFrame([{"code": "000001", "trade_date": "20260807", "signal": 1.0}]),
            )
            store.write_label_snapshot(
                "a_share",
                "2026-08-07",
                pd.DataFrame([{"code": "000001", "trade_date": "20260807", "horizon": 3, "label": "up"}]),
            )
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
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(installed["status"], "installed")
        self.assertEqual(target_features.iloc[0]["code"], "000001")
        self.assertTrue(target_window_manifest)

    def test_tampered_bundle_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            payload = bundle / "payload/data.txt"
            payload.parent.mkdir(parents=True)
            payload.write_text("before", encoding="utf-8")
            (bundle / "manifest.json").write_text(json.dumps({
                "schema_version": 1,
                "kind": "research_training_input",
                "files": [{
                    "path": "data.txt",
                    "sha256": "invalid",
                    "size": 6,
                }],
            }), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "transfer_bundle_hash_mismatch"):
                verify_transfer_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
