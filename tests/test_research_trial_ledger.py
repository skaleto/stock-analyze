import tempfile
import unittest
from pathlib import Path

from stock_analyze.research.trial_ledger import (
    CampaignLedger,
    DEFAULT_CLASSICAL_TRIAL_SPECS,
    TrialLedger,
)


class ResearchTrialLedgerTest(unittest.TestCase):
    def test_bounded_declaration_refuses_a_fourth_trial(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TrialLedger(Path(tmp) / "trial_ledger.json")

            with self.assertRaisesRegex(ValueError, "trial_ledger_spec_budget:3"):
                ledger.declare(
                    family_id="baseline-first-v1",
                    objective="candidate_incremental_net_return",
                    specs=[
                        {"spec_id": f"trial-{index}", "family": "fixture"}
                        for index in range(4)
                    ],
                    max_specs=3,
                )

    def test_declaration_is_idempotent_and_monthly_retrain_does_not_add_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TrialLedger(Path(tmp) / "trial_ledger.json")
            first = ledger.declare(
                family_id="a_share:5:classic-v1",
                specs=DEFAULT_CLASSICAL_TRIAL_SPECS,
                objective="exact_net_active_return",
            )
            second = ledger.declare(
                family_id="a_share:5:classic-v1",
                specs=DEFAULT_CLASSICAL_TRIAL_SPECS,
                objective="exact_net_active_return",
            )

        self.assertEqual(first["declaration_id"], second["declaration_id"])
        self.assertEqual(len(second["specs"]), 5)
        self.assertEqual(second["declaration_count"], 1)

    def test_changed_specification_requires_a_new_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TrialLedger(Path(tmp) / "trial_ledger.json")
            ledger.declare(
                family_id="a_share:5:classic-v1",
                specs=DEFAULT_CLASSICAL_TRIAL_SPECS,
                objective="exact_net_active_return",
            )

            with self.assertRaisesRegex(ValueError, "trial_ledger_declaration_mismatch"):
                ledger.declare(
                    family_id="a_share:5:classic-v1",
                    specs=DEFAULT_CLASSICAL_TRIAL_SPECS[:-1],
                    objective="exact_net_active_return",
                )

    def test_finalize_records_run_results_without_mutating_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TrialLedger(Path(tmp) / "trial_ledger.json")
            declared = ledger.declare(
                family_id="a_share:5:classic-v1",
                specs=DEFAULT_CLASSICAL_TRIAL_SPECS,
                objective="exact_net_active_return",
            )
            finalized = ledger.finalize(
                run_id="20260808:model-v1",
                declaration_id=declared["declaration_id"],
                results=[{"spec_id": "ridge_ranker", "sharpe": 0.4}],
            )

        self.assertEqual(len(finalized["runs"]), 1)
        self.assertEqual(finalized["runs"][0]["run_id"], "20260808:model-v1")
        self.assertEqual(len(finalized["specs"]), 5)

    def test_campaign_manifest_is_immutable_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CampaignLedger(Path(tmp))
            payload = {
                "campaign_id": "strategy-recovery-20260814-v1",
                "source_commit": "abc123",
                "simulator_version": "paper-parity-daily-v1",
                "input_fingerprints": ["a", "b"],
                "thresholds": {"maximum_drawdown": 0.25},
                "transparent_specs": [
                    {"spec_id": f"rule-{index}", "spec_hash": f"h{index}"}
                    for index in range(24)
                ],
                "incremental_specs": [],
            }

            first = ledger.declare(payload)
            second = ledger.declare(payload)

            self.assertEqual(first["manifest_hash"], second["manifest_hash"])
            self.assertEqual(first["declaration_count"], 1)
            with self.assertRaisesRegex(ValueError, "campaign_manifest_mismatch"):
                ledger.declare({**payload, "source_commit": "changed"})

    def test_campaign_enforces_stage_and_total_trial_budgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CampaignLedger(Path(tmp))
            payload = {
                "campaign_id": "strategy-recovery-20260814-v1",
                "source_commit": "abc123",
                "simulator_version": "paper-parity-daily-v1",
                "input_fingerprints": ["a", "b"],
                "thresholds": {},
                "transparent_specs": [
                    {"spec_id": f"rule-{index}", "spec_hash": f"r{index}"}
                    for index in range(24)
                ],
                "incremental_specs": [
                    {"spec_id": f"ml-{index}", "spec_hash": f"m{index}"}
                    for index in range(8)
                ],
            }
            manifest = ledger.declare(payload)
            for index in range(24):
                ledger.record_trial(
                    manifest_hash=manifest["manifest_hash"],
                    stage="transparent",
                    trial={"trial_id": f"rule-{index}", "spec_hash": f"r{index}"},
                )
            for index in range(8):
                ledger.record_trial(
                    manifest_hash=manifest["manifest_hash"],
                    stage="incremental_ml",
                    trial={"trial_id": f"ml-{index}", "spec_hash": f"m{index}"},
                )

            repeated = ledger.record_trial(
                manifest_hash=manifest["manifest_hash"],
                stage="transparent",
                trial={"trial_id": "rule-0", "spec_hash": "r0"},
            )
            self.assertTrue(repeated["idempotent"])
            with self.assertRaisesRegex(ValueError, "campaign_budget_exhausted"):
                ledger.record_trial(
                    manifest_hash=manifest["manifest_hash"],
                    stage="incremental_ml",
                    trial={"trial_id": "ml-extra", "spec_hash": "extra"},
                )


if __name__ == "__main__":
    unittest.main()
