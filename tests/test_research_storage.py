import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.schemas import ModelIdentity, PredictionRecord
from stock_analyze.research.storage import ResearchStore


class ResearchStorageTest(unittest.TestCase):
    def test_arrow_integer_downcasts_at_exact_int32_boundaries(self):
        frame = pd.DataFrame({
            "value": pd.Series(
                [-(2**31), 2**31 - 1],
                dtype="int64[pyarrow]",
            )
        })

        normalized = ResearchStore._normalize_identifiers(frame)

        self.assertEqual(str(normalized["value"].dtype), "int32[pyarrow]")
        self.assertEqual(normalized["value"].tolist(), [-(2**31), 2**31 - 1])

    def test_arrow_large_signed_and_unsigned_integers_round_trip_without_downcast(self):
        frame = pd.DataFrame({
            "large_signed": pd.Series([3_000_000_000], dtype="int64[pyarrow]"),
            "large_unsigned": pd.Series([3_000_000_000], dtype="uint64[pyarrow]"),
        })
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(Path(tmp))
            store.write_feature_snapshot("a_share", "2026-07-10", frame)

            loaded = store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(int(loaded.iloc[0]["large_signed"]), 3_000_000_000)
        self.assertEqual(int(loaded.iloc[0]["large_unsigned"]), 3_000_000_000)
        self.assertEqual(str(loaded["large_signed"].dtype), "int64[pyarrow]")
        self.assertEqual(str(loaded["large_unsigned"].dtype), "uint64[pyarrow]")

    def test_nullable_arrow_large_integer_round_trip_stays_nullable_integer(self):
        frame = pd.DataFrame({
            "large_nullable": pd.Series(
                [3_000_000_001, pd.NA],
                dtype="int64[pyarrow]",
            )
        })
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(Path(tmp))
            store.write_feature_snapshot("a_share", "2026-07-10", frame)

            loaded = store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(int(loaded.iloc[0]["large_nullable"]), 3_000_000_001)
        self.assertTrue(pd.isna(loaded.iloc[1]["large_nullable"]))
        self.assertEqual(str(loaded["large_nullable"].dtype), "int64[pyarrow]")

    def test_model_identity_includes_account_scope(self):
        identity = ModelIdentity(
            market="a_share",
            account_scope="hs300",
            horizon=3,
            model_version="v1",
        )

        self.assertEqual(identity.key, "a_share/hs300/3/v1")

    def test_model_root_is_partitioned_by_account_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(Path(tmp))

            path = store.model_root("a_share", "hs300", 3)

        self.assertEqual(path, Path(tmp) / "models" / "a_share" / "hs300" / "3")

    def test_prediction_probabilities_must_sum_to_one(self):
        with self.assertRaisesRegex(ValueError, "prediction_probability_sum"):
            PredictionRecord(
                code="000001",
                as_of="2026-07-10",
                horizon=5,
                p_up=0.7,
                p_flat=0.2,
                p_down=0.2,
            )

    def test_prediction_horizon_must_be_supported(self):
        with self.assertRaisesRegex(ValueError, "prediction_horizon"):
            PredictionRecord(
                code="000001",
                as_of="2026-07-10",
                horizon=7,
                p_up=0.4,
                p_flat=0.2,
                p_down=0.4,
            )

    def test_feature_snapshot_preserves_text_codes(self):
        frame = pd.DataFrame(
            [{"code": "000001", "trade_date": "20260710", "momentum_20": 0.12}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(Path(tmp))
            store.write_feature_snapshot("a_share", "2026-07-10", frame)

            loaded = store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(loaded.iloc[0]["code"], "000001")
        self.assertEqual(loaded.iloc[0]["trade_date"], "20260710")

    def test_latest_common_snapshot_uses_most_recent_date_not_after_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(Path(tmp))
            for day in ("2026-07-16", "2026-07-17"):
                frame = pd.DataFrame([{"code": "000001", "trade_date": day.replace("-", "")}])
                store.write_feature_snapshot("a_share", day, frame)
                store.write_label_snapshot("a_share", day, frame.assign(horizon=5, label="up"))

            selected = store.latest_common_snapshot_date(
                "a_share", as_of="2026-07-18"
            )

        self.assertEqual(selected, "20260717")

    def test_attribution_snapshot_is_scoped_and_preserves_lineage_ids(self):
        frame = pd.DataFrame([
            {
                "strategy_id": "trend-v2",
                "account_id": "hs300",
                "code": "000001",
                "model_version": "A20-V005",
                "holding_episode_id": "000001-20260710",
                "net_pnl": 12.5,
            }
        ])
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(Path(tmp))
            path = store.write_attribution_snapshot(
                "a_share",
                "2026-07-24",
                "trend-v2",
                "hs300",
                frame,
            )
            loaded = store.read_attribution_snapshot(
                "a_share",
                "2026-07-24",
                "trend-v2",
                "hs300",
            )

        self.assertEqual(
            path.name,
            "20260724__trend-v2__hs300.parquet",
        )
        self.assertEqual(loaded.iloc[0]["code"], "000001")
        self.assertEqual(
            loaded.iloc[0]["holding_episode_id"],
            "000001-20260710",
        )

    def test_prune_dated_artifacts_keeps_recent_and_monthly_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(Path(tmp))
            dates = [
                "20260131", "20260227", "20260331", "20260430",
                "20260714", "20260715", "20260716", "20260717",
            ]
            for category in ("features", "labels", "events"):
                directory = Path(tmp) / category / "a_share"
                directory.mkdir(parents=True)
                for run_key in dates:
                    (directory / f"{run_key}.parquet").write_bytes(b"snapshot")

            removed = store.prune_dated_artifacts(
                "a_share",
                categories=("features", "labels", "events"),
                keep_recent=3,
                keep_monthly=3,
            )
            remaining = {
                path.stem
                for path in (Path(tmp) / "features" / "a_share").glob("*.parquet")
            }

        self.assertEqual(
            remaining,
            {"20260227", "20260331", "20260430", "20260715", "20260716", "20260717"},
        )
        self.assertEqual(removed, 6)


if __name__ == "__main__":
    unittest.main()
