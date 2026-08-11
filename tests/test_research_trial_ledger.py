import tempfile
import unittest
from pathlib import Path

from stock_analyze.research.trial_ledger import (
    DEFAULT_CLASSICAL_TRIAL_SPECS,
    TrialLedger,
)


class ResearchTrialLedgerTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
