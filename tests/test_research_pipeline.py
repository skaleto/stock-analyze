import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.research.pipeline import ResearchPipeline
from stock_analyze.research.source_features import SourceCollection


class ResearchPipelineTest(unittest.TestCase):
    def _write_history(self, root: Path, rows: int = 140, code: str = "000001") -> None:
        cache = root / "data" / "shared" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
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
        ).to_csv(cache / f"history_{code}_20260710_1098.csv", index=False)

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

    def test_a_share_keeps_full_history_sample_and_latest_row_for_all_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for code in ("000001", "000002", "000003"):
                self._write_history(root, rows=80, code=code)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
                max_full_history_instruments=1,
            )

            result = pipeline.prepare_data()
            snapshot = pipeline.store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(result["instruments"], 3)
        self.assertEqual(snapshot.loc[snapshot["history_role"] == "full", "code"].nunique(), 1)
        self.assertEqual(snapshot.loc[snapshot["history_role"] == "latest_only", "code"].nunique(), 2)
        self.assertEqual(len(snapshot.loc[snapshot["history_role"] == "latest_only"]), 2)

    def test_a_share_history_sample_is_stable_and_not_code_prefix_biased(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(
                Path(tmp), market="a_share", agent="codex", max_full_history_instruments=10
            )
            codes = [f"{index:06d}" for index in range(100)]
            first = pipeline._full_history_codes(codes)
            second = pipeline._full_history_codes(list(reversed(codes)))

        self.assertEqual(first, second)
        self.assertEqual(len(first), 10)
        self.assertGreater(max(int(code) for code in first), 50)

    def test_a_share_cache_selection_prefers_three_year_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root, rows=80, code="000001")
            cache = root / "data" / "shared" / "cache"
            (cache / "history_000001_20260710_220.csv").write_text(
                (cache / "history_000001_20260710_1098.csv").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10")

            selected = pipeline._history_files()

        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].name.endswith("_1098.csv"))


if __name__ == "__main__":
    unittest.main()
