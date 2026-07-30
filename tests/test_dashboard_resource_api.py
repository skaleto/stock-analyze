from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from stock_analyze.dashboard_aggregator import (
    _json_safe,
    build_dashboard_summary_data,
)
from stock_analyze.dashboard_api import (
    build_dashboard_governance_data,
    build_dashboard_intelligence_data,
    build_dashboard_intelligence_document_data,
    build_dashboard_intelligence_event_data,
    build_dashboard_operations_data,
    build_dashboard_overview_data,
    build_dashboard_performance_data,
    build_dashboard_portfolio_data,
    build_dashboard_predictions_data,
    build_dashboard_research_data,
    build_dashboard_system_overview_data,
)
from stock_analyze.dashboard_http import DashboardResourceNotFound
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.research.lineage import ResearchLineageStore


def _seed_repo(root: Path) -> None:
    config_dir = root / "configs" / "agents"
    config_dir.mkdir(parents=True)
    (config_dir / "codex_cn_qdii_etf.yaml").write_text(
        json.dumps(
            {
                "agent_id": "codex",
                "strategy_id": "codex_qdii_v1",
                "name": "趋势进攻",
                "factors": {"momentum_20": {"weight": 1.0, "direction": "high"}},
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "codex_a_share.yaml").write_text(
        json.dumps(
            {
                "agent_id": "codex",
                "strategy_id": "codex_a_share_v1",
                "name": "趋势进攻",
                "factors": {"momentum_20": {"weight": 1.0, "direction": "high"}},
            }
        ),
        encoding="utf-8",
    )
    (root / "data" / "a_share" / "codex").mkdir(parents=True)
    (root / "reports" / "a_share" / "codex").mkdir(parents=True)
    data_dir = root / "data" / "cn_qdii_etf" / "codex"
    reports_dir = root / "reports" / "cn_qdii_etf" / "codex"
    cache_dir = root / "data" / "cn_qdii_etf" / "shared" / "cache"
    data_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    pd.DataFrame([{"ts_code": "513100.SH", "name": "纳指ETF"}]).to_csv(
        cache_dir / "fund_basic_E.csv", index=False
    )
    pd.DataFrame(
        [
            {
                "date": "2026-07-09",
                "account_id": "us_exposure",
                "cash": 1_000_000,
                "market_value": 0,
                "total_value": 1_000_000,
                "benchmark_code": "513100.SH",
                "benchmark_close": 1.0,
            },
            {
                "date": "2026-07-10",
                "account_id": "us_exposure",
                "cash": 900_000,
                "market_value": 110_000,
                "total_value": 1_010_000,
                "benchmark_code": "513100.SH",
                "benchmark_close": 1.1,
            },
        ]
    ).to_csv(data_dir / "daily_nav.csv", index=False)
    pd.DataFrame(
        [
            {
                "run_id": "daily-1",
                "command": "run-daily",
                "started_at": "2026-07-10T18:30:00",
                "finished_at": "2026-07-10T18:30:02",
                "status": "success",
                "duration_ms": 2000,
            }
        ]
    ).to_csv(data_dir / "runs.csv", index=False)
    (data_dir / "pending_orders.json").write_text(
        json.dumps(
            [
                {
                    "account_id": "us_exposure",
                    "code": "513100.SH",
                    "side": "buy",
                    "delta_shares": 100,
                    "target_value": 110,
                    "trade_date": "2026-07-13",
                    "run_id": "run-internal",
                    "strategy_id": "strategy-internal",
                    "warnings": [f"internal-warning-{index}" for index in range(100)],
                }
            ]
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "account_id": "us_exposure",
                "code": "513100.SH",
                "shares": 100,
                "market_value": 110,
            }
        ]
    ).to_csv(data_dir / "positions.csv", index=False)
    pd.DataFrame(
        [
            {
                "trade_date": "2026-07-10",
                "account_id": "us_exposure",
                "code": "513100.SH",
                "side": "buy",
                "shares": 100,
                "price": 1.1,
                "gross_amount": 110,
            }
        ]
    ).to_csv(data_dir / "trades.csv", index=False)
    (reports_dir / "weekly_report.md").write_text("# 周报\n\n测试内容", encoding="utf-8")

    prediction_dir = data_dir / "predictions"
    prediction_dir.mkdir()
    rows = []
    for horizon in (3, 5):
        for index in range(7):
            rows.append(
                {
                    "as_of": "2026-07-10",
                    "code": str(513100 + index),
                    "horizon": horizon,
                    "p_up": 0.50 + index / 100,
                    "p_flat": 0.30,
                    "p_down": 0.20 - index / 100,
                    "confidence": 0.60 + index / 100,
                }
            )
    pd.DataFrame(rows).to_parquet(prediction_dir / "20260710.parquet", index=False)


def _seed_intelligence(root: Path) -> None:
    store = IntelligenceStore(root / "data" / "shared" / "intelligence")
    published_at = "2026-07-20T08:00:00+00:00"
    seen_at = "2026-07-20T08:05:00+00:00"
    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO ingestion_runs(
                run_id, source, started_at, finished_at, status,
                cursor_in, cursor_out, fetched, inserted, error
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "ingest-1",
                "tushare_anns_d",
                published_at,
                seen_at,
                "succeeded",
                "20260719",
                "20260720",
                34,
                34,
                "",
            ),
        )
        connection.execute(
            """
            INSERT INTO source_cursors(source, cursor, updated_at)
            VALUES(?,?,?)
            """,
            ("tushare_anns_d", "20260720", seen_at),
        )
        document_ids: list[int] = []
        for index in range(34):
            cursor = connection.execute(
                """
                INSERT INTO documents(
                    source, source_id, title, published_at, first_seen_at,
                    effective_at, source_url, mime_type, content_hash,
                    raw_path, metadata_json, status, queue_priority,
                    live_observed
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "tushare_anns_d",
                    f"ann-{index}",
                    f"测试公告 {index}",
                    published_at,
                    seen_at,
                    published_at,
                    f"https://example.com/ann-{index}.pdf",
                    "application/pdf",
                    f"{index:064x}",
                    f"raw/{index}",
                    json.dumps(
                        {
                            "market": "a_share",
                            "no_event_reason": (
                                "公告仅包含例行披露，未识别到可量化重大事件"
                                if index == 32
                                else None
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    "processed",
                    100,
                    1,
                ),
            )
            document_id = int(cursor.lastrowid)
            document_ids.append(document_id)
            connection.execute(
                """
                INSERT INTO document_security_links(
                    document_id, ts_code, name, provenance,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    document_id,
                    f"{600000 + index:06d}.SH",
                    f"测试公司 {index}",
                    "source_metadata",
                    seen_at,
                    seen_at,
                ),
            )

        for index, document_id in enumerate(document_ids[:31]):
            run_id = f"run-{index}"
            event_id = f"event-{index}"
            candidate_id = f"candidate-{index}"
            connection.execute(
                """
                INSERT INTO semantic_runs(
                    run_id, document_id, artifact_hash, provider, model,
                    prompt_version, schema_version, taxonomy_version,
                    parser_version, input_hash, output_hash, output_uri,
                    status, input_tokens, output_tokens, latency_ms,
                    cost_microunits, error, started_at, finished_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    document_id,
                    f"{index + 100:064x}",
                    "deepseek",
                    "deepseek-v4-pro",
                    "announcement-events-v1",
                    "announcement-semantic-v1",
                    "announcement-events-v1",
                    "announcement-layout-v1",
                    f"{index + 200:064x}",
                    f"{index + 300:064x}",
                    f"oss://stock-analyze/semantic/{run_id}.json",
                    "succeeded",
                    100,
                    30,
                    500,
                    1000,
                    "",
                    seen_at,
                    seen_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO events(
                    event_id, document_id, event_type, direction, strength,
                    confidence, novelty, horizon_days, published_at,
                    effective_at, evidence, extraction_method, metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    document_id,
                    "buyback",
                    0.7,
                    0.8,
                    0.86,
                    0.72,
                    20,
                    published_at,
                    published_at,
                    "董事会审议通过股份回购方案",
                    "llm-semantic-v1",
                    json.dumps({"market": "a_share"}),
                ),
            )
            connection.execute(
                """
                INSERT INTO event_candidates(
                    candidate_id, run_id, document_id, event_index,
                    event_type, lifecycle, payload_json, validation_status,
                    validation_errors_json, canonical_event_id, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    run_id,
                    document_id,
                    0,
                    "buyback",
                    "announced",
                    json.dumps(
                        {
                            "direction": 0.7,
                            "confidence": 0.86,
                            "materiality": 0.75,
                            "relevance": 0.9,
                            "novelty": 0.72,
                            "event": {
                                "subjects": [
                                    {
                                        "entity_id": "external:战略投资方",
                                        "role": "counterparty",
                                    }
                                ]
                            },
                        }
                    ),
                    "canonical",
                    "[]",
                    event_id,
                    seen_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO event_scores(
                    event_id, relevance, novelty, materiality, certainty,
                    source_credibility, direction, confidence,
                    scoring_version, inputs_json, scored_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    0.9,
                    0.72,
                    0.75,
                    0.88,
                    0.95,
                    0.7,
                    0.86,
                    "event-score-v1",
                    "{}",
                    seen_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO event_entities(
                    event_id, entity_type, entity_id, entity_name,
                    industry, confidence
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    event_id,
                    "security",
                    f"{600000 + index:06d}.SH",
                    f"测试公司 {index}",
                    "工业",
                    0.98,
                ),
            )

        quarantined_document = document_ids[31]
        connection.execute(
            """
            INSERT INTO semantic_runs(
                run_id, document_id, artifact_hash, provider, model,
                prompt_version, schema_version, taxonomy_version,
                parser_version, input_hash, output_hash, output_uri,
                status, error, started_at, finished_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run-quarantined",
                quarantined_document,
                f"{901:064x}",
                "deepseek",
                "deepseek-v4-pro",
                "announcement-events-v1",
                "announcement-semantic-v1",
                "announcement-events-v1",
                "announcement-layout-v1",
                f"{902:064x}",
                f"{903:064x}",
                "oss://stock-analyze/semantic/run-quarantined.json",
                "succeeded",
                "",
                seen_at,
                seen_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO event_candidates(
                candidate_id, run_id, document_id, event_index,
                event_type, lifecycle, payload_json, validation_status,
                validation_errors_json, canonical_event_id, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "candidate-quarantined",
                "run-quarantined",
                quarantined_document,
                0,
                "major_contract",
                "announced",
                json.dumps(
                    {
                        "direction": 0.4,
                        "confidence": 0.52,
                        "materiality": 0.6,
                        "relevance": 0.8,
                        "novelty": 0.9,
                    }
                ),
                "quarantined",
                json.dumps(["evidence_quote_mismatch"]),
                None,
                seen_at,
            ),
        )

        for run_id, document_id, status, error in (
            ("run-no-event", document_ids[32], "no_event", ""),
            ("run-failed", document_ids[33], "failed_terminal", "provider_schema_invalid"),
        ):
            connection.execute(
                """
                INSERT INTO semantic_runs(
                    run_id, document_id, artifact_hash, provider, model,
                    prompt_version, schema_version, taxonomy_version,
                    parser_version, input_hash, output_hash, output_uri,
                    status, error, started_at, finished_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    document_id,
                    f"{document_id + 1000:064x}",
                    "deepseek",
                    "deepseek-v4-pro",
                    "announcement-events-v1",
                    "announcement-semantic-v1",
                    "announcement-events-v1",
                    "announcement-layout-v1",
                    f"{document_id + 2000:064x}",
                    f"{document_id + 3000:064x}" if status == "no_event" else None,
                    (
                        f"oss://stock-analyze/semantic/{run_id}.json"
                        if status == "no_event"
                        else None
                    ),
                    status,
                    error,
                    seen_at,
                    seen_at,
                ),
            )

        first_document = document_ids[0]
        connection.execute(
            """
            INSERT INTO document_artifacts(
                artifact_id, document_id, artifact_type, content_hash,
                storage_uri, mime_type, byte_size, parser_version,
                status, error, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "parsed-0",
                first_document,
                "parsed",
                f"{700:064x}",
                "oss://stock-analyze/announcements/parsed/0.json.gz",
                "application/json",
                2048,
                "announcement-layout-v1",
                "parsed",
                "",
                seen_at,
                seen_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO document_chunks(
                chunk_id, document_id, artifact_id, sequence_no,
                page_number, section, bbox_json, text, text_hash,
                ocr_used, ocr_confidence, parser_version
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "chunk-0",
                first_document,
                "parsed-0",
                0,
                1,
                "正文",
                "[0,0,100,100]",
                "董事会审议通过股份回购方案，回购金额不低于人民币一亿元。",
                f"{701:064x}",
                0,
                None,
                "announcement-layout-v1",
            ),
        )
        for index in range(105):
            connection.execute(
                """
                INSERT INTO event_evidence(
                    candidate_id, document_id, evidence_id, chunk_id,
                    page_number, start_char, end_char, quote,
                    normalized_quote_hash
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    "candidate-0",
                    first_document,
                    f"evidence-{index:03d}",
                    "chunk-0",
                    1,
                    0,
                    12,
                    f"第 {index + 1} 条证据：董事会审议通过股份回购方案",
                    f"{800 + index:064x}",
                ),
            )
        for index in range(55):
            connection.execute(
                """
                INSERT INTO event_facts(
                    event_id, fact_name, ordinal, raw_value, numeric_value,
                    text_value, unit, currency, period, evidence_ids_json,
                    provenance
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "event-0",
                    f"fact-{index:03d}",
                    0,
                    str(index),
                    str(index),
                    None,
                    "元",
                    "CNY",
                    "2026",
                    json.dumps(["evidence-000"]),
                    "semantic",
                ),
            )


class DashboardResourceApiTests(unittest.TestCase):
    def test_json_boundary_converts_numpy_arrays(self) -> None:
        payload = _json_safe(
            {
                "calibration": np.array([0.1, np.nan, 0.3]),
                "labels": {"risk", "opportunity"},
            }
        )

        encoded = json.dumps(payload, allow_nan=False)

        self.assertEqual(payload["calibration"], [0.1, None, 0.3])
        self.assertEqual(
            payload["labels"],
            ["opportunity", "risk"],
        )
        self.assertIn('"calibration"', encoded)

    def test_summary_does_not_build_legacy_full_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            with mock.patch(
                "stock_analyze.dashboard_aggregator.build_dashboard_detail_data",
                side_effect=AssertionError("legacy detail must not be used"),
            ):
                payload = build_dashboard_summary_data(
                    repo_root=root,
                    markets=["cn_qdii_etf"],
                    agents=["codex"],
                )

        self.assertEqual(payload["markets"][0]["agents"][0]["agent"], "codex")

    def test_resources_have_single_domain_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            kwargs = {"repo_root": root, "market": "cn_qdii_etf", "agent": "codex"}
            overview = build_dashboard_overview_data(**kwargs)
            performance = build_dashboard_performance_data(**kwargs)
            portfolio = build_dashboard_portfolio_data(**kwargs)
            predictions = build_dashboard_predictions_data(**kwargs, limit_per_horizon=3)
            research = build_dashboard_research_data(**kwargs)
            operations = build_dashboard_operations_data(**kwargs)
            governance = build_dashboard_governance_data(**kwargs)

        self.assertEqual(
            set(overview),
            {"generated_at", "market", "market_label", "currency", "agent", "strategy", "latest_nav"},
        )
        self.assertEqual(set(performance), {"generated_at", "market", "agent", "nav"})
        self.assertEqual(
            set(portfolio),
            {"generated_at", "market", "agent", "activity", "orders", "positions", "trades"},
        )
        self.assertEqual(
            set(predictions),
            {
                "generated_at",
                "market",
                "agent",
                "prediction_summary",
                "alerts",
                "regimes",
                "model_health",
                "source_health",
            },
        )
        self.assertEqual(
            set(research),
            {"generated_at", "market", "agent", "selection", "lookthrough", "research"},
        )
        self.assertEqual(
            set(operations),
            {"generated_at", "market", "agent", "runs", "weekly_report"},
        )
        self.assertEqual(
            set(governance),
            {
                "generated_at",
                "market",
                "agent",
                "action_state",
                "lineage",
                "risk",
                "attribution",
                "drift",
                "experiments",
                "intelligence_evidence",
                "distinctness",
            },
        )

    def test_system_overview_connects_intelligence_models_and_formal_strategies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            lineage = ResearchLineageStore(
                root / "data" / "shared" / "research_lineage.sqlite3"
            )
            lineage.append_decision_runs(
                [
                    {
                        "decision_run_id": "daily-old:us_exposure",
                        "source_run_id": "daily-old",
                        "agent_id": "codex",
                        "market": "cn_qdii_etf",
                        "strategy_id": "trend-v1",
                        "account_id": "us_exposure",
                        "as_of": "2026-07-09",
                        "model_policy_status": "rule_only",
                        "model_applied_candidates": 0,
                        "model_candidate_coverage": 0.0,
                        "model_fallback_reason": "prediction_artifact_missing",
                    },
                    {
                        "decision_run_id": "daily-new:us_exposure",
                        "source_run_id": "daily-new",
                        "agent_id": "codex",
                        "market": "cn_qdii_etf",
                        "strategy_id": "trend-v1",
                        "account_id": "us_exposure",
                        "as_of": "2026-07-10",
                        "model_policy_status": "active",
                        "model_versions": {"5": "model-v3"},
                        "model_applied_candidates": 4,
                        "model_candidate_coverage": 0.4,
                        "model_fallback_reason": "",
                    },
                ]
            )

            with mock.patch(
                "stock_analyze.dashboard_api.agg._read_model_iteration_status",
                return_value={
                    "status": "unavailable",
                    "candidate": None,
                    "champion": None,
                },
            ):
                payload = build_dashboard_system_overview_data(
                    repo_root=root,
                )

        self.assertEqual(
            set(payload),
            {
                "generated_at",
                "markets",
                "models",
                "strategy_model_usage",
                "intelligence",
                "errors",
            },
        )
        self.assertNotIn(
            "strategy_model_usage_read_unavailable",
            {item["code"] for item in payload["errors"]},
        )
        self.assertEqual(
            {item["market"] for item in payload["markets"]},
            {"a_share", "cn_qdii_etf"},
        )
        usage = next(
            item
            for item in payload["strategy_model_usage"]
            if item["market"] == "cn_qdii_etf"
            and item["agent"] == "codex"
        )
        self.assertEqual(usage["as_of"], "2026-07-10")
        self.assertEqual(usage["status"], "active")
        self.assertEqual(usage["applied_candidates"], 4)
        self.assertEqual(usage["model_versions"], {"5": "model-v3"})
        self.assertIn("pipeline", payload["intelligence"])
        self.assertEqual(
            {item["market"] for item in payload["models"]},
            {"a_share", "cn_qdii_etf"},
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertLess(len(serialized.encode("utf-8")), 250_000)
        self.assertNotIn("rowsByDecision", serialized)
        self.assertNotIn("raw_content", serialized)

    def test_system_overview_redacts_intelligence_ingestion_errors(self) -> None:
        sensitive_path = "/opt/stock-analyze/secrets/intelligence.env"
        sensitive_key = "DEEPSEEK_API_KEY=plainsecretvalue123456"
        sensitive_endpoint = "https://user:password@api.internal.example/v1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_intelligence(root)
            database = (
                root
                / "data"
                / "shared"
                / "intelligence"
                / "intelligence.sqlite3"
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    UPDATE ingestion_runs
                    SET status='failed', error=?
                    WHERE run_id='ingest-1'
                    """,
                    (
                        f"{sensitive_path}: {sensitive_key}; "
                        f"endpoint={sensitive_endpoint}",
                    ),
                )
                connection.commit()

            with mock.patch(
                "stock_analyze.dashboard_api.agg._read_model_iteration_status",
                return_value={
                    "status": "unavailable",
                    "candidate": None,
                    "champion": None,
                },
            ):
                payload = build_dashboard_system_overview_data(repo_root=root)

        source = payload["intelligence"]["pipeline"]["sources"][0]
        self.assertEqual(source["error"], "情报采集状态读取失败")
        serialized = json.dumps(payload, ensure_ascii=False)
        for secret in (
            sensitive_path,
            sensitive_key,
            sensitive_endpoint,
            "plainsecretvalue123456",
        ):
            self.assertNotIn(secret, serialized)

    def test_system_overview_keeps_other_sections_when_model_usage_read_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            with (
                mock.patch(
                    "stock_analyze.dashboard_api._latest_strategy_model_usage",
                    side_effect=OSError(
                        "/srv/stock-analyze/private/key.json: secret-token"
                    ),
                ),
                mock.patch(
                    "stock_analyze.dashboard_api.agg._read_model_iteration_status",
                    return_value={
                        "status": "unavailable",
                        "candidate": None,
                        "champion": None,
                    },
                ),
            ):
                payload = build_dashboard_system_overview_data(
                    repo_root=root,
                )

        self.assertEqual(
            {item["market"] for item in payload["markets"]},
            {"a_share", "cn_qdii_etf"},
        )
        self.assertEqual(
            {item["market"] for item in payload["models"]},
            {"a_share", "cn_qdii_etf"},
        )
        self.assertEqual(payload["strategy_model_usage"], [])
        self.assertEqual(
            payload["errors"],
            [
                {
                    "code": "strategy_model_usage_read_unavailable",
                    "section": "strategy_model_usage",
                    "message": "策略模型采用记录暂不可用。",
                }
            ],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("/srv/stock-analyze", serialized)
        self.assertNotIn("secret-token", serialized)

    def test_system_overview_isolates_sqlite_summary_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            with (
                mock.patch(
                    "stock_analyze.dashboard_api.agg.build_dashboard_summary_data",
                    side_effect=sqlite3.OperationalError(
                        "/srv/private/summary.sqlite3: secret-token"
                    ),
                ),
                mock.patch(
                    "stock_analyze.dashboard_api.agg._read_model_iteration_status",
                    return_value={
                        "status": "unavailable",
                        "candidate": None,
                        "champion": None,
                    },
                ),
                mock.patch(
                    "stock_analyze.dashboard_api._latest_strategy_model_usage",
                    return_value=[],
                ),
            ):
                payload = build_dashboard_system_overview_data(repo_root=root)

        self.assertEqual(payload["markets"], [])
        self.assertEqual(len(payload["models"]), 2)
        self.assertIn("pipeline", payload["intelligence"])
        self.assertEqual(
            payload["errors"],
            [
                {
                    "code": "market_summary_read_unavailable",
                    "section": "markets",
                    "message": "市场概览暂不可用。",
                }
            ],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("/srv/private", serialized)
        self.assertNotIn("secret-token", serialized)

    def test_system_overview_isolates_sqlite_intelligence_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            with (
                mock.patch(
                    "stock_analyze.dashboard_api.build_dashboard_intelligence_data",
                    side_effect=sqlite3.OperationalError(
                        "/srv/private/intelligence.sqlite3: secret-token"
                    ),
                ),
                mock.patch(
                    "stock_analyze.dashboard_api.agg._read_model_iteration_status",
                    return_value={
                        "status": "unavailable",
                        "candidate": None,
                        "champion": None,
                    },
                ),
                mock.patch(
                    "stock_analyze.dashboard_api._latest_strategy_model_usage",
                    return_value=[],
                ),
            ):
                payload = build_dashboard_system_overview_data(repo_root=root)

        self.assertEqual(
            {item["market"] for item in payload["markets"]},
            {"a_share", "cn_qdii_etf"},
        )
        self.assertEqual(len(payload["models"]), 2)
        self.assertEqual(payload["intelligence"]["pipeline"]["status"], "unavailable")
        self.assertEqual(
            payload["errors"],
            [
                {
                    "code": "intelligence_read_unavailable",
                    "section": "intelligence",
                    "message": "情报链路暂不可用。",
                }
            ],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("/srv/private", serialized)
        self.assertNotIn("secret-token", serialized)

    def test_system_overview_isolates_one_model_lineage_read_failure(
        self,
    ) -> None:
        def model_status(_root: Path, market: str) -> dict[str, object]:
            if market == "a_share":
                raise OSError("/private/model-registry.json is unreadable")
            return {
                "status": "complete",
                "candidate": {"display_version": "Q5-V004"},
                "champion": None,
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            with mock.patch(
                "stock_analyze.dashboard_api.agg._read_model_iteration_status",
                side_effect=model_status,
            ):
                payload = build_dashboard_system_overview_data(repo_root=root)

        models = {item["market"]: item for item in payload["models"]}
        self.assertEqual(models["a_share"]["iteration"]["status"], "unavailable")
        self.assertEqual(
            models["cn_qdii_etf"]["iteration"]["candidate"]["display_version"],
            "Q5-V004",
        )
        self.assertEqual(
            payload["errors"],
            [
                {
                    "code": "model_lineage_read_unavailable",
                    "section": "models",
                    "market": "a_share",
                    "message": "A股模型采用链暂不可用。",
                }
            ],
        )
        self.assertNotIn(
            "/private/model-registry.json",
            json.dumps(payload, ensure_ascii=False),
        )

    def test_system_overview_does_not_hide_programming_errors(self) -> None:
        targets = (
            "stock_analyze.dashboard_api.agg.build_dashboard_summary_data",
            "stock_analyze.dashboard_api.agg._read_model_iteration_status",
            "stock_analyze.dashboard_api.build_dashboard_intelligence_data",
            "stock_analyze.dashboard_api._latest_strategy_model_usage",
        )
        exception_types = (RuntimeError, TypeError, ValueError)
        safe_intelligence = {
            "pipeline": {"status": "unavailable", "sources": []},
            "extraction": {},
            "factorSupply": {},
            "modelImpact": {},
            "decisions": {},
            "rowsByDecision": {},
        }
        for target in targets:
            for exception_type in exception_types:
                with self.subTest(target=target, exception=exception_type.__name__):
                    with tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        _seed_repo(root)
                        with (
                            mock.patch(
                                "stock_analyze.dashboard_api.agg.build_dashboard_summary_data",
                                return_value={"markets": []},
                            ),
                            mock.patch(
                                "stock_analyze.dashboard_api.agg._read_model_iteration_status",
                                return_value={
                                    "status": "unavailable",
                                    "candidate": None,
                                    "champion": None,
                                },
                            ),
                            mock.patch(
                                "stock_analyze.dashboard_api.build_dashboard_intelligence_data",
                                return_value=safe_intelligence,
                            ),
                            mock.patch(
                                "stock_analyze.dashboard_api._latest_strategy_model_usage",
                                return_value=[],
                            ),
                            mock.patch(
                                target,
                                side_effect=exception_type(
                                    "unexpected programming error"
                                ),
                            ),
                        ):
                            with self.assertRaisesRegex(
                                exception_type,
                                "unexpected programming error",
                            ):
                                build_dashboard_system_overview_data(
                                    repo_root=root,
                                )

    def test_governance_resource_projects_latest_lineage_and_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            lineage = ResearchLineageStore(
                root / "data" / "shared" / "research_lineage.sqlite3"
            )
            lineage.append_decision_runs({
                "decision_run_id": "daily-1:us_exposure",
                "source_run_id": "daily-1",
                "agent_id": "codex",
                "market": "cn_qdii_etf",
                "strategy_id": "trend-v1",
                "account_id": "us_exposure",
                "as_of": "2026-07-10",
                "account_state_hash": "state-1",
                "feature_snapshot_id": "features-1",
            })
            pending_path = (
                root / "data" / "cn_qdii_etf" / "codex"
                / "pending_orders.json"
            )
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            pending[0]["optimizer_diagnostics"] = {
                "turnover": 0.2,
                "volatility": 0.15,
                "stress_losses": {"market": -0.08},
                "binding_constraints": ["max_name_weight"],
            }
            pending_path.write_text(json.dumps(pending), encoding="utf-8")

            payload = build_dashboard_governance_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        self.assertEqual(payload["lineage"]["status"], "available")
        self.assertEqual(
            payload["lineage"]["decision_runs"][0]["decision_run_id"],
            "daily-1:us_exposure",
        )
        self.assertEqual(payload["risk"]["status"], "available")
        self.assertEqual(
            payload["risk"]["portfolios"][0]["stress_losses"]["market"],
            -0.08,
        )

    def test_governance_keeps_latest_attribution_when_it_belongs_to_prior_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            lineage = ResearchLineageStore(
                root / "data" / "shared" / "research_lineage.sqlite3"
            )
            lineage.append_decision_runs([
                {
                    "decision_run_id": "daily-1:us_exposure",
                    "source_run_id": "daily-1",
                    "agent_id": "codex",
                    "market": "cn_qdii_etf",
                    "strategy_id": "trend-v1",
                    "account_id": "us_exposure",
                    "as_of": "2026-07-09",
                },
                {
                    "decision_run_id": "daily-2:us_exposure",
                    "source_run_id": "daily-2",
                    "agent_id": "codex",
                    "market": "cn_qdii_etf",
                    "strategy_id": "trend-v1",
                    "account_id": "us_exposure",
                    "as_of": "2026-07-10",
                },
            ])
            lineage.append_pnl_attributions({
                "pnl_attribution_id": "pnl-20260710-us",
                "decision_run_id": "daily-1:us_exposure",
                "security_code": "__PORTFOLIO__",
                "account_id": "us_exposure",
                "as_of": "2026-07-10",
                "net_pnl": 1000.0,
                "reconciliation_delta": 0.0,
                "status": "partial",
            })

            payload = build_dashboard_governance_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        self.assertEqual(
            payload["lineage"]["decision_runs"][0]["decision_run_id"],
            "daily-2:us_exposure",
        )
        self.assertEqual(payload["attribution"]["status"], "available")
        self.assertEqual(
            payload["attribution"]["rows"][0]["pnl_attribution_id"],
            "pnl-20260710-us",
        )

    def test_predictions_are_bounded_per_horizon_and_report_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            payload = build_dashboard_predictions_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
                limit_per_horizon=3,
            )

        summary = payload["prediction_summary"]
        self.assertEqual(summary["total"], 14)
        self.assertEqual(len(summary["rows"]), 6)
        self.assertEqual(
            {horizon: sum(row["horizon"] == horizon for row in summary["rows"]) for horizon in (3, 5)},
            {3: 3, 5: 3},
        )
        self.assertAlmostEqual(summary["rows"][0]["confidence"], 0.66)

    def test_prediction_limit_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            payload = build_dashboard_predictions_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
                limit_per_horizon=0,
            )

        counts = {
            horizon: sum(row["horizon"] == horizon for row in payload["prediction_summary"]["rows"])
            for horizon in (3, 5)
        }
        self.assertEqual(counts, {3: 1, 5: 1})

    def test_portfolio_projects_runtime_rows_to_public_dto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            payload = build_dashboard_portfolio_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        order = payload["orders"]["rows"][0]
        activity = payload["activity"]["rows"][0]
        self.assertEqual(order["shares"], 100)
        self.assertNotIn("warnings", order)
        self.assertNotIn("run_id", order)
        self.assertNotIn("strategy_id", order)
        self.assertNotIn("warnings", activity)

    def test_research_does_not_depend_on_trade_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            trades_path = root / "data" / "cn_qdii_etf" / "codex" / "trades.csv"
            trades_path.write_text("broken\nvalue\n", encoding="utf-8")

            payload = build_dashboard_research_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        self.assertEqual(payload["lookthrough"]["source"], "positions")

    def test_intelligence_summary_is_bounded_and_excludes_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_intelligence(root)
            with mock.patch(
                "pandas.read_sql_query",
                side_effect=AssertionError("summary must use bounded sqlite projections"),
            ):
                payload = build_dashboard_intelligence_data(
                    repo_root=root,
                    market="a_share",
                    agent="codex",
                )

        rows_by_decision = payload["rowsByDecision"]
        self.assertNotIn("rows", payload)
        self.assertEqual(len(rows_by_decision["canonical"]), 30)
        self.assertEqual(len(rows_by_decision["no_event"]), 1)
        self.assertEqual(len(rows_by_decision["quarantined"]), 1)
        self.assertEqual(len(rows_by_decision["failed"]), 1)
        self.assertTrue(
            all(len(rows) <= 30 for rows in rows_by_decision.values())
        )
        self.assertEqual(payload["pipeline"]["documents"], 34)
        self.assertEqual(
            payload["pipeline"]["sources"][0]["source"],
            "tushare_anns_d",
        )
        self.assertEqual(payload["decisions"]["canonical"], 31)
        self.assertEqual(payload["decisions"]["quarantined"], 1)
        self.assertEqual(payload["decisions"]["no_event"], 1)
        self.assertEqual(payload["decisions"]["failed"], 1)
        self.assertTrue(
            all(
                row["event_subject"] == "战略投资方"
                for row in rows_by_decision["canonical"]
            )
        )
        self.assertEqual(
            payload["extraction"]["contract"]["profileId"],
            "a-share-announcement-v1",
        )
        self.assertNotIn("champion", payload["extraction"])
        latest_batch = payload["extraction"]["latestBatch"]
        self.assertEqual(latest_batch["model"], "deepseek-v4-pro")
        self.assertEqual(latest_batch["runs"], 34)
        self.assertEqual(latest_batch["succeeded"], 32)
        self.assertEqual(latest_batch["noEvent"], 1)
        self.assertEqual(latest_batch["quarantined"], 1)
        self.assertEqual(latest_batch["failed"], 1)
        self.assertEqual(latest_batch["inputTokens"], 3100)
        self.assertEqual(latest_batch["outputTokens"], 930)
        self.assertEqual(payload["factorSupply"]["status"], "unavailable")
        self.assertFalse(payload["modelImpact"]["adopted"])
        self.assertEqual(payload["modelImpact"]["status"], "unavailable")
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("document_chunks", encoded)
        self.assertNotIn("董事会审议通过股份回购方案，回购金额", encoded)
        self.assertNotIn("raw_path", encoded)

    def test_intelligence_summary_exposes_local_artifact_worker_progress(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_intelligence(root)
            store = IntelligenceStore(
                root / "data" / "shared" / "intelligence"
            )
            with store.connect() as connection:
                document_id = int(
                    connection.execute(
                        "SELECT id FROM documents ORDER BY id LIMIT 1"
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO artifact_worker_jobs(
                        job_id, worker_id, stage, status, created_at,
                        lease_until, finished_at, manifest_hash,
                        result_hash, counts_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "awj-dashboard",
                        "coding-plan-a",
                        "parse",
                        "imported",
                        "2026-07-30T01:00:00+00:00",
                        "2026-07-30T02:00:00+00:00",
                        "2026-07-30T01:05:00+00:00",
                        "manifest",
                        "result",
                        json.dumps(
                            {
                                "processed": 1,
                                "succeeded": 1,
                                "failed_retryable": 0,
                                "failed_terminal": 0,
                            }
                        ),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO artifact_worker_items(
                        job_id, ordinal, document_id, input_hash,
                        status, error, updated_at
                    ) VALUES(
                        'awj-dashboard', 0, ?, 'input',
                        'succeeded', '', '2026-07-30T01:05:00+00:00'
                    )
                    """,
                    (document_id,),
                )
                connection.commit()

            payload = build_dashboard_intelligence_data(
                repo_root=root,
                market="a_share",
                agent="codex",
            )

        workers = payload["pipeline"]["artifactWorkers"]
        self.assertEqual(workers["completedDocuments"], 1)
        self.assertEqual(workers["parsedDocuments"], 1)
        self.assertEqual(workers["downloadedDocuments"], 0)
        self.assertEqual(workers["activeLeases"], 0)
        self.assertEqual(workers["stages"]["parse"]["imported"], 1)
        self.assertEqual(
            workers["latestFinishedAt"],
            "2026-07-30T01:05:00+00:00",
        )

    def test_intelligence_summary_uses_the_operational_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_intelligence(root)
            reports = root / "reports" / "intelligence"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "semantic_status_latest.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-07-29T07:00:00Z",
                        "metadata": {
                            "documents": 999,
                            "total_documents": 1003,
                        },
                        "artifacts": {
                            "by_status": {"downloaded": 300, "parsed": 200},
                        },
                        "semantic": {
                            "by_status": {"succeeded": 80, "no_event": 20},
                            "decisions": {
                                "canonical": 70,
                                "no_event": 20,
                                "quarantined": 4,
                                "failed": 6,
                            },
                        },
                        "pipeline": {
                            "stages": {
                                "catalogued": 999,
                                "pdf_ready": 300,
                                "parsed": 200,
                                "semantic_completed": 100,
                                "canonical_events": 70,
                            },
                            "backlog": {
                                "download": 699,
                                "parse": 100,
                                "semantic": 94,
                                "total": 893,
                            },
                            "sources": [
                                {
                                    "source": "tushare_announcement",
                                    "documents": 999,
                                    "latest_published_at": (
                                        "2026-07-29T04:00:00Z"
                                    ),
                                    "last_ingested_at": (
                                        "2026-07-29T04:30:00Z"
                                    ),
                                    "latest_run_status": "success",
                                    "fetched": 10,
                                    "inserted": 2,
                                    "cursor": "2026-07-29T04:30:00Z",
                                    "cursor_updated_at": (
                                        "2026-07-29T04:30:00Z"
                                    ),
                                }
                            ],
                        },
                        "quality": {},
                        "versions": {},
                        "capacity": {},
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "stock_analyze.dashboard_api._pipeline_sources",
                side_effect=AssertionError(
                    "snapshot-backed summary scanned the document catalog"
                ),
            ):
                payload = build_dashboard_intelligence_data(
                    repo_root=root,
                    market="a_share",
                    agent="codex",
                )

        self.assertEqual(payload["pipeline"]["documents"], 999)
        self.assertEqual(payload["pipeline"]["stages"]["parsed"], 200)
        self.assertEqual(payload["pipeline"]["backlog"]["total"], 893)
        self.assertEqual(
            payload["pipeline"]["sources"][0]["source"],
            "tushare_announcement",
        )
        self.assertEqual(payload["decisions"]["canonical"], 70)
        self.assertEqual(
            payload["extraction"]["semanticRuns"]["succeeded"],
            80,
        )

    def test_intelligence_dashboard_ignores_retired_semantic_registry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_intelligence(root)
            registry_path = (
                root
                / "data"
                / "shared"
                / "intelligence"
                / "semantic_registry.json"
            )
            registry_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "champion": {
                            "provider": "codex",
                            "model": "registered-champion",
                            "prompt_version": "announcement-events-v2",
                            "schema_version": "announcement-semantic-v1",
                            "taxonomy_version": "announcement-events-v1",
                            "parser_version": "announcement-layout-v1",
                            "promoted_at": "2026-07-21T09:00:00+00:00",
                        },
                    }
                ),
                encoding="utf-8",
            )

            payload = build_dashboard_intelligence_data(
                repo_root=root,
                market="a_share",
                agent="codex",
            )

        self.assertNotIn("champion", payload["extraction"])
        self.assertEqual(
            payload["extraction"]["contract"]["profileId"],
            "a-share-announcement-v1",
        )
        self.assertEqual(
            payload["extraction"]["latestBatch"]["model"],
            "deepseek-v4-pro",
        )

    def test_intelligence_latest_batch_prefers_semantic_daily_run_report(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_intelligence(root)
            job_id = "sj-live-dashboard"
            job_dir = (
                root
                / "data"
                / "shared"
                / "intelligence"
                / "extraction_jobs"
                / job_id
            )
            job_dir.mkdir(parents=True)
            (job_dir / "job.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "profile_id": "a-share-announcement-v1",
                        "prompt_version": "announcement-events-v1",
                        "schema_version": "announcement-semantic-v1",
                        "taxonomy_version": "announcement-events-v1",
                        "items": [
                            {
                                "document_id": 1,
                                "parser_version": "announcement-layout-v1",
                            },
                            {
                                "document_id": 2,
                                "parser_version": "announcement-layout-v1",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report_dir = root / "reports" / "intelligence"
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "semantic_daily_20260729.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-07-29T04:20:00+00:00",
                        "quality_status": "partial",
                        "prepared": {
                            "job_id": job_id,
                            "documents": 2,
                        },
                        "run": {
                            "status": "partial",
                            "job_id": job_id,
                            "executor": {
                                "provider": "openai-compatible",
                                "model": "deepseek-v4-pro",
                            },
                            "expected": 2,
                            "completed": 1,
                            "reused": 0,
                            "failed": 1,
                            "remaining": 1,
                            "validation_repairs": 1,
                            "validation_repair_failures": 1,
                            "usage": {
                                "input_tokens": 123,
                                "output_tokens": 45,
                                "total_tokens": 168,
                                "latency_ms": 1000,
                                "request_count": 3,
                            },
                            "started_at": "2026-07-29T04:18:00+00:00",
                            "finished_at": "2026-07-29T04:20:00+00:00",
                        },
                        "import": {
                            "valid": 0,
                            "no_event": 1,
                            "quarantined": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            payload = build_dashboard_intelligence_data(
                repo_root=root,
                market="a_share",
                agent="codex",
            )

        latest_batch = payload["extraction"]["latestBatch"]
        self.assertEqual(latest_batch["batchKey"], job_id)
        self.assertEqual(
            latest_batch["profileId"],
            "a-share-announcement-v1",
        )
        self.assertEqual(latest_batch["qualityStatus"], "partial")
        self.assertEqual(latest_batch["runs"], 2)
        self.assertEqual(latest_batch["noEvent"], 1)
        self.assertEqual(latest_batch["failed"], 1)
        self.assertEqual(latest_batch["remaining"], 0)
        self.assertEqual(latest_batch["inputTokens"], 123)
        self.assertEqual(latest_batch["outputTokens"], 45)
        self.assertEqual(latest_batch["requestCount"], 3)
        self.assertEqual(latest_batch["validationRepairs"], 1)
        self.assertEqual(latest_batch["validationRepairFailures"], 1)
        self.assertEqual(
            latest_batch["parserVersion"],
            "announcement-layout-v1",
        )

    def test_intelligence_factor_supply_and_model_effect_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_intelligence(root)
            (root / "configs" / "intelligence_factors.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "factor_sets": {
                            "event-lite-v1": {
                                "state": "research",
                                "features": ["event_net_strength_5d"],
                            }
                        },
                        "factors": {
                            "event_net_strength_5d": {"state": "observing"}
                        },
                    }
                ),
                encoding="utf-8",
            )
            report_dir = root / "reports" / "intelligence"
            report_dir.mkdir(parents=True)
            (report_dir / "factor_validation_a_share_20260720.json").write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "market": "a_share",
                        "snapshot_date": "20260720",
                        "rows": 4200,
                        "factors": {
                            "event_net_strength_5d": {
                                "coverage": 0.42,
                                "signal_activation_rate": 0.18,
                                "daily_ic_count": 36,
                                "mean_rank_ic": 0.021,
                                "recommendation": "observe",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (
                report_dir / "model_incremental_effect_a_share_20260720.json"
            ).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "complete",
                        "market": "a_share",
                        "as_of": "20260720",
                        "snapshot_date": "20260720",
                        "factor_set": "event-lite-v1",
                        "horizons": {
                            "5": {
                                "status": "complete",
                                "support": {
                                    "rows": 4200,
                                    "covered_ratio": 0.42,
                                    "active_ratio": 0.18,
                                },
                                "deltas": {"macro_f1": 0.012},
                            }
                        },
                        "qualified_horizons": 1,
                        "activation": "unchanged",
                    }
                ),
                encoding="utf-8",
            )

            payload = build_dashboard_intelligence_data(
                repo_root=root,
                market="a_share",
                agent="codex",
            )

        factor_supply = payload["factorSupply"]
        self.assertEqual(factor_supply["status"], "complete")
        self.assertEqual(factor_supply["snapshotDate"], "20260720")
        self.assertEqual(factor_supply["lifecycleCounts"]["observing"], 1)
        self.assertFalse(factor_supply["modelEligible"])
        self.assertEqual(
            factor_supply["factors"][0]["name"],
            "event_net_strength_5d",
        )
        self.assertEqual(factor_supply["factors"][0]["coverage"], 0.42)
        model_impact = payload["modelImpact"]
        self.assertEqual(model_impact["status"], "complete")
        self.assertEqual(model_impact["qualifiedHorizons"], 1)
        self.assertEqual(model_impact["activation"], "unchanged")
        self.assertFalse(model_impact["adopted"])
        self.assertIn("未进入正式模型", model_impact["reason"])

    def test_intelligence_event_detail_is_bounded_and_traceable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_intelligence(root)
            payload = build_dashboard_intelligence_event_data(
                repo_root=root,
                market="a_share",
                agent="codex",
                event_id="event-0",
            )

        self.assertEqual(payload["decision"], "canonical")
        self.assertEqual(payload["event"]["event_type"], "buyback")
        self.assertEqual(payload["event"]["lifecycle"], "announced")
        self.assertEqual(payload["issuer"]["code"], "600000.SH")
        self.assertEqual(payload["versions"]["model"], "deepseek-v4-pro")
        self.assertEqual(payload["scores"]["direction"], 0.7)
        self.assertEqual(payload["scores"]["materiality"], 0.75)
        self.assertLessEqual(len(payload["evidence"]), 100)
        self.assertLessEqual(len(payload["facts"]), 50)
        self.assertEqual(payload["evidence"][0]["page_number"], 1)
        self.assertEqual(
            payload["document"]["source_url"],
            "https://example.com/ann-0.pdf",
        )

    def test_intelligence_document_detail_is_bounded_and_unknown_ids_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_intelligence(root)
            payload = build_dashboard_intelligence_document_data(
                repo_root=root,
                market="a_share",
                agent="codex",
                document_id="1",
            )
            with self.assertRaises(DashboardResourceNotFound):
                build_dashboard_intelligence_event_data(
                    repo_root=root,
                    market="a_share",
                    agent="codex",
                    event_id="missing",
                )
            with self.assertRaises(DashboardResourceNotFound):
                build_dashboard_intelligence_document_data(
                    repo_root=root,
                    market="a_share",
                    agent="codex",
                    document_id="9999",
                )

        self.assertEqual(payload["document"]["document_id"], 1)
        self.assertLessEqual(len(payload["artifacts"]), 50)
        self.assertLessEqual(len(payload["decisions"]), 50)
        self.assertNotIn("chunks", payload)
        self.assertNotIn("raw_path", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
