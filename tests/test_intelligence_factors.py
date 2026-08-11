from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.intelligence.diagnostics import evaluate_event_factors
from stock_analyze.intelligence.factors import (
    EVENT_LITE_FACTOR_COLUMNS,
    attach_event_features,
)
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import MarketEvent, SourceDocument
from stock_analyze.intelligence.lifecycle import (
    active_features,
    load_factor_records,
    model_iteration_features,
)


class IntelligenceFactorsTest(unittest.TestCase):
    @staticmethod
    def _insert_scored_buyback(
        store: IntelligenceStore,
        *,
        source_id: str,
        published_at: str,
        first_seen_at: str,
        effective_at: str,
    ) -> int:
        document_id, _ = store.insert_document(SourceDocument(
            source="tushare_announcement",
            source_id=source_id,
            title="股份回购公告",
            published_at=published_at,
            first_seen_at=first_seen_at,
            effective_at=effective_at,
            source_url=f"https://x.test/{source_id}",
        ))
        event_id = f"event-{source_id}"
        store.insert_event(MarketEvent(
            event_id=event_id,
            document_id=document_id,
            event_type="buyback",
            direction=1,
            strength=1,
            confidence=1,
            novelty=1,
            horizon_days=20,
            published_at=published_at,
            effective_at=effective_at,
            evidence="股份回购",
            entities=({"entity_id": "000001", "confidence": 1},),
            metadata={"market": "a_share"},
        ))
        with store.connect() as connection:
            connection.execute(
                """
                INSERT INTO event_scores(
                    event_id, relevance, novelty, materiality, certainty,
                    source_credibility, direction, confidence,
                    scoring_version, inputs_json, scored_at
                ) VALUES (?, 1, 1, 1, 1, 1, 1, 1, 'test-v1', '{}', ?)
                """,
                (event_id, first_seen_at),
            )
        return document_id

    def test_observed_policy_does_not_backfill_a_late_known_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(root)
            self._insert_scored_buyback(
                store,
                source_id="late-observed",
                published_at="2021-03-15T10:00:00+08:00",
                first_seen_at="2026-07-24T10:00:00+08:00",
                effective_at="2021-03-15T10:00:00+08:00",
            )
            features = pd.DataFrame({
                "code": ["000001"],
                "trade_date": ["20210316"],
            })

            enriched = attach_event_features(
                features,
                root,
                market="a_share",
                as_of="2026-07-24",
                availability_policy="observed",
            )

        self.assertEqual(enriched.iloc[0]["buyback_event_score_20d"], 0.0)

    def test_research_policy_rebuilds_pre_cutoff_availability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(root)
            document_id = self._insert_scored_buyback(
                store,
                source_id="late-research",
                published_at="2021-03-15T10:00:00+08:00",
                first_seen_at="2026-07-24T10:00:00+08:00",
                effective_at="2021-03-15T10:00:00+08:00",
            )
            store.upsert_reconstructed_availability(
                document_id,
                source_recorded_at="2021-03-15T10:01:00+08:00",
                research_available_at="2021-03-15T10:01:00+08:00",
                provenance="reconstructed_rec_time",
            )
            features = pd.DataFrame({
                "code": ["000001"],
                "trade_date": ["20210316"],
            })

            enriched = attach_event_features(
                features,
                root,
                market="a_share",
                as_of="2026-07-24",
                availability_policy="research",
            )

        self.assertGreater(enriched.iloc[0]["buyback_event_score_20d"], 0.0)
        self.assertGreater(enriched.iloc[0]["event_net_strength_5d"], 0.0)
        self.assertGreater(
            enriched.iloc[0]["event_net_materiality_20d"],
            0.0,
        )
        self.assertEqual(
            set(EVENT_LITE_FACTOR_COLUMNS),
            {
                "event_net_strength_5d",
                "event_net_materiality_20d",
                "event_relevance_20d",
                "event_certainty_20d",
                "event_revision_risk_20d",
                "announcement_novelty_20d",
                "event_source_confirmation",
                "event_data_coverage",
            },
        )

    def test_research_policy_never_reconstructs_after_historical_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(root)
            document_id = self._insert_scored_buyback(
                store,
                source_id="post-cutoff",
                published_at="2026-07-20T10:00:00+08:00",
                first_seen_at="2026-07-24T10:00:00+08:00",
                effective_at="2026-07-20T10:00:00+08:00",
            )
            store.upsert_reconstructed_availability(
                document_id,
                source_recorded_at="2026-07-20T10:01:00+08:00",
                research_available_at="2026-07-20T10:01:00+08:00",
                provenance="reconstructed_rec_time",
            )
            features = pd.DataFrame({
                "code": ["000001"],
                "trade_date": ["20260721"],
            })

            enriched = attach_event_features(
                features,
                root,
                market="a_share",
                as_of="2026-07-24",
                availability_policy="research",
            )

        self.assertEqual(enriched.iloc[0]["buyback_event_score_20d"], 0.0)

    def test_event_is_invisible_before_effective_date_and_decays_afterward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(root)
            doc_id, _ = store.insert_document(SourceDocument(
                source="cninfo", source_id="1", title="buyback",
                published_at="2026-07-18T16:00:00Z", first_seen_at="2026-07-18T16:01:00Z",
                effective_at="2026-07-21T00:00:00Z", source_url="https://x.test/1", content=b"buyback",
            ))
            store.insert_event(MarketEvent(
                event_id="e1", document_id=doc_id, event_type="buyback",
                direction=1, strength=1, confidence=1, novelty=1, horizon_days=20,
                published_at="2026-07-18T16:00:00Z", effective_at="2026-07-21T00:00:00Z",
                evidence="buyback", entities=({"entity_id": "000001", "confidence": 1},),
                metadata={"market": "a_share"},
            ))
            features = pd.DataFrame({
                "code": ["000001", "000001", "000001"],
                "trade_date": ["20260718", "20260721", "20260728"],
                "industry": ["bank", "bank", "bank"],
                "momentum_20": [0.1, 0.1, 0.1],
                "volume_ratio_5_20": [1.2, 1.2, 1.2],
            })
            enriched = attach_event_features(features, root, market="a_share", as_of="2026-07-28")
            self.assertTrue(pd.isna(enriched.iloc[0]["event_positive_decay_5d"]))
            self.assertAlmostEqual(enriched.iloc[1]["event_positive_decay_5d"], 1.0)
            self.assertLess(enriched.iloc[2]["event_positive_decay_5d"], enriched.iloc[1]["event_positive_decay_5d"])

    def test_missing_source_has_explicit_zero_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            features = pd.DataFrame({"code": ["1"], "trade_date": ["20260718"]})
            enriched = attach_event_features(features, Path(tmp), market="a_share", as_of="2026-07-18")
            self.assertEqual(enriched.iloc[0]["event_data_coverage"], 0.0)

    def test_no_event_semantic_run_counts_as_processed_coverage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(root)
            document_id, _ = store.insert_document(SourceDocument(
                source="tushare_announcement",
                source_id="semantic-no-event",
                title="普通公告",
                published_at="2026-07-18T08:00:00Z",
                first_seen_at="2026-07-18T08:01:00Z",
                effective_at="2026-07-18T08:00:00Z",
                source_url="https://x.test/no-event",
                metadata={
                    "ts_code": "000001.SZ",
                    "security_links": [{
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "provenance": "test",
                    }],
                },
            ))
            digest = hashlib.sha256(b"artifact").hexdigest()
            claim = store.claim_semantic_run(
                document_id=document_id,
                artifact_hash=digest,
                provider="codex",
                model="test",
                prompt_version="semantic-extract-v1",
                schema_version="announcement-events-v1-lite",
                taxonomy_version="cn-announcement-taxonomy-v1",
                parser_version="announcement-layout-v1",
                input_hash=hashlib.sha256(b"input").hexdigest(),
            )
            store.finish_semantic_run(
                str(claim["run_id"]),
                status="no_event",
                output_hash=hashlib.sha256(b"output").hexdigest(),
                output_uri="localblob://artifacts/announcements/test.json",
            )
            features = pd.DataFrame({
                "code": ["000001"],
                "trade_date": ["20260721"],
            })

            enriched = attach_event_features(
                features,
                root,
                market="a_share",
                as_of="2026-07-21",
            )

        self.assertEqual(enriched.iloc[0]["event_data_coverage"], 1.0)
        self.assertEqual(enriched.iloc[0]["event_net_materiality_20d"], 0.0)

    def test_a_share_events_use_china_day_and_exclude_next_morning_from_prior_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(root)
            doc_id, _ = store.insert_document(SourceDocument(
                source="tushare_announcement",
                source_id="china-day",
                title="股份回购公告",
                published_at="2026-07-23T16:00:00Z",
                first_seen_at="2026-07-23T23:00:00Z",
                effective_at="2026-07-23T16:00:00Z",
                source_url="https://x.test/china-day",
            ))
            store.insert_event(MarketEvent(
                event_id="china-day",
                document_id=doc_id,
                event_type="buyback",
                direction=1,
                strength=1,
                confidence=1,
                novelty=1,
                horizon_days=20,
                published_at="2026-07-23T16:00:00Z",
                effective_at="2026-07-23T16:00:00Z",
                evidence="buyback",
                entities=({"entity_id": "000001", "confidence": 1},),
                metadata={"market": "a_share"},
            ))
            features = pd.DataFrame({
                "code": ["000001", "000001"],
                "trade_date": ["20260723", "20260724"],
            })

            prior_day = attach_event_features(
                features.iloc[:1],
                root,
                market="a_share",
                as_of="2026-07-23",
            )
            current_day = attach_event_features(
                features,
                root,
                market="a_share",
                as_of="2026-07-24",
            )

        self.assertTrue(pd.isna(prior_day.iloc[0]["event_positive_decay_5d"]))
        self.assertTrue(pd.isna(current_day.iloc[0]["event_positive_decay_5d"]))
        self.assertAlmostEqual(current_day.iloc[1]["event_positive_decay_5d"], 1.0)

    def test_policy_features_are_mapped_by_industry_and_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(root)
            doc_id, _ = store.insert_document(SourceDocument(
                source="ndrc_policy", source_id="p1", title="support semiconductor",
                published_at="2026-07-01T00:00:00Z", first_seen_at="2026-07-01T00:01:00Z",
                effective_at="2026-07-01T00:00:00Z", source_url="https://x.test/p1",
            ))
            store.insert_event(MarketEvent(
                event_id="p1", document_id=doc_id, event_type="industry_support",
                direction=1, strength=1, confidence=1, novelty=1, horizon_days=60,
                published_at="2026-07-01T00:00:00Z", effective_at="2026-07-01T00:00:00Z",
                evidence="support", entities=({
                    "entity_type": "industry", "entity_id": "电子", "entity_name": "电子",
                    "industry": "电子", "confidence": 1,
                },), metadata={"market": "all"},
            ))
            features = pd.DataFrame({
                "code": ["000001", "000002", "000003"],
                "trade_date": ["20260701", "20260702", "20260702"],
                "industry": ["电子", "电子", "银行"],
            })
            enriched = attach_event_features(features, root, market="a_share", as_of="2026-07-02")
            self.assertAlmostEqual(enriched.iloc[0]["policy_industry_exposure_20d"], 1.0)
            self.assertGreater(enriched.iloc[1]["policy_industry_exposure_20d"], 0.9)
            self.assertTrue(pd.isna(enriched.iloc[2]["policy_industry_exposure_20d"]))

    def test_lifecycle_only_exposes_approved_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "configs" / "states.json"
            path.parent.mkdir()
            reports = root / "reports"
            reports.mkdir()
            hashes = {}
            for name in ("b", "c"):
                payload = f'{{"factor":"{name}","status":"qualified"}}\n'
                report_path = reports / f"{name}.json"
                report_path.write_text(payload, encoding="utf-8")
                hashes[name] = hashlib.sha256(
                    payload.encode("utf-8")
                ).hexdigest()
            path.write_text(
                json.dumps({
                    "factors": {
                        "a": "observing",
                        "legacy_unverified": "model_iteration",
                        "b": {
                            "state": "model_iteration",
                            "evidence": {
                                "status": "qualified",
                                "report_path": "reports/b.json",
                                "report_hash": hashes["b"],
                            },
                        },
                        "c": {
                            "state": "active",
                            "evidence": {
                                "status": "qualified",
                                "report_path": "reports/c.json",
                                "report_hash": hashes["c"],
                            },
                        },
                    }
                }),
                encoding="utf-8",
            )
            self.assertEqual(model_iteration_features(path), {"b", "c"})
            self.assertEqual(active_features(path), {"c"})
            self.assertEqual(
                load_factor_records(path)["legacy_unverified"]["effective_state"],
                "observing",
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["factors"]["b"]["evidence"]["report_hash"] = "0" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(
                load_factor_records(path)["b"]["effective_state"],
                "observing",
            )

    def test_factor_evaluation_requires_stable_cross_sectional_evidence(self) -> None:
        feature_rows = []
        label_rows = []
        for day in range(1, 26):
            trade_date = f"202606{day:02d}"
            for index in range(10):
                signal = float(index)
                feature_rows.append({
                    "code": f"{index:06d}",
                    "trade_date": trade_date,
                    "event_positive_decay_5d": signal,
                })
                for horizon, scale in ((5, 0.01), (20, 0.004)):
                    label_rows.append({
                        "code": f"{index:06d}",
                        "trade_date": trade_date,
                        "horizon": horizon,
                        "excess_return": signal * scale,
                    })

        result = evaluate_event_factors(
            pd.DataFrame(feature_rows),
            pd.DataFrame(label_rows),
        )

        row = result["factors"]["event_positive_decay_5d"]
        self.assertEqual(row["recommendation"], "model_iteration")
        self.assertEqual(row["gate_reasons"], [])
        self.assertGreaterEqual(row["ic_sign_stability"], 0.95)
        self.assertGreater(row["ablation_long_short_spread"], 0.0)
        self.assertLess(row["false_positive_rate"], 0.1)
        self.assertIn("5", row["horizon_rank_ic"])
        self.assertIn("20", row["horizon_rank_ic"])

    def test_directionless_or_short_sample_factor_remains_observing(self) -> None:
        features = pd.DataFrame({
            "code": [f"{index:06d}" for index in range(6)],
            "trade_date": ["20260710"] * 6,
            "event_data_coverage": [1.0] * 6,
        })
        labels = pd.DataFrame({
            "code": features["code"],
            "trade_date": features["trade_date"],
            "horizon": [5] * 6,
            "excess_return": [0.01, -0.01, 0.02, -0.02, 0.0, 0.01],
        })

        row = evaluate_event_factors(features, labels)["factors"]["event_data_coverage"]

        self.assertEqual(row["recommendation"], "observe")
        self.assertIn("direction_not_declared", row["gate_reasons"])
        self.assertIn("daily_ic_count_below_floor", row["gate_reasons"])

    def test_diagnostics_distinguish_non_null_from_real_activation(
        self,
    ) -> None:
        features = pd.DataFrame({
            "code": [f"{index:06d}" for index in range(6)],
            "trade_date": ["20260710"] * 6,
            "event_net_materiality_20d": [0.0] * 6,
            "event_data_coverage": [0.0] * 6,
        })
        labels = pd.DataFrame({
            "code": features["code"],
            "trade_date": features["trade_date"],
            "horizon": [5] * 6,
            "excess_return": [0.01, -0.01, 0.02, -0.02, 0.0, 0.01],
        })

        row = evaluate_event_factors(
            features,
            labels,
        )["factors"]["event_net_materiality_20d"]

        self.assertEqual(row["non_null_coverage"], 1.0)
        self.assertEqual(row["source_coverage"], 0.0)
        self.assertEqual(row["signal_activation_rate"], 0.0)
        self.assertEqual(row["coverage"], 0.0)
        self.assertIn("coverage_below_floor", row["gate_reasons"])


if __name__ == "__main__":
    unittest.main()
