from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pyarrow.parquet as pq

from stock_analyze.research import a_share_materializer as materializer
from stock_analyze.research.a_share_materializer import (
    materialize_a_share_research_data,
)


def _paused_materialization_worker(
    repo_root: str,
    cache_root: str,
    start: date,
    end: date,
    as_of: str,
    ready,
    release,
    result,
) -> None:
    original = materializer._build_history
    paused = False

    def pause_once(*args, **kwargs):
        nonlocal paused
        if not paused:
            paused = True
            ready.set()
            if not release.wait(20):
                raise TimeoutError("test_release_timeout")
        return original(*args, **kwargs)

    try:
        with patch.object(materializer, "_build_history", side_effect=pause_once):
            summary = materialize_a_share_research_data(
                repo_root=Path(repo_root),
                cache_root=Path(cache_root),
                start=start,
                end=end,
                as_of=as_of,
            )
        result.put(("ok", summary["status"]))
    except BaseException as exc:  # pragma: no cover - relayed to parent
        result.put(("error", repr(exc)))


def _try_flock_worker(lock_path: str, result) -> None:
    import fcntl

    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result.put(False)
            return
        result.put(True)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class AShareResearchMaterializerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.cache_root = self.repo_root / "input" / "backtest_cache"
        self.start = date(2020, 1, 2)
        self.end = date(2020, 2, 3)
        self.as_of = "20200203"
        self._write_fixture()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=columns).to_csv(path, index=False)

    def _write_fixture(self) -> None:
        self._write_csv(
            self.cache_root / "trade_cal.csv",
            [
                {"exchange": "SSE", "cal_date": "20200102", "is_open": 1, "pretrade_date": "20191231"},
                {"exchange": "SSE", "cal_date": "20200103", "is_open": 1, "pretrade_date": "20200102"},
                {"exchange": "SSE", "cal_date": "20200203", "is_open": 1, "pretrade_date": "20200123"},
            ],
        )
        self._write_csv(
            self.cache_root / "stock_basic.csv",
            [
                {"ts_code": "000001.SZ", "symbol": "000001", "name": "未来主数据名", "area": "深圳", "industry": "银行", "list_date": "19910403", "delist_date": "", "list_status": "L"},
                {"ts_code": "000002.SZ", "symbol": "000002", "name": "万科A", "area": "深圳", "industry": "地产", "list_date": "19910129", "delist_date": "", "list_status": "L"},
                {"ts_code": "000003.SZ", "symbol": "000003", "name": "退市样本", "area": "深圳", "industry": "制造", "list_date": "19910130", "delist_date": "20200103", "list_status": "D"},
            ],
        )

        daily_rows = {
            "2020-01-02": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "2020-01-03": ["000002.SZ", "000003.SZ"],
            "2020-02-03": ["000001.SZ", "000002.SZ"],
        }
        for day, codes in daily_rows.items():
            raw_date = day.replace("-", "")
            self._write_csv(
                self.cache_root / "daily" / f"{day}.csv",
                [
                    {
                        "ts_code": code,
                        "trade_date": raw_date,
                        "open": 10.0 + offset,
                        "high": 10.8 + offset,
                        "low": 9.8 + offset,
                        "close": 10.5 + offset,
                        "pre_close": 10.0 + offset,
                        "vol": 1000 + offset,
                        "amount": 10000 + offset,
                    }
                    for offset, code in enumerate(codes)
                ],
            )
            self._write_csv(
                self.cache_root / "daily_basic" / f"{day}.csv",
                [
                    {
                        "ts_code": code,
                        "trade_date": raw_date,
                        "pe_ttm": 10.0 + offset,
                        "pb": 1.0 + offset,
                        "dv_ttm": 0.5 + offset,
                        "turnover_rate": 2.0 + offset,
                        "total_mv": 100000.0 + offset,
                    }
                    for offset, code in enumerate(codes)
                ],
            )
        suspend_columns = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
        self._write_csv(
            self.cache_root / "suspend_d" / "2020-01-02.csv",
            [{"ts_code": "000002.SZ", "trade_date": "20200102", "suspend_timing": "09:30", "suspend_type": "R"}],
            suspend_columns,
        )
        self._write_csv(
            self.cache_root / "suspend_d" / "2020-01-03.csv",
            [{"ts_code": "000001.SZ", "trade_date": "20200103", "suspend_timing": "09:30-15:00", "suspend_type": "S"}],
            suspend_columns,
        )
        self._write_csv(
            self.cache_root / "suspend_d" / "2020-02-03.csv",
            [{"ts_code": "000002.SZ", "trade_date": "20200203", "suspend_timing": "09:30-10:30", "suspend_type": "S"}],
            suspend_columns,
        )

        index_rows = {
            "000300_2020-01.csv": [("000300.SH", "000001.SZ")],
            "000905_2020-01.csv": [("000905.SH", "000003.SZ")],
            "000300_2020-02.csv": [("000300.SH", "000002.SZ")],
            "000905_2020-02.csv": [("000905.SH", "000001.SZ")],
        }
        for filename, members in index_rows.items():
            self._write_csv(
                self.cache_root / "index_weight" / filename,
                [
                    {"index_code": index_code, "con_code": code, "trade_date": "20200131" if "01.csv" in filename else "20200203", "weight": 1.0}
                    for index_code, code in members
                ],
            )

        names = {
            "000001.SZ": [
                {"ts_code": "000001.SZ", "name": "平安旧名", "start_date": "19910403", "end_date": "20200131", "ann_date": "19910403", "change_reason": "上市"},
                {"ts_code": "000001.SZ", "name": "平安新名", "start_date": "20200201", "end_date": "", "ann_date": "20200201", "change_reason": "更名"},
            ],
            "000002.SZ": [
                {"ts_code": "000002.SZ", "name": "万科A", "start_date": "19910129", "end_date": "", "ann_date": "19910129", "change_reason": "上市"},
            ],
            "000003.SZ": [
                {"ts_code": "000003.SZ", "name": "退市样本", "start_date": "19910130", "end_date": "20200103", "ann_date": "19910130", "change_reason": "上市"},
            ],
        }
        status = {
            "000001.SZ": [("20200102", "1", "1"), ("20200103", "0", "1"), ("20200203", "1", "0")],
            "000002.SZ": [("20200102", "1", "0"), ("20200103", "1", "0"), ("20200203", "1", "0")],
            "000003.SZ": [("20200102", "1", "0"), ("20200103", "1", "0")],
        }
        for code in ("000001.SZ", "000002.SZ", "000003.SZ"):
            self._write_csv(self.cache_root / "namechange" / f"{code}.csv", names[code])
            fina_rows = [
                {"ts_code": code, "ann_date": "20200120", "end_date": "20191231", "roe": 8.0, "grossprofit_margin": 20.0, "debt_to_assets": 50.0, "netprofit_yoy": 4.0},
            ]
            if code == "000001.SZ":
                fina_rows.append(
                    {"ts_code": code, "ann_date": "20200202", "end_date": "20191231", "roe": 9.5, "grossprofit_margin": 21.0, "debt_to_assets": 49.0, "netprofit_yoy": 5.0}
                )
            self._write_csv(self.cache_root / "fina_indicator" / f"{code}.csv", fina_rows)
            self._write_csv(
                self.cache_root / "income" / f"{code}.csv",
                [
                    {
                        "ts_code": code, "ann_date": "20200120",
                        "f_ann_date": "20200120", "end_date": "20191231",
                        "report_type": "1", "update_flag": "0",
                        "revenue": 100.0, "operate_profit": 20.0,
                        "n_income": 15.0, "total_cogs": 70.0, "rd_exp": 5.0,
                    },
                    {
                        "ts_code": code, "ann_date": "20200202",
                        "f_ann_date": "20200202", "end_date": "20191231",
                        "report_type": "1", "update_flag": "1",
                        "revenue": 110.0, "operate_profit": 22.0,
                        "n_income": 16.0, "total_cogs": 76.0, "rd_exp": 6.0,
                    },
                ],
            )
            self._write_csv(
                self.cache_root / "balancesheet" / f"{code}.csv",
                [{
                    "ts_code": code, "ann_date": "20200120",
                    "f_ann_date": "20200120", "end_date": "20191231",
                    "report_type": "1", "update_flag": "0",
                    "total_assets": 500.0,
                }],
            )
            self._write_csv(
                self.cache_root / "cashflow" / f"{code}.csv",
                [{
                    "ts_code": code, "ann_date": "20200120",
                    "f_ann_date": "20200120", "end_date": "20191231",
                    "report_type": "1", "update_flag": "0",
                    "n_cashflow_act": 18.0, "free_cashflow": 12.0,
                }],
            )
            self._write_csv(
                self.cache_root / "adj_factor" / f"{code}.csv",
                [
                    {"ts_code": code, "trade_date": raw_date, "adj_factor": 1.0}
                    for raw_date, _, _ in status[code]
                ],
            )
            self._write_csv(
                self.cache_root / "baostock_status" / f"{code}.csv",
                [
                    {"ts_code": code, "trade_date": raw_date, "tradestatus": tradestatus, "is_st": is_st, "st_source": "baostock_history_isST_v1", "code": f"sz.{code[:6]}"}
                    for raw_date, tradestatus, is_st in status[code]
                ],
            )

        for benchmark in ("000300", "000905"):
            self._write_csv(
                self.cache_root / "benchmark_daily" / f"{benchmark}.csv",
                [
                    {"ts_code": f"{benchmark}.SH", "trade_date": raw_date, "open": 4000.0 + offset, "high": 4020.0 + offset, "low": 3990.0 + offset, "close": 4010.0 + offset, "vol": 100000.0, "amount": 1000000.0}
                    for offset, raw_date in enumerate(("20200102", "20200103", "20200203"))
                ],
            )

    def _run(self) -> dict[str, object]:
        return materialize_a_share_research_data(
            repo_root=self.repo_root,
            cache_root=self.cache_root,
            start=self.start,
            end=self.end,
            as_of=self.as_of,
        )

    def _lock_path(self, as_of: str | None = None) -> Path:
        return (
            self.repo_root / "data" / "research" / "raw" / "a_share"
            / ".materialization_locks" / f"{as_of or self.as_of}.lock"
        )

    def _assert_lock_available_from_another_process(self, as_of: str | None = None) -> None:
        context = multiprocessing.get_context("fork")
        result = context.Queue()
        process = context.Process(
            target=_try_flock_worker,
            args=(str(self._lock_path(as_of)), result),
        )
        process.start()
        process.join(10)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 0)
        self.assertTrue(result.get(timeout=2))

    def _output_hashes(self) -> dict[str, str]:
        paths = [
            *sorted((self.repo_root / "data" / "shared" / "cache").glob("history_*.csv")),
            *sorted((self.repo_root / "data" / "research" / "raw" / "a_share" / self.as_of).glob("*")),
        ]
        return {
            str(path.relative_to(self.repo_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
            if path.is_file()
        }

    def test_materializes_point_in_time_calendar_history_and_raw_sources(self) -> None:
        summary = self._run()

        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["historical_union_count"], 3)
        self.assertEqual(summary["historical_union_codes"], ["000001", "000002", "000003"])

        history_path = next(
            (self.repo_root / "data" / "shared" / "cache").glob("history_000001_20200203_*.csv")
        )
        history = pd.read_csv(
            history_path,
            dtype={"code": str, "ts_code": str, "trade_date": str, "list_date": str, "delist_date": str},
        )
        self.assertIn("volume", history.columns)
        self.assertNotIn("vol", history.columns)
        self.assertEqual(history["code"].unique().tolist(), ["000001"])
        jan_2 = history.loc[history["trade_date"].eq("20200102")].iloc[0]
        jan_3 = history.loc[history["trade_date"].eq("20200103")].iloc[0]
        feb_3 = history.loc[history["trade_date"].eq("20200203")].iloc[0]
        self.assertEqual(jan_2["name"], "平安旧名")
        self.assertEqual(feb_3["name"], "平安新名")
        self.assertEqual(float(jan_2["amount_thousand_yuan"]), 10_000.0)
        self.assertEqual(float(jan_2["amount"]), 10_000_000.0)
        self.assertEqual(jan_2["amount_unit"], "yuan")
        self.assertEqual(int(jan_2["is_st"]), 1)
        self.assertTrue(pd.isna(jan_3["open"]))
        self.assertEqual(int(jan_3["is_suspended"]), 1)
        self.assertEqual(jan_3["suspension_status_source"], "baostock+tushare")

        conflict_path = next(
            (self.repo_root / "data" / "shared" / "cache").glob("history_000002_20200203_*.csv")
        )
        events = pd.read_csv(conflict_path, dtype={"trade_date": str})
        resume_row = events.loc[events["trade_date"].eq("20200102")].iloc[0]
        partial_row = events.loc[events["trade_date"].eq("20200203")].iloc[0]
        self.assertEqual(int(resume_row["tushare_resume_event"]), 1)
        self.assertEqual(int(resume_row["tushare_suspend_event"]), 0)
        self.assertEqual(int(resume_row["status_conflict"]), 0)
        self.assertEqual(int(resume_row["is_tradable"]), 1)
        self.assertEqual(int(partial_row["partial_suspension_event"]), 1)
        self.assertEqual(int(partial_row["status_conflict"]), 0)
        self.assertEqual(int(partial_row["is_tradable"]), 0)
        self.assertEqual(int(partial_row["is_suspended"]), 0)
        self.assertEqual(partial_row["security_status"], "partial_suspension_event")
        self.assertEqual(partial_row["tushare_suspend_timing"], "09:30-10:30")

        delisted_path = next(
            (self.repo_root / "data" / "shared" / "cache").glob("history_000003_20200203_*.csv")
        )
        delisted = pd.read_csv(
            delisted_path,
            dtype={"code": str, "trade_date": str, "list_date": str, "delist_date": str},
        )
        self.assertEqual(delisted["code"].unique().tolist(), ["000003"])
        self.assertEqual(delisted["trade_date"].tolist(), ["20200102", "20200103"])
        self.assertTrue(pd.isna(delisted.iloc[0]["delist_date"]))
        self.assertEqual(delisted.iloc[1]["delist_date"], "20200103")
        self.assertEqual(delisted.iloc[1]["security_status"], "delisted")

        raw_root = self.repo_root / "data" / "research" / "raw" / "a_share" / self.as_of
        financials = pd.read_parquet(raw_root / "fina_indicator.parquet")
        revisions = financials.loc[
            financials["ts_code"].eq("000001.SZ")
            & financials["end_date"].eq("20191231")
        ]
        self.assertEqual(revisions["ann_date"].tolist(), ["20200120", "20200202"])
        income = pd.read_parquet(raw_root / "income.parquet")
        income_revisions = income.loc[
            income["ts_code"].eq("000001.SZ")
            & income["end_date"].eq("20191231")
        ]
        self.assertEqual(income_revisions["ann_date"].tolist(), ["20200120", "20200202"])
        self.assertEqual(income_revisions["update_flag"].tolist(), ["0", "1"])
        self.assertEqual(len(pd.read_parquet(raw_root / "balancesheet.parquet")), 3)
        self.assertEqual(len(pd.read_parquet(raw_root / "cashflow.parquet")), 3)
        daily_basic = pd.read_parquet(raw_root / "daily_basic.parquet")
        self.assertIn("000001.SZ", daily_basic["ts_code"].astype(str).tolist())
        benchmark = pd.read_parquet(raw_root / "benchmark_000300.parquet")
        self.assertTrue({"open", "high", "low", "close"}.issubset(benchmark.columns))

        manifest = json.loads((raw_root / "materialization_manifest.json").read_text())
        self.assertEqual(manifest["schema_version"], "a-share-materialization-v1")
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["historical_union_codes"], ["000001", "000002", "000003"])
        self.assertEqual(manifest["historical_union_count"], 3)
        self.assertEqual(
            manifest["field_semantics"]["is_suspended"],
            "baostock_full_day_tradestatus_zero",
        )
        self.assertTrue(manifest["field_semantics"]["amount"].startswith("yuan"))
        self.assertTrue(manifest["field_semantics"]["history_prices"].startswith("raw_execution_prices"))
        self.assertIn("source_digest", manifest)
        self.assertIn("output_digest", manifest)
        self.assertEqual(
            manifest["endpoint_coverage"]["adj_factor"]["membership_point_in_time_coverage"],
            1.0,
        )
        self.assertEqual(
            manifest["endpoint_coverage"]["adj_factor"]["membership_warmup_point_in_time_coverage"],
            1.0,
        )

        snapshot = json.loads((raw_root / "snapshot_manifest.json").read_text())
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["status"], "complete")
        self.assertEqual(snapshot["as_of"], "20200203")
        self.assertEqual(snapshot["start"], "2020-01-02")
        self.assertEqual(snapshot["end"], "2020-02-03")
        self.assertEqual(snapshot["historical_union_codes"], ["000001", "000002", "000003"])
        self.assertEqual(snapshot["historical_union_count"], 3)
        self.assertEqual(snapshot["source_paths"], sorted(snapshot["source_paths"]))
        self.assertIn("daily/2020-01-02.csv", snapshot["source_paths"])
        self.assertEqual(snapshot["sources"], snapshot["source_paths"])
        self.assertEqual(set(snapshot["source_hashes"]), set(snapshot["source_paths"]))
        self.assertEqual(snapshot["endpoint_coverage"], manifest["endpoint_coverage"])
        self.assertEqual(snapshot["outputs"], manifest["outputs"])
        self.assertEqual(snapshot["output_digest"], manifest["output_digest"])
        for output in snapshot["outputs"].values():
            self.assertIn("rows", output)
            self.assertIn("min_date", output)
            self.assertIn("max_date", output)
            self.assertIn("sha256", output)

    def test_same_fixture_is_byte_for_byte_deterministic(self) -> None:
        first = self._run()
        first_hashes = self._output_hashes()

        second = self._run()
        second_hashes = self._output_hashes()

        self.assertEqual(first_hashes, second_hashes)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])

    def test_same_as_of_concurrent_writer_fails_without_touching_owner_artifacts(self) -> None:
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        release = context.Event()
        result = context.Queue()
        process = context.Process(
            target=_paused_materialization_worker,
            args=(
                str(self.repo_root),
                str(self.cache_root),
                self.start,
                self.end,
                self.as_of,
                ready,
                release,
                result,
            ),
        )
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            marker = (
                self.repo_root / "data" / "research" / "raw" / "a_share"
                / self.as_of / ".materialization_in_progress"
            )
            marker_before = marker.read_bytes()
            staging_before = sorted(
                path.name
                for path in (self.repo_root / "data" / "research").glob(
                    f".materialization-staging-{self.as_of}-*"
                )
            )
            started = time.monotonic()
            with self.assertRaisesRegex(
                ValueError, "a_share_materialization_locked"
            ):
                self._run()
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(marker.read_bytes(), marker_before)
            self.assertEqual(
                sorted(
                    path.name
                    for path in (self.repo_root / "data" / "research").glob(
                        f".materialization-staging-{self.as_of}-*"
                    )
                ),
                staging_before,
            )
        finally:
            release.set()
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(result.get(timeout=2), ("ok", "complete"))

    def test_different_as_of_writer_does_not_share_lock(self) -> None:
        context = multiprocessing.get_context("fork")
        ready = context.Event()
        release = context.Event()
        result = context.Queue()
        process = context.Process(
            target=_paused_materialization_worker,
            args=(
                str(self.repo_root),
                str(self.cache_root),
                self.start,
                self.end,
                self.as_of,
                ready,
                release,
                result,
            ),
        )
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            other = materialize_a_share_research_data(
                repo_root=self.repo_root,
                cache_root=self.cache_root,
                start=self.start,
                end=self.end,
                as_of="20200204",
            )
            self.assertEqual(other["status"], "complete")
        finally:
            release.set()
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(result.get(timeout=2), ("ok", "complete"))

    def test_completed_writer_releases_lock_and_stale_lock_file_is_reusable(self) -> None:
        self._run()

        self.assertTrue(self._lock_path().is_file())
        self._assert_lock_available_from_another_process()
        self.assertEqual(self._run()["status"], "complete")
        self._assert_lock_available_from_another_process()

    def test_materializer_streams_partitions_through_disk_sqlite_and_parquet(self) -> None:
        source = inspect.getsource(materializer)
        self.assertNotIn("pd.concat(", source)
        self.assertNotIn("status_parts", source)
        self.assertIn("sqlite3.connect", source)
        self.assertIn("ParquetWriter", source)

        original_read_csv = pd.read_csv
        with patch.object(
            materializer.pd,
            "read_csv",
            wraps=original_read_csv,
        ) as read_csv, patch.object(
            materializer.sqlite3,
            "connect",
            wraps=materializer.sqlite3.connect,
        ) as connect:
            self._run()

        sqlite_paths = [str(call.args[0]) for call in connect.call_args_list]
        self.assertTrue(sqlite_paths)
        self.assertNotIn(":memory:", sqlite_paths)
        partition_calls = [
            call
            for call in read_csv.call_args_list
            if call.args
            and Path(call.args[0]).parent.name in {"daily", "daily_basic"}
            and call.kwargs.get("usecols")
        ]
        self.assertTrue(partition_calls)
        self.assertTrue(all(isinstance(call.kwargs.get("dtype"), dict) for call in partition_calls))

    def test_future_records_do_not_modify_earlier_history(self) -> None:
        self._run()
        history_path = next(
            (self.repo_root / "data" / "shared" / "cache").glob("history_000001_20200203_*.csv")
        )
        before = pd.read_csv(history_path, dtype={"trade_date": str})
        earlier_before = before.loc[before["trade_date"].eq("20200102")].reset_index(drop=True)

        name_path = self.cache_root / "namechange" / "000001.SZ.csv"
        names = pd.read_csv(name_path, dtype=str)
        names.loc[len(names)] = {
            "ts_code": "000001.SZ",
            "name": "三月未来名",
            "start_date": "20200301",
            "end_date": "",
            "ann_date": "20200301",
            "change_reason": "未来更名",
        }
        names.to_csv(name_path, index=False)

        self._run()
        after_path = next(
            (self.repo_root / "data" / "shared" / "cache").glob("history_000001_20200203_*.csv")
        )
        after = pd.read_csv(after_path, dtype={"trade_date": str})
        earlier_after = after.loc[after["trade_date"].eq("20200102")].reset_index(drop=True)
        pd.testing.assert_frame_equal(earlier_before, earlier_after, check_dtype=False)

    def test_current_master_name_is_not_backfilled_when_point_in_time_name_is_missing(self) -> None:
        name_path = self.cache_root / "namechange" / "000002.SZ.csv"
        pd.DataFrame(
            columns=[
                "ts_code", "name", "start_date", "end_date", "ann_date",
                "change_reason",
            ]
        ).to_csv(name_path, index=False)

        self._run()
        history_path = next(
            (self.repo_root / "data" / "shared" / "cache").glob("history_000002_20200203_*.csv")
        )
        before = pd.read_csv(history_path, dtype={"trade_date": str})
        self.assertTrue(before["name"].isna().all())
        self.assertEqual(set(before["name_source"]), {"unavailable_point_in_time"})

        master_path = self.cache_root / "stock_basic.csv"
        master = pd.read_csv(master_path, dtype=str)
        master.loc[master["ts_code"].eq("000002.SZ"), "name"] = "未来再次更名"
        master.to_csv(master_path, index=False)
        self._run()
        after = pd.read_csv(history_path, dtype={"trade_date": str})
        self.assertTrue(after["name"].isna().all())

    def test_missing_baostock_day_keeps_nullable_state_and_fails_closed(self) -> None:
        status_path = self.cache_root / "baostock_status" / "000002.SZ.csv"
        status = pd.read_csv(status_path, dtype=str)
        status = status.loc[~status["trade_date"].eq("20200103")]
        status.to_csv(status_path, index=False)

        self._run()

        history_path = next(
            (self.repo_root / "data" / "shared" / "cache").glob(
                "history_000002_20200203_*.csv"
            )
        )
        history = pd.read_csv(history_path, dtype={"trade_date": str})
        row = history.loc[history["trade_date"].eq("20200103")].iloc[0]
        self.assertTrue(pd.isna(row["is_suspended"]))
        self.assertEqual(int(row["is_tradable"]), 0)
        self.assertEqual(row["security_status"], "status_unknown")

    def test_missing_or_invalid_required_source_fails_without_complete_manifest(self) -> None:
        (self.cache_root / "daily_basic" / "2020-01-03.csv").unlink()

        with self.assertRaisesRegex(ValueError, "materialization_source_missing"):
            self._run()

        manifest = (
            self.repo_root / "data" / "research" / "raw" / "a_share"
            / self.as_of / "materialization_manifest.json"
        )
        self.assertFalse(manifest.exists())

        self._write_fixture()
        broken_daily_basic = self.cache_root / "daily_basic" / "2020-01-02.csv"
        pd.read_csv(broken_daily_basic).drop(columns=["dv_ttm"]).to_csv(
            broken_daily_basic, index=False
        )
        with self.assertRaisesRegex(ValueError, "materialization_schema_invalid"):
            self._run()
        self.assertFalse(manifest.exists())

        self._write_fixture()
        broken_fina = self.cache_root / "fina_indicator" / "000001.SZ.csv"
        pd.read_csv(broken_fina).drop(columns=["roe"]).to_csv(broken_fina, index=False)
        with self.assertRaisesRegex(ValueError, "materialization_schema_invalid"):
            self._run()
        self.assertFalse(manifest.exists())

        self._write_fixture()
        broken = self.cache_root / "baostock_status" / "000001.SZ.csv"
        pd.read_csv(broken).drop(columns=["tradestatus"]).to_csv(broken, index=False)
        with self.assertRaisesRegex(ValueError, "materialization_schema_invalid"):
            self._run()
        self.assertFalse(manifest.exists())

    def test_incomplete_adjustment_history_fails_before_publish(self) -> None:
        path = self.cache_root / "adj_factor" / "000001.SZ.csv"
        frame = pd.read_csv(path, dtype={"trade_date": str})
        frame = frame.loc[~frame["trade_date"].eq("20200203")]
        frame.to_csv(path, index=False)

        with self.assertRaisesRegex(
            ValueError,
            "materialization_source_incomplete:adj_factor_coverage",
        ):
            self._run()

        manifest = (
            self.repo_root / "data" / "research" / "raw" / "a_share"
            / self.as_of / "materialization_manifest.json"
        )
        self.assertFalse(manifest.exists())

    def test_adjustment_gap_in_pre_membership_warmup_fails_before_publish(self) -> None:
        path = self.cache_root / "adj_factor" / "000002.SZ.csv"
        frame = pd.read_csv(path, dtype={"trade_date": str})
        frame = frame.loc[~frame["trade_date"].eq("20200103")]
        frame.to_csv(path, index=False)

        with self.assertRaisesRegex(
            ValueError,
            "materialization_source_incomplete:adj_factor_warmup_coverage",
        ):
            self._run()

    def test_wrong_or_empty_daily_partition_fails_closed(self) -> None:
        daily_path = self.cache_root / "daily" / "2020-01-02.csv"
        wrong_day = pd.read_csv(daily_path, dtype=str)
        wrong_day["trade_date"] = "20200103"
        wrong_day.to_csv(daily_path, index=False)

        with self.assertRaisesRegex(
            ValueError,
            "materialization_source_invalid:daily/2020-01-02.csv:trade_date",
        ):
            self._run()

        self._write_fixture()
        empty = pd.read_csv(daily_path, nrows=0)
        empty.to_csv(daily_path, index=False)
        with self.assertRaisesRegex(
            ValueError,
            "materialization_source_invalid:daily/2020-01-02.csv:empty",
        ):
            self._run()

    def test_wrong_daily_basic_and_suspend_partition_dates_fail_closed(self) -> None:
        for endpoint in ("daily_basic", "suspend_d"):
            with self.subTest(endpoint=endpoint):
                self._write_fixture()
                path = self.cache_root / endpoint / "2020-01-02.csv"
                frame = pd.read_csv(path, dtype=str)
                frame["trade_date"] = "20200103"
                frame.to_csv(path, index=False)
                with self.assertRaisesRegex(
                    ValueError,
                    rf"materialization_source_invalid:{endpoint}/2020-01-02.csv:trade_date",
                ):
                    self._run()

    def test_index_weight_identity_and_cutoff_fail_closed(self) -> None:
        path = self.cache_root / "index_weight" / "000300_2020-01.csv"
        mutations = (
            ("wrong_index", {"index_code": "000905.SH"}),
            ("bad_member", {"con_code": "ABC.SZ"}),
            ("future_snapshot", {"trade_date": "20200201"}),
        )
        for label, values in mutations:
            with self.subTest(label=label):
                self._write_fixture()
                frame = pd.read_csv(path, dtype=str)
                for column, value in values.items():
                    frame.loc[0, column] = value
                frame.to_csv(path, index=False)
                with self.assertRaisesRegex(
                    ValueError,
                    "materialization_source_invalid:index_weight/000300_2020-01.csv",
                ):
                    self._run()

    def test_b_share_members_are_filtered_without_per_code_sources(self) -> None:
        path = self.cache_root / "index_weight" / "000300_2020-01.csv"
        frame = pd.read_csv(path, dtype=str)
        b_members = pd.DataFrame(
            [
                {**frame.iloc[0].to_dict(), "con_code": "200001.SZ"},
                {**frame.iloc[0].to_dict(), "con_code": "900901.SH"},
            ]
        )
        pd.concat([frame, b_members], ignore_index=True).to_csv(path, index=False)

        summary = self._run()

        self.assertEqual(summary["status"], "complete")
        self.assertEqual(
            summary["historical_union_codes"], ["000001", "000002", "000003"]
        )
        raw_index = pd.read_parquet(
            self.repo_root / "data" / "research" / "raw" / "a_share"
            / self.as_of / "index_weight.parquet"
        )
        self.assertFalse(raw_index["con_code"].isin(["200001.SZ", "900901.SH"]).any())
        self.assertFalse((self.cache_root / "fina_indicator" / "200001.SZ.csv").exists())
        self.assertFalse((self.cache_root / "fina_indicator" / "900901.SH.csv").exists())

    def test_per_code_and_benchmark_content_identity_fail_closed(self) -> None:
        cases = (
            ("fina_indicator", "000001.SZ.csv"),
            ("adj_factor", "000001.SZ.csv"),
            ("namechange", "000001.SZ.csv"),
            ("baostock_status", "000001.SZ.csv"),
        )
        for endpoint, filename in cases:
            with self.subTest(endpoint=endpoint):
                self._write_fixture()
                path = self.cache_root / endpoint / filename
                frame = pd.read_csv(path, dtype=str)
                frame.loc[0, "ts_code"] = "000002.SZ"
                frame.to_csv(path, index=False)
                with self.assertRaisesRegex(
                    ValueError,
                    rf"materialization_source_invalid:{endpoint}/{filename}:ts_code",
                ):
                    self._run()

        self._write_fixture()
        benchmark = self.cache_root / "benchmark_daily" / "000300.csv"
        frame = pd.read_csv(benchmark, dtype=str)
        frame.loc[0, "ts_code"] = "000905.SH"
        frame.to_csv(benchmark, index=False)
        with self.assertRaisesRegex(
            ValueError,
            "materialization_source_invalid:benchmark_daily/000300.csv:ts_code",
        ):
            self._run()

    def test_success_removes_only_same_as_of_history_outside_union(self) -> None:
        history_root = self.repo_root / "data" / "shared" / "cache"
        history_root.mkdir(parents=True, exist_ok=True)
        stale_same_run = history_root / "history_999999_20200203_1.csv"
        other_run = history_root / "history_888888_20200103_1.csv"
        stale_same_run.write_text("code,trade_date\n999999,20200203\n")
        other_run.write_text("code,trade_date\n888888,20200103\n")

        self._run()

        same_run_codes = sorted(
            path.name.split("_")[1]
            for path in history_root.glob("history_*_20200203_*.csv")
        )
        self.assertEqual(same_run_codes, ["000001", "000002", "000003"])
        self.assertFalse(stale_same_run.exists())
        self.assertTrue(other_run.exists())

    def test_as_of_requires_exact_valid_calendar_date(self) -> None:
        for invalid in ("20200203junk", "20200230", "2020-2-03", "2020/02/03"):
            with self.subTest(as_of=invalid), self.assertRaisesRegex(
                ValueError, "materialization_as_of_invalid"
            ):
                materialize_a_share_research_data(
                    repo_root=self.repo_root,
                    cache_root=self.cache_root,
                    start=self.start,
                    end=self.end,
                    as_of=invalid,
                )

    def test_full_day_status_conflict_is_fail_closed(self) -> None:
        suspend = self.cache_root / "suspend_d" / "2020-02-03.csv"
        frame = pd.read_csv(suspend, dtype=str)
        frame.loc[frame["ts_code"].eq("000002.SZ"), "suspend_timing"] = "09:30-15:00"
        frame.to_csv(suspend, index=False)

        self._run()

        history_path = next(
            (self.repo_root / "data" / "shared" / "cache").glob(
                "history_000002_20200203_*.csv"
            )
        )
        history = pd.read_csv(history_path, dtype={"trade_date": str})
        row = history.loc[history["trade_date"].eq("20200203")].iloc[0]
        self.assertEqual(int(row["status_conflict"]), 1)
        self.assertEqual(int(row["is_tradable"]), 0)
        self.assertEqual(row["security_status"], "status_conflict")

    def test_failed_validation_preserves_previous_committed_version(self) -> None:
        self._run()
        raw_root = (
            self.repo_root / "data" / "research" / "raw" / "a_share" / self.as_of
        )
        self.assertTrue((raw_root / "materialization_manifest.json").exists())
        self.assertTrue((raw_root / "snapshot_manifest.json").exists())
        before = self._output_hashes()
        (self.cache_root / "daily_basic" / "2020-01-03.csv").unlink()

        with self.assertRaisesRegex(ValueError, "materialization_source_missing"):
            self._run()

        self.assertEqual(self._output_hashes(), before)
        self.assertTrue((raw_root / "materialization_manifest.json").exists())
        self.assertTrue((raw_root / "snapshot_manifest.json").exists())
        self.assertFalse((raw_root / ".materialization_in_progress").exists())
        self._assert_lock_available_from_another_process()

    def test_source_drift_aborts_before_publish_and_preserves_previous_version(self) -> None:
        self._run()
        before = self._output_hashes()
        original = materializer._build_history
        drifted = False

        def mutate_source_once(*args, **kwargs):
            nonlocal drifted
            if not drifted:
                drifted = True
                path = self.cache_root / "daily" / "2020-01-02.csv"
                frame = pd.read_csv(path)
                frame.loc[0, "close"] = float(frame.loc[0, "close"]) + 0.01
                frame.to_csv(path, index=False)
            return original(*args, **kwargs)

        with patch.object(
            materializer,
            "_build_history",
            side_effect=mutate_source_once,
        ), self.assertRaisesRegex(ValueError, "materialization_source_changed"):
            self._run()

        self.assertEqual(self._output_hashes(), before)

    def test_marker_exists_during_build_and_is_removed_after_commit(self) -> None:
        marker = (
            self.repo_root / "data" / "research" / "raw" / "a_share"
            / self.as_of / ".materialization_in_progress"
        )
        observed: list[bool] = []
        marker_payloads: list[str] = []
        original = materializer._build_history

        def observe_marker(*args, **kwargs):
            observed.append(marker.exists())
            marker_payloads.append(marker.read_text(encoding="utf-8"))
            return original(*args, **kwargs)

        with patch.object(materializer, "_build_history", side_effect=observe_marker):
            self._run()

        self.assertTrue(observed)
        self.assertTrue(all(observed))
        self.assertTrue(all('"owner"' in payload for payload in marker_payloads))
        self.assertTrue(all('"generation"' in payload for payload in marker_payloads))
        self.assertFalse(marker.exists())

    def test_publish_failure_rolls_back_previous_version(self) -> None:
        self._run()
        before = self._output_hashes()
        raw_root = (
            self.repo_root / "data" / "research" / "raw" / "a_share" / self.as_of
        )
        original_replace = os.replace
        failed = False

        def fail_raw_publish(source, destination):
            nonlocal failed
            if (
                not failed
                and Path(destination) == raw_root
                and ".materialization-staging" in str(source)
            ):
                failed = True
                raise OSError("simulated publish failure")
            return original_replace(source, destination)

        with patch.object(
            materializer.os,
            "replace",
            side_effect=fail_raw_publish,
        ), self.assertRaisesRegex(OSError, "simulated publish failure"):
            self._run()

        self.assertTrue(failed)
        self.assertEqual(self._output_hashes(), before)
        self.assertFalse((raw_root / ".materialization_in_progress").exists())
        self._assert_lock_available_from_another_process()

    def test_empty_suspend_parquet_uses_same_fixed_arrow_schema(self) -> None:
        self._run()
        suspend_path = (
            self.repo_root / "data" / "research" / "raw" / "a_share"
            / self.as_of / "suspend_d.parquet"
        )
        populated_schema = pq.read_schema(suspend_path)

        self._write_fixture()
        columns = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
        for path in (self.cache_root / "suspend_d").glob("*.csv"):
            pd.DataFrame(columns=columns).to_csv(path, index=False)
        self._run()

        self.assertEqual(pq.read_schema(suspend_path), populated_schema)
        self.assertEqual(pd.read_parquet(suspend_path).columns.tolist(), columns)

    def test_manifest_write_failure_leaves_neither_completion_manifest(self) -> None:
        original = materializer.write_text_atomic

        def fail_materialization_manifest(path: Path, text: str, *args, **kwargs) -> None:
            if Path(path).name == "materialization_manifest.json":
                raise OSError("simulated manifest write failure")
            original(path, text, *args, **kwargs)

        with patch.object(
            materializer,
            "write_text_atomic",
            side_effect=fail_materialization_manifest,
        ), self.assertRaisesRegex(OSError, "simulated manifest write failure"):
            self._run()

        raw_root = (
            self.repo_root / "data" / "research" / "raw" / "a_share" / self.as_of
        )
        self.assertFalse((raw_root / "materialization_manifest.json").exists())
        self.assertFalse((raw_root / "snapshot_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
