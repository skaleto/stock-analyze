from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from stock_analyze.research.a_share_all_cap_contract import load_all_cap_contract
from stock_analyze.research.a_share_all_cap_universe import _contract_hash
from stock_analyze.research.a_share_all_cap_holdout import (
    canonical_hash,
    contract_sha256,
    open_holdout,
    run_development,
    run_holdout,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "configs/research/a_share_all_cap_v2.yaml"


class AShareAllCapHoldoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = load_all_cap_contract(CONTRACT_PATH)
        self.store = self.root / "data/research/a_share_all_cap/v1"
        self.store.mkdir(parents=True)
        self.manifests = self._write_bound_manifests()

    @staticmethod
    def _with_hash(payload: dict[str, object]) -> dict[str, object]:
        result = dict(payload)
        result["manifest_sha256"] = canonical_hash(result)
        return result

    def _write_manifest(
        self,
        relative: str,
        payload: dict[str, object],
    ) -> Path:
        path = self.store / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                self._with_hash(payload),
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return path

    def _write_bound_manifests(self) -> dict[str, Path]:
        contract_hash = contract_sha256(self.contract)
        source = self._write_manifest(
            "sources/publications/source/manifest.json",
            {
                "kind": "source",
                "campaign_id": self.contract.campaign_id,
                "start_date": "20180102",
                "end_date": "20260821",
            },
        )
        source_hash = json.loads(source.read_text())["manifest_sha256"]
        universe = self._write_manifest(
            "universe/publications/universe/manifest.json",
            {
                "kind": "universe",
                "campaign_id": self.contract.campaign_id,
                "contract_sha256": contract_hash,
                "source_manifest_sha256": source_hash,
            },
        )
        universe_hash = json.loads(universe.read_text())["manifest_sha256"]
        feature = self._write_manifest(
            "features/publications/features/manifest.json",
            {
                "kind": "feature",
                "campaign_id": self.contract.campaign_id,
                "contract_sha256": contract_hash,
                "source_manifest_sha256": source_hash,
                "universe_manifest_sha256": universe_hash,
            },
        )
        return {
            "source": source,
            "universe": universe,
            "feature": feature,
        }

    def _development(self, *, passed: bool = True) -> dict[str, object]:
        return run_development(
            repo_root=self.root,
            contract=self.contract,
            manifest_paths=self.manifests,
            load_evaluation=lambda: {
                "status": "pass" if passed else "fail",
                "gate": {"passed": passed},
                "observed_return_dates": ["20180102", "20241231"],
                "result": {"reasons": [] if passed else ["sleeve:micro"]},
            },
        )

    def test_development_is_content_addressed_and_binds_all_manifests(self) -> None:
        artifact = self._development()
        artifact_path = Path(str(artifact["artifact_path"]))
        persisted = json.loads(artifact_path.read_text(encoding="utf-8"))

        self.assertTrue(
            artifact_path.resolve().is_relative_to(self.store.resolve())
        )
        self.assertEqual(
            artifact_path.name,
            f"{artifact['artifact_sha256']}.json",
        )
        self.assertEqual(persisted["artifact_sha256"], artifact["artifact_sha256"])
        self.assertEqual(persisted["contract_sha256"], contract_sha256(self.contract))
        self.assertEqual(
            set(persisted["manifests"]),
            {"source", "universe", "feature"},
        )

    def test_contract_hash_matches_the_published_universe_contract_hash(self) -> None:
        self.assertEqual(
            contract_sha256(self.contract),
            _contract_hash(self.contract),
        )

    def test_development_rejects_any_2025_or_later_return_row(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "all_cap_development:window",
        ):
            run_development(
                repo_root=self.root,
                contract=self.contract,
                manifest_paths=self.manifests,
                load_evaluation=lambda: {
                    "status": "pass",
                    "gate": {"passed": True},
                    "observed_return_dates": ["20250102"],
                    "result": {},
                },
            )

        self.assertFalse((self.store / "development").exists())

    def test_development_rejects_future_rows_hidden_below_result(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "all_cap_development:window",
        ):
            run_development(
                repo_root=self.root,
                contract=self.contract,
                manifest_paths=self.manifests,
                load_evaluation=lambda: {
                    "status": "pass",
                    "gate": {"passed": True},
                    "observed_return_dates": ["20180102", "20241231"],
                    "result": {
                        "rows": [
                            {
                                "trade_date": "20241231",
                                "label_end_date": "20250102",
                                "net_return": 0.1,
                            }
                        ]
                    },
                },
            )

    def test_development_result_is_sealed_once_and_never_replaced(self) -> None:
        first = self._development()
        first_path = Path(str(first["artifact_path"]))
        first_bytes = first_path.read_bytes()

        with self.assertRaisesRegex(
            ValueError,
            "all_cap_development:already_sealed",
        ):
            self._development(passed=False)

        sealed = json.loads(
            (self.store / "development/sealed.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sealed["artifact_sha256"], first["artifact_sha256"])
        self.assertEqual(first_path.read_bytes(), first_bytes)

    def test_holdout_refuses_failed_development_gate(self) -> None:
        development = self._development(passed=False)

        with self.assertRaisesRegex(
            ValueError,
            "all_cap_holdout:development_gate",
        ):
            open_holdout(development, self.contract, self.root)

        self.assertFalse((self.store / "holdout/opened.json").exists())

    def test_holdout_refuses_bad_artifact_checksum(self) -> None:
        development = self._development()
        development["status"] = "fail"

        with self.assertRaisesRegex(
            ValueError,
            "all_cap_holdout:development_checksum",
        ):
            open_holdout(development, self.contract, self.root)

    def test_holdout_refuses_manifest_binding_mismatch(self) -> None:
        development = self._development()
        development["manifests"]["feature"]["manifest_sha256"] = "0" * 64
        unsigned = dict(development)
        unsigned.pop("artifact_sha256")
        unsigned.pop("artifact_path")
        development["artifact_sha256"] = canonical_hash(unsigned)

        with self.assertRaisesRegex(
            ValueError,
            "all_cap_holdout:manifest_binding",
        ):
            open_holdout(development, self.contract, self.root)

    def test_holdout_refuses_second_open_before_other_validation(self) -> None:
        development = self._development()
        first = open_holdout(development, self.contract, self.root)

        with self.assertRaisesRegex(
            ValueError,
            "all_cap_holdout:already_opened",
        ):
            open_holdout({"not": "valid"}, self.contract, self.root)

        self.assertTrue(first["immutable"])

    def test_holdout_accepts_a_passing_gate_with_report_fields(self) -> None:
        development = self._development()
        development["gate"] = {
            "passed": True,
            "reasons": [],
            "funded_sleeves": ["large", "mid", "small", "micro"],
        }
        unsigned = dict(development)
        unsigned.pop("artifact_sha256")
        unsigned.pop("artifact_path")
        development["artifact_sha256"] = canonical_hash(unsigned)

        opened = open_holdout(development, self.contract, self.root)

        self.assertTrue(opened["immutable"])

    def test_marker_exists_before_holdout_returns_are_read(self) -> None:
        development = self._development()
        marker = self.store / "holdout/opened.json"

        def load_evaluation() -> dict[str, object]:
            self.assertTrue(marker.exists())
            return {
                "status": "fail",
                "gate": {"passed": False},
                "observed_return_dates": ["20250102", "20260821"],
                "result": {"reasons": ["sleeve:micro"]},
            }

        result = run_holdout(
            repo_root=self.root,
            contract=self.contract,
            development=development,
            load_evaluation=load_evaluation,
        )

        self.assertEqual(result["status"], "fail")
        self.assertTrue(Path(str(result["artifact_path"])).exists())

    def test_holdout_result_is_never_replaced(self) -> None:
        development = self._development()
        first = run_holdout(
            repo_root=self.root,
            contract=self.contract,
            development=development,
            load_evaluation=lambda: {
                "status": "insufficient_data",
                "gate": {"passed": False},
                "observed_return_dates": [],
                "result": {"reasons": ["missing_returns"]},
            },
        )
        first_path = Path(str(first["artifact_path"]))
        first_bytes = first_path.read_bytes()

        with self.assertRaisesRegex(
            ValueError,
            "all_cap_holdout:already_opened",
        ):
            run_holdout(
                repo_root=self.root,
                contract=self.contract,
                development=development,
                load_evaluation=lambda: {
                    "status": "pass",
                    "gate": {"passed": True},
                    "observed_return_dates": ["20250102"],
                    "result": {},
                },
            )

        self.assertEqual(first_path.read_bytes(), first_bytes)

    def test_rejects_manifest_path_escape_and_symlink(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        escaped = dict(self.manifests)
        escaped["source"] = outside
        with self.assertRaisesRegex(ValueError, "all_cap_artifact:path"):
            run_development(
                repo_root=self.root,
                contract=self.contract,
                manifest_paths=escaped,
                load_evaluation=lambda: {},
            )

        link = self.store / "linked-feature.json"
        try:
            link.symlink_to(self.manifests["feature"])
        except OSError:
            self.skipTest("symlinks unavailable")
        linked = dict(self.manifests)
        linked["feature"] = link
        with self.assertRaisesRegex(ValueError, "all_cap_artifact:symlink"):
            run_development(
                repo_root=self.root,
                contract=self.contract,
                manifest_paths=linked,
                load_evaluation=lambda: {},
            )


if __name__ == "__main__":
    unittest.main()
