from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from stock_analyze.intelligence.diagnostics import (
    build_semantic_status_report,
    build_quality_report,
    write_semantic_status_report,
)
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument


class IntelligenceDiagnosticsTest(unittest.TestCase):
    def test_semantic_status_reports_the_configured_production_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "configs" / "intelligence_semantic.yaml"
            config.parent.mkdir(parents=True)
            config.write_text(
                "production_extraction_profile: test-mentions-v1\n"
                "parser:\n"
                "  version: announcement-layout-v1\n"
                "artifact_store:\n"
                "  local_root: data/shared/intelligence/artifacts\n",
                encoding="utf-8",
            )
            profile_dir = (
                root / "configs" / "intelligence_extraction_profiles"
            )
            profile_dir.mkdir(parents=True)
            (profile_dir / "test_mentions_v1.json").write_text(
                json.dumps(
                    {
                        "profile_id": "test-mentions-v1",
                        "prompt_version": "test-prompt-v1",
                        "schema_version": "test-schema-v1",
                        "taxonomy_version": "test-taxonomy-v1",
                    }
                ),
                encoding="utf-8",
            )

            report = build_semantic_status_report(root)

        self.assertEqual(report["versions"]["profile"], "test-mentions-v1")
        self.assertEqual(report["versions"]["prompt"], "test-prompt-v1")

    def test_semantic_status_ignores_non_values_in_rec_time_metadata(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "configs" / "intelligence_semantic.yaml"
            config.parent.mkdir(parents=True)
            config.write_text(
                "parser:\n"
                "  version: announcement-layout-v1\n"
                "artifact_store:\n"
                "  local_root: data/shared/intelligence/artifacts\n",
                encoding="utf-8",
            )
            store = IntelligenceStore(
                root / "data/shared/intelligence"
            )
            for source_id, rec_time in (
                ("invalid", "nan"),
                ("valid", "2026-07-29 12:30:00"),
            ):
                store.insert_document(
                    SourceDocument(
                        source="tushare_announcement",
                        source_id=source_id,
                        title=source_id,
                        published_at="2026-07-29T00:00:00Z",
                        first_seen_at="2026-07-29T00:01:00Z",
                        effective_at="2026-07-29T00:00:00Z",
                        source_url=f"https://example.com/{source_id}",
                        content=b"metadata",
                        metadata={"rec_time": rec_time},
                    )
                )

            report = build_semantic_status_report(root)

        self.assertEqual(
            report["metadata"]["latest_rec_time"],
            "2026-07-29 12:30:00",
        )
        self.assertEqual(report["metadata"]["total_documents"], 2)
        self.assertEqual(
            report["pipeline"]["stages"]["catalogued"],
            2,
        )
        self.assertEqual(
            report["pipeline"]["backlog"]["download"],
            2,
        )
        self.assertEqual(
            report["pipeline"]["sources"][0]["source"],
            "tushare_announcement",
        )
        self.assertNotIn("champion", report["versions"])
        self.assertEqual(
            report["versions"]["contract"],
            "semantic-extraction-job-v1",
        )

    def test_semantic_status_snapshots_keep_only_the_latest_200(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports" / "intelligence"
            reports.mkdir(parents=True)
            for index in range(205):
                (reports / f"semantic_status_{index:020d}.json").write_text(
                    "{}\n",
                    encoding="utf-8",
                )
            report = {
                "metadata": {},
                "artifacts": {},
                "semantic": {},
                "quality": {},
                "versions": {},
                "capacity": {},
            }

            write_semantic_status_report(
                tmp,
                report,
                now=lambda: datetime(
                    2026,
                    7,
                    29,
                    5,
                    0,
                    tzinfo=timezone.utc,
                ),
            )

            snapshots = sorted(
                reports.glob("semantic_status_[0-9]*.json")
            )
            self.assertEqual(len(snapshots), 200)
            self.assertFalse(
                (reports / "semantic_status_00000000000000000000.json").exists()
            )
            self.assertTrue(
                (reports / "semantic_status_latest.json").exists()
            )

    def test_quality_report_uses_bounded_sql_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(
                root / "data/shared/intelligence"
            )
            for index, delay_minutes in enumerate((1, 3), 1):
                store.insert_document(
                    SourceDocument(
                        source="tushare_announcement",
                        source_id=f"notice-{index}",
                        title=f"公告 {index}",
                        published_at="2026-07-18T00:00:00Z",
                        first_seen_at=(
                            "2026-07-18T00:01:00Z"
                            if delay_minutes == 1
                            else "2026-07-18T00:03:00Z"
                        ),
                        effective_at="2026-07-18T00:00:00Z",
                        source_url=(
                            "https://static.cninfo.com.cn/"
                            f"notice-{index}.pdf"
                        ),
                        content=b"metadata",
                        metadata={"content_scope": "title_metadata"},
                    )
                )

            with patch.object(
                IntelligenceStore,
                "documents",
                side_effect=AssertionError(
                    "quality report loaded every document"
                ),
            ):
                report = build_quality_report(root)

        source = report["sources"][0]
        self.assertEqual(report["documents"], 2)
        self.assertEqual(
            report["point_in_time_quality"]
            ["documents_with_first_seen"],
            2,
        )
        self.assertEqual(source["documents"], 2)
        self.assertAlmostEqual(
            source["median_ingestion_delay_minutes"],
            2.0,
            places=4,
        )
        self.assertAlmostEqual(
            source["p95_ingestion_delay_minutes"],
            3.0,
            places=4,
        )


if __name__ == "__main__":
    unittest.main()
