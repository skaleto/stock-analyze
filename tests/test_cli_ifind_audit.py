from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from stock_analyze.cli import main


class IfindAuditCliTest(unittest.TestCase):
    def test_cli_routes_full_market_supplement_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auditor = Mock()
            auditor.resolve_as_of.return_value = "2026-07-24"
            auditor.run.return_value = {
                "status": "success",
                "as_of": "2026-07-24",
                "datasets": {},
                "report_path": str(root / "report.json"),
            }
            with patch(
                "stock_analyze.intelligence.cross_source.CrossSourceAuditor",
                return_value=auditor,
            ):
                result = main(
                    [
                        "intelligence-source-audit",
                        "--repo-root",
                        str(root),
                        "--as-of",
                        "2026-07-24",
                        "--datasets",
                        "market",
                        "announcement",
                        "--announcement-scope",
                        "full-market",
                        "--supplement",
                    ]
                )

        self.assertEqual(result, 0)
        auditor.run.assert_called_once_with(
            as_of="2026-07-24",
            datasets={"market", "announcement"},
            full_market_announcements=True,
            announcement_codes=(),
            supplement=True,
        )

    def test_cli_uses_operational_codes_when_not_full_market(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auditor = Mock()
            auditor.resolve_as_of.return_value = "2026-07-24"
            auditor.operational_codes.return_value = (
                "000001.SZ",
                "513100.SH",
            )
            auditor.run.return_value = {
                "status": "success",
                "as_of": "2026-07-24",
                "datasets": {},
            }
            with patch(
                "stock_analyze.intelligence.cross_source.CrossSourceAuditor",
                return_value=auditor,
            ):
                result = main(
                    [
                        "intelligence-source-audit",
                        "--repo-root",
                        str(root),
                        "--datasets",
                        "announcement",
                    ]
                )

        self.assertEqual(result, 0)
        auditor.resolve_as_of.assert_called_once_with(None)
        auditor.run.assert_called_once_with(
            as_of="2026-07-24",
            datasets={"announcement"},
            full_market_announcements=False,
            announcement_codes=("000001.SZ", "513100.SH"),
            supplement=False,
        )


if __name__ == "__main__":
    unittest.main()
