from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from stock_analyze.intelligence.blob_store import (
    OssBlobStore,
    build_blob_store,
)
from stock_analyze.intelligence.entities import EntityResolver
from stock_analyze.intelligence.extraction import RuleEventExtractor
from stock_analyze.intelligence.sources.official import _is_b_share_code


ROOT = Path(__file__).resolve().parents[1]
NEW_EVENT_FACTORS = {
    "event_relevance_20d",
    "event_materiality_positive_20d",
    "event_materiality_negative_20d",
    "event_certainty_20d",
    "event_revision_risk_20d",
    "earnings_event_score_20d",
    "buyback_event_score_20d",
    "shareholder_flow_event_score_20d",
    "contract_event_score_60d",
    "corporate_action_event_score_60d",
    "legal_risk_event_score_60d",
    "delisting_risk_event_score_60d",
    "capital_structure_event_score_60d",
}
SENSITIVE_RUNTIME_VALUES = (
    "TUSHARE_TOKEN",
    "INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE",
    "INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE",
    "INTELLIGENCE_LLM_API_KEY_FILE",
    "INTELLIGENCE_LLM_MODEL_CANDIDATE_A",
    "INTELLIGENCE_LLM_MODEL_CANDIDATE_B",
)


class SystemStructureTests(unittest.TestCase):
    def test_only_current_market_implementations_are_active(self) -> None:
        markets = ROOT / "stock_analyze" / "markets"
        self.assertTrue((markets / "a_share").is_dir())
        self.assertTrue((markets / "cn_qdii_etf").is_dir())
        self.assertFalse((markets / "hk").exists())
        self.assertFalse((markets / "us").exists())
        self.assertFalse((markets / "_yfinance_base.py").exists())
        self.assertTrue((markets / "_pricing.py").is_file())

        archive = ROOT / "archive" / "direct-overseas"
        self.assertTrue((archive / "source" / "stock_analyze" / "markets" / "hk").is_dir())
        self.assertTrue((archive / "source" / "stock_analyze" / "markets" / "us").is_dir())
        self.assertTrue((archive / "source" / "configs" / "competition_hk.yaml").is_file())
        self.assertTrue((archive / "source" / "configs" / "competition_us.yaml").is_file())

    def test_retired_operator_scripts_are_absent(self) -> None:
        for name in (
            "notify-overseas.sh",
            "overseas_summary.py",
            "notify-daily-summary.sh",
            "verify_data_sources.py",
        ):
            with self.subTest(name=name):
                self.assertFalse((ROOT / "scripts" / name).exists())

        self.assertFalse((ROOT / "scripts" / "run-overseas.sh").exists())
        self.assertTrue((ROOT / "archive" / "direct-overseas" / "run-overseas.sh").is_file())
        self.assertTrue(
            (ROOT / "archive" / "direct-overseas" / "source" / "scripts" / "verify_data_sources.py").is_file()
        )

    def test_retired_and_pipeline_triggered_timer_files_are_absent(self) -> None:
        unit_dir = ROOT / "deploy" / "systemd"
        retired = (
            "stock-analyze-daily.service",
            "stock-analyze-daily.timer",
            "stock-analyze-weekly.service",
            "stock-analyze-weekly.timer",
            "stock-analyze-claude-cn-qdii-etf-daily.timer",
            "stock-analyze-codex-cn-qdii-etf-daily.timer",
        )
        for name in retired:
            with self.subTest(name=name):
                self.assertFalse((unit_dir / name).exists())

    def test_harness_and_overview_are_canonical(self) -> None:
        overview = (ROOT / "docs" / "system-overview.md").read_text(encoding="utf-8")
        harness = (ROOT / "docs" / "system-harness.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        for text in (overview, harness, agents):
            self.assertIn("cn_qdii_etf", text)
            self.assertIn("稳健防守", text)
            self.assertIn("趋势进攻", text)
        self.assertIn("scripts/system-audit.sh", harness)
        self.assertIn("docs/system-harness.md", agents)
        self.assertIn("/opt/stock-analyze/data/notifications/", agents)
        self.assertIn("evidence-first-research-stop-decision.md", agents)
        for asset in (
            "Earnings forecast/express",
            "Repurchase + holder trade",
            "Shareholder counts",
            "Restricted-share unlocks",
            "Implemented annual dividends",
            "Block trades",
        ):
            with self.subTest(asset=asset):
                self.assertIn(asset, agents)

    def test_system_audit_covers_local_and_remote_contracts(self) -> None:
        audit = (ROOT / "scripts" / "system-audit.sh").read_text(encoding="utf-8")

        self.assertIn("tests.test_system_structure", audit)
        self.assertIn("tests.test_dashboard_http", audit)
        self.assertIn("check-ecs-timers.sh", audit)
        self.assertIn("/api/dashboard/summary.json", audit)
        self.assertIn("/api/dashboard/operations.json?market=a_share&agent=claude", audit)
        self.assertIn("--remote", audit)
        self.assertIn("systemctl list-units --failed", audit)
        self.assertIn("SA_PYTHON_BIN", audit)
        self.assertIn("/opt/stock-analyze/venv/bin/python", audit)

    def test_ecs_lark_document_publisher_is_part_of_harness(self) -> None:
        publisher = ROOT / "scripts" / "publish_system_doc_to_lark.py"
        harness = (ROOT / "docs" / "system-harness.md").read_text(encoding="utf-8")

        self.assertTrue(publisher.is_file())
        self.assertIn("publish_system_doc_to_lark.py", harness)

    def test_active_runtime_has_no_yfinance_dependency(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("yfinance", requirements.lower())

    def test_runtime_cleanup_is_allowlisted_and_preserves_active_data(self) -> None:
        cleanup = (ROOT / "scripts" / "cleanup-retired-runtime.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--apply", cleanup)
        self.assertIn("data/hk", cleanup)
        self.assertIn("data/us", cleanup)
        self.assertIn("data/research/.a_share-feature-batches-*", cleanup)
        self.assertIn("archive/runtime-data/legacy-agent", cleanup)
        self.assertNotIn('"$LEGACY_ROOT/data"', cleanup)
        self.assertIn(
            'PROTECTED_PATHS+=("$LEGACY_ROOT/data/notifications")', cleanup
        )
        self.assertIn('"$LEGACY_ROOT/data/intelligence.db"', cleanup)
        for protected in (
            "data/a_share",
            "data/cn_qdii_etf",
            "data/shared/cache",
            "data/shared/backtest_cache",
            "data/research/models",
            "data/model_iterations",
        ):
            with self.subTest(protected=protected):
                self.assertIn(f'PROTECTED_PATHS+=("$APP_DIR/{protected}")', cleanup)

    def test_production_announcement_artifacts_resolve_to_oss(self) -> None:
        config = yaml.safe_load(
            (ROOT / "configs" / "intelligence_semantic.yaml").read_text(
                encoding="utf-8"
            )
        )
        artifact = config["artifact_store"]
        self.assertEqual(artifact["production_kind"], "oss")
        self.assertNotEqual(artifact["production_kind"], "local")

        with tempfile.TemporaryDirectory() as tmp:
            key_id = Path(tmp) / "oss-key-id"
            key_secret = Path(tmp) / "oss-key-secret"
            key_id.write_text("test-key-id", encoding="utf-8")
            key_secret.write_text("test-key-secret", encoding="utf-8")
            environment = {
                artifact["credential_env"]["access_key_id_file"]: str(key_id),
                artifact["credential_env"]["access_key_secret_file"]: str(
                    key_secret
                ),
            }
            with patch.dict(os.environ, environment, clear=False):
                store = build_blob_store(
                    config,
                    production=True,
                    oss_bucket_factory=lambda *_args: object(),
                )
        self.assertIsInstance(store, OssBlobStore)

    def test_pdf_pipeline_cannot_write_below_intelligence_raw(self) -> None:
        forbidden = "data/shared/intelligence/raw"
        raw_root = ROOT / forbidden
        if raw_root.exists():
            for path in raw_root.rglob("*"):
                if not path.is_file():
                    continue
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertNotEqual(path.suffix.casefold(), ".pdf")
                    self.assertFalse(path.read_bytes().startswith(b"%PDF-"))
        for relative in (
            "stock_analyze/intelligence/blob_store.py",
            "stock_analyze/intelligence/pdf_fetcher.py",
            "stock_analyze/intelligence/document_parser.py",
            "scripts/install-intelligence-runtime.sh",
            "scripts/deploy-app-to-ecs.sh",
        ):
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn(forbidden, text)
                self.assertNotIn(".raw_root", text)

    def test_committed_files_do_not_contain_runtime_secret_values(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout.split(b"\0")
        secret_values: dict[str, bytes] = {}
        for name in SENSITIVE_RUNTIME_VALUES:
            configured = os.environ.get(name, "").strip()
            if not configured:
                continue
            if name.endswith("_FILE"):
                try:
                    configured = Path(configured).read_text(
                        encoding="utf-8"
                    ).strip()
                except OSError:
                    continue
            if len(configured) >= 8:
                secret_values[name] = configured.encode("utf-8")

        for raw_path in tracked:
            if not raw_path:
                continue
            path = ROOT / os.fsdecode(raw_path)
            if not path.is_file():
                continue
            payload = path.read_bytes()
            for name, value in secret_values.items():
                with self.subTest(path=str(path.relative_to(ROOT)), secret=name):
                    self.assertNotIn(value, payload)

    def test_b_shares_are_rejected_at_adapter_and_event_boundaries(self) -> None:
        for code in ("200001.SZ", "200512.SZ", "900901.SH", "900957.SH"):
            with self.subTest(code=code):
                self.assertTrue(_is_b_share_code(code))
                row = {
                    "source": "tushare_announcement",
                    "metadata_json": json.dumps({"ts_code": code}),
                    "mime_type": "text/plain",
                    "title": "股份回购公告",
                    "published_at": "2026-07-24T01:00:00+00:00",
                    "effective_at": "2026-07-24T01:00:00+00:00",
                }
                self.assertEqual(
                    RuleEventExtractor(EntityResolver({})).extract(
                        1, row, "公司拟实施股份回购。".encode("utf-8")
                    ),
                    (),
                )

        for code in ("000001.SZ", "600000.SH", "159920.SZ", "513500.SH"):
            with self.subTest(code=code):
                self.assertFalse(_is_b_share_code(code))

    def test_new_announcement_factors_remain_observing(self) -> None:
        payload = json.loads(
            (ROOT / "configs" / "intelligence_factors.json").read_text(
                encoding="utf-8"
            )
        )
        factors = payload["factors"]
        self.assertTrue(NEW_EVENT_FACTORS.issubset(factors))
        self.assertEqual(
            {name: factors[name]["state"] for name in NEW_EVENT_FACTORS},
            {name: "observing" for name in NEW_EVENT_FACTORS},
        )

    def test_announcement_backfill_is_manual_and_has_no_timer(self) -> None:
        unit_dir = ROOT / "deploy" / "systemd"
        service = (
            unit_dir / "stock-analyze-intelligence-backfill.service"
        ).read_text(encoding="utf-8")
        self.assertFalse(
            (unit_dir / "stock-analyze-intelligence-backfill.timer").exists()
        )
        self.assertNotIn("[Install]", service)
        self.assertNotIn("WantedBy=timers.target", service)
        self.assertIn("--resume", service)

    def test_announcement_runbook_covers_the_operating_contract(self) -> None:
        runbook_path = ROOT / "docs" / "announcement-intelligence-runbook.md"
        self.assertTrue(runbook_path.is_file())
        runbook = runbook_path.read_text(encoding="utf-8")
        for marker in (
            "configured",
            "intelligence-backfill",
            "--resume",
            "intelligence-status",
            "intelligence-enrich",
            "intelligence-semantic-daily",
            "intelligence-semantic-executor.yaml",
            "DeepSeek",
            "intelligence-reconcile",
            "quarantined",
            "intelligence-evaluate",
            "PRAGMA integrity_check",
            "df -h",
            "ossutil",
            "observed",
            "research",
            "historical_cutoff",
            "未自动入模",
            "语料",
            "因子",
            "模型影响",
            "飞书",
            "真实回报",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, runbook)
        self.assertNotIn("intelligence-semantic-promote", runbook)
        self.assertNotIn("Champion", runbook)

        for doc_name in (
            "system-overview.md",
            "competition-runbook.md",
        ):
            with self.subTest(doc=doc_name):
                document = (ROOT / "docs" / doc_name).read_text(
                    encoding="utf-8"
                )
                self.assertIn("announcement-intelligence-runbook.md", document)


if __name__ == "__main__":
    unittest.main()
