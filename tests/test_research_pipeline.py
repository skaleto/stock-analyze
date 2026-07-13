import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.research.pipeline import ResearchPipeline
from stock_analyze.research.source_features import SourceCollection


class ResearchPipelineTest(unittest.TestCase):
    def _write_history(self, root: Path, rows: int = 140) -> None:
        cache = root / "data" / "shared" / "cache"
        cache.mkdir(parents=True)
        dates = pd.date_range("2026-01-01", periods=rows, freq="B")
        close = 10.0 + np.sin(np.arange(rows) / 5.0) + np.arange(rows) * 0.01
        pd.DataFrame(
            {
                "日期": dates.strftime("%Y-%m-%d"),
                "开盘": close - 0.1,
                "最高": close + 0.3,
                "最低": close - 0.3,
                "收盘": close,
                "成交量": 1_000_000 + np.arange(rows) * 1000,
                "成交额": 20_000_000 + np.arange(rows) * 10_000,
            }
        ).to_csv(cache / "history_000001_20260710_1098.csv", index=False)

    def test_prepare_is_idempotent_and_research_runs_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)

            first = pipeline.prepare_data()
            second = pipeline.prepare_data()
            research = pipeline.run_research()
            snapshot = pipeline.store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(first["status"], "built")
        self.assertEqual(second["status"], "cached")
        self.assertEqual(snapshot.iloc[0]["code"], "000001")
        self.assertGreater(research["labels_rows"], 0)
        self.assertGreater(research["events_rows"], 0)
        self.assertEqual(research["stages"], ["features", "labels", "events", "regimes", "event_study"])

    def test_prediction_model_failure_writes_fallback_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.prepare_data()
            with patch("stock_analyze.research.pipeline.load_model_bundle", side_effect=ValueError("bad model")):
                result = pipeline.predict()

            self.assertEqual(result["status"], "fallback")
            self.assertTrue(Path(result["health_path"]).exists())

    def test_online_prepare_persists_normalized_source_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10")
            sources = SourceCollection(
                frames={
                    "daily_basic": pd.DataFrame([
                        {"ts_code": "000001.SZ", "trade_date": "20260710", "pe_ttm": 12.0, "source": "tushare:daily_basic", "observed_at": "2026-07-10T18:00:00+08:00"}
                    ])
                },
                health=pd.DataFrame([{"source": "daily_basic", "failed": False, "rows": 1}]),
            )
            with patch.object(pipeline, "_collect_sources", return_value=sources):
                result = pipeline.prepare_data()

            raw_path = root / "data" / "research" / "raw" / "a_share" / "20260710" / "daily_basic.parquet"
            raw_exists = raw_path.exists()
            snapshot = pipeline.store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(result["sources"], 1)
        self.assertTrue(raw_exists)
        self.assertEqual(float(snapshot.loc[snapshot["trade_date"] == "20260710", "pe_ttm"].iloc[-1]), 12.0)

    def test_a_share_source_collection_limits_financial_deep_fetch(self):
        class FakeProvider:
            pro = object()

            def _safe_pro_call(self, label, call):
                return call()

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(Path(tmp), market="a_share", agent="codex", as_of="2026-07-10")
            empty = SourceCollection(frames={}, health=pd.DataFrame())
            with (
                patch("stock_analyze.markets.a_share.data_provider.make_provider", return_value=FakeProvider()),
                patch("stock_analyze.markets.a_share.market_data.collect_research_sources", return_value=empty) as collect,
            ):
                pipeline._collect_sources([f"{index:06d}" for index in range(100)])

        self.assertEqual(len(collect.call_args.kwargs["codes"]), 40)


if __name__ == "__main__":
    unittest.main()
