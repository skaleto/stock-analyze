from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.research.campaign_report import write_final_campaign_report


class ResearchCampaignReportTest(unittest.TestCase):
    def test_final_report_requires_all_four_terminal_scope_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "campaign_final_scope_count"):
                write_final_campaign_report(
                    Path(tmp),
                    campaign_id="campaign-1",
                    manifest_hash="hash-1",
                    scopes=[{
                        "market": "a_share",
                        "account_scope": "hs300",
                        "status": "falsified",
                        "reasons": ["no_candidate"],
                    }],
                )

    def test_final_report_is_machine_readable_and_never_activates_formal_strategy(self) -> None:
        scopes = [
            {"market": "a_share", "account_scope": "hs300", "status": "falsified", "reasons": ["gate_1"]},
            {"market": "a_share", "account_scope": "zz500", "status": "insufficient_data", "reasons": ["pit"]},
            {"market": "cn_qdii_etf", "account_scope": "hk_exposure", "status": "baseline_only", "reasons": ["ml_no_increment"]},
            {"market": "cn_qdii_etf", "account_scope": "us_exposure", "status": "shadow_ready", "reasons": []},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = write_final_campaign_report(
                Path(tmp),
                campaign_id="campaign-1",
                manifest_hash="hash-1",
                scopes=scopes,
            )
            payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "complete")
        self.assertFalse(payload["formal_strategy_activated"])
        self.assertIsNone(payload["champion_model_version"])
        self.assertEqual(len(payload["scopes"]), 4)


if __name__ == "__main__":
    unittest.main()
