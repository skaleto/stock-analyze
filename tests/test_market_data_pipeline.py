from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.data_provider import AkshareProvider, CacheMiss
from stock_analyze.market_data import (
    _merged_filters,
    prepare_market_data,
)


class FakeProvider(AkshareProvider):
    """In-memory AkshareProvider for tests.

    Replaces every network-touching method with a pre-canned response. The
    base class's cache logic still runs because we do not override
    ``save_cache`` / ``load_cache``.
    """

    def __init__(self, cache_dir: Path | None, *, as_of: str = "2026-05-22", failures: set[str] | None = None) -> None:
        super().__init__(cache_dir=cache_dir, offline=False, as_of=as_of)
        self._failures = failures or set()
        self._mock_spot = pd.DataFrame(
            [
                {"code": "600519", "name": "贵州茅台", "latest_price": 1620.0, "pe": 32.5, "pb": 8.1, "market_cap_yi": 20000},
                {"code": "000001", "name": "平安银行", "latest_price": 12.5, "pe": 5.6, "pb": 0.7, "market_cap_yi": 2400},
                {"code": "000651", "name": "格力电器", "latest_price": 35.0, "pe": 7.8, "pb": 1.9, "market_cap_yi": 1900},
            ]
        )

    def spot(self) -> pd.DataFrame:
        if "spot" in self._failures:
            return pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])
        return self._mock_spot.copy()

    def index_constituents(self, scope: str) -> pd.DataFrame:
        if f"constituents_{scope}" in self._failures:
            return pd.DataFrame(columns=["code", "name"])
        return pd.DataFrame([{"code": "600519", "name": "贵州茅台"}, {"code": "000001", "name": "平安银行"}])

    def trading_calendar(self) -> list[str]:
        return ["2026-05-20", "2026-05-21", "2026-05-22", "2026-05-25", "2026-05-26"]

    def basic_info(self, code: str) -> dict:
        if f"basic_{code}" in self._failures:
            raise RuntimeError("basic_info network down")
        return {"code": code, "name": "T", "industry": "酿酒", "listing_date": "2001-01-01"}

    def price_history(self, code: str, as_of: str | None = None, days: int = 180) -> pd.DataFrame:
        return pd.DataFrame([{"日期": "2026-05-22", "收盘": 100.0, "开盘": 99.0, "最高": 101.0, "最低": 98.5, "成交额": 1e8}])

    def valuation_metrics(self, code: str) -> dict:
        return {"pe": 15.0, "pb": 1.5}

    def financial_metrics(self, code: str) -> dict:
        return {"roe": 12.0, "gross_margin": 25.0, "debt_ratio": 40.0, "net_profit_growth": 8.0}

    def dividend_yield(self, code: str, as_of: str | None = None) -> float | None:
        return 2.0

    def benchmark_close(self, benchmark_code: str, as_of: str | None = None) -> tuple[float | None, str | None]:
        if f"benchmark_{benchmark_code}" in self._failures:
            return None, None
        return 3850.0, "2026-05-22"


class CacheMissBehaviorTests(unittest.TestCase):
    def test_offline_raises_cache_miss_for_uncached_method(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = AkshareProvider(cache_dir=tmp, offline=True, as_of="2026-05-22")
            with self.assertRaises(CacheMiss) as ctx:
                provider.benchmark_close("000300", as_of="2026-05-22")
        self.assertEqual(ctx.exception.method, "benchmark_close")
        self.assertEqual(ctx.exception.cache_name, "benchmark_000300_20260522")

    def test_offline_reads_cache_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Pre-seed cache as prepare-market-data would
            pd.DataFrame([
                {"benchmark_code": "000300", "close": 3500.0, "trade_date": "2026-05-22"}
            ]).to_csv(Path(tmp) / "benchmark_000300_20260522.csv", index=False, encoding="utf-8-sig")
            provider = AkshareProvider(cache_dir=tmp, offline=True, as_of="2026-05-22")
            close, trade_date = provider.benchmark_close("000300", as_of="2026-05-22")
        self.assertEqual(close, 3500.0)
        self.assertEqual(trade_date, "2026-05-22")

    def test_offline_does_not_emit_http_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = AkshareProvider(cache_dir=tmp, offline=True, as_of="2026-05-22")
            with self.assertRaises(CacheMiss):
                provider.spot()
        # Health log records cache_miss instead of any 'failed' network attempt
        miss_records = [h for h in provider.health if h.get("status") == "cache_miss"]
        self.assertTrue(miss_records, "should record cache_miss in health log")
        self.assertFalse([h for h in provider.health if h.get("source", "").startswith("spot_eastmoney")])


class WeekendCacheResolutionTests(unittest.TestCase):
    def test_offline_provider_auto_resolves_to_latest_cache_date(self) -> None:
        """Saturday agent runs (no as_of given) read Friday's cache files."""

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            # Use past dates so the "must be <= today" filter accepts them.
            # Simulate prepare-market-data writing Friday's snapshot, then a
            # Saturday agent run picking it up automatically.
            pd.DataFrame([{"code": "600519", "name": "T", "latest_price": 100.0, "pe": 10, "pb": 1, "market_cap_yi": 100}]).to_csv(cache / "spot_20260515.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame([{"benchmark_code": "000300", "close": 3850.0, "trade_date": "2026-05-15"}]).to_csv(cache / "benchmark_000300_20260515.csv", index=False, encoding="utf-8-sig")

            provider = AkshareProvider(cache_dir=cache, offline=True, as_of=None)
            self.assertEqual(provider._date_stamp(), "20260515")

            close, trade_date = provider.benchmark_close("000300")
            self.assertEqual(close, 3850.0)
            self.assertEqual(trade_date, "2026-05-15")

    def test_explicit_as_of_overrides_auto_detect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            pd.DataFrame([{"code": "600519"}]).to_csv(cache / "spot_20260515.csv", index=False, encoding="utf-8-sig")

            provider = AkshareProvider(cache_dir=cache, offline=True, as_of="2026-05-10")
            self.assertEqual(provider._date_stamp(), "20260510")

    def test_auto_detect_skips_future_dates(self) -> None:
        """Past cache files should be picked over future-dated ones."""

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            pd.DataFrame([{"code": "600519"}]).to_csv(cache / "spot_20260515.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame([{"code": "600519"}]).to_csv(cache / "spot_20300101.csv", index=False, encoding="utf-8-sig")

            provider = AkshareProvider(cache_dir=cache, offline=True, as_of=None)
            self.assertEqual(provider._date_stamp(), "20260515")


class MergedFiltersTests(unittest.TestCase):
    def test_takes_max_max_fetch_and_min_other(self) -> None:
        agents = [
            {"filters": {"max_fetch_candidates": 200, "min_pe": 0.0, "min_avg_amount_20": 50_000_000}},
            {"filters": {"max_fetch_candidates": 300, "min_pe": 5.0, "min_avg_amount_20": 80_000_000}},
        ]
        merged = _merged_filters(agents)
        self.assertEqual(merged["max_fetch_candidates"], 300)
        self.assertEqual(merged["min_pe"], 0.0)
        self.assertEqual(merged["min_avg_amount_20"], 50_000_000)


class PrepareMarketDataTests(unittest.TestCase):
    def _patch_provider(self, fake: FakeProvider) -> None:
        from stock_analyze import market_data

        self._original = market_data.make_provider
        market_data.make_provider = lambda **kwargs: fake  # type: ignore[assignment]
        self.addCleanup(self._restore, market_data)

    def _restore(self, module) -> None:  # type: ignore[no-untyped-def]
        module.make_provider = self._original

    def _scaffold(self, root: Path) -> None:
        configs = root / "configs"
        agents_dir = configs / "agents"
        agents_dir.mkdir(parents=True)
        (configs / "competition.yaml").write_text(json.dumps({
            "competition_id": "test",
            "start_date": "2026-05-26",
            "initial_cash": 1_000_000,
            "accounts": [
                {"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500_000, "top_n": 5},
            ],
            "schedule": {"rebalance": "weekly_after_close", "signal_day": "last_trading_day_of_week", "execution": "next_trading_day_open"},
            "trading": {"lot_size": 100, "commission_rate": 0.0003, "min_commission": 5, "stamp_tax_rate": 0.0005, "slippage_rate": 0.0005, "max_single_weight": 0.05},
        }))
        (agents_dir / "claude.yaml").write_text(json.dumps({
            "agent_id": "claude",
            "filters": {"max_fetch_candidates": 50, "min_pe": 0, "min_avg_amount_20": 50_000_000},
        }))

    def test_writes_snapshot_with_success_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold(root)
            cache_dir = root / "data" / "shared" / "cache"
            cache_dir.mkdir(parents=True)
            fake = FakeProvider(cache_dir=cache_dir)
            self._patch_provider(fake)

            snapshot = prepare_market_data(as_of="2026-05-22", repo_root=root, max_workers=1)

            self.assertEqual(snapshot["status"], "success")
            self.assertEqual(snapshot["as_of"], "2026-05-22")
            self.assertGreater(snapshot["candidates_fetched"], 0)
            self.assertEqual(snapshot["errors"], [])
            self.assertEqual(snapshot["rows"]["benchmark_000300"], 1)
            snapshot_path = root / "data" / "shared" / "market_snapshot_2026-05-22.json"
            self.assertTrue(snapshot_path.exists())

    def test_status_failed_when_spot_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold(root)
            cache_dir = root / "data" / "shared" / "cache"
            cache_dir.mkdir(parents=True)
            fake = FakeProvider(cache_dir=cache_dir, failures={"spot"})
            self._patch_provider(fake)

            snapshot = prepare_market_data(as_of="2026-05-22", repo_root=root, max_workers=1)

        self.assertEqual(snapshot["status"], "failed")
        self.assertIn("spot", snapshot["fetch_summary"]["fatal"])

    def test_partial_when_one_stock_basic_info_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold(root)
            cache_dir = root / "data" / "shared" / "cache"
            cache_dir.mkdir(parents=True)
            fake = FakeProvider(cache_dir=cache_dir, failures={"basic_600519"})
            self._patch_provider(fake)

            snapshot = prepare_market_data(as_of="2026-05-22", repo_root=root, max_workers=1)

        self.assertEqual(snapshot["status"], "partial")
        self.assertTrue(any(err.get("method") == "basic_info" for err in snapshot["errors"]))

    def test_does_not_refetch_when_snapshot_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold(root)
            cache_dir = root / "data" / "shared" / "cache"
            cache_dir.mkdir(parents=True)
            fake = FakeProvider(cache_dir=cache_dir)
            self._patch_provider(fake)

            first = prepare_market_data(as_of="2026-05-22", repo_root=root, max_workers=1)
            second = prepare_market_data(as_of="2026-05-22", repo_root=root, max_workers=1)

        self.assertEqual(first["status"], "success")
        self.assertEqual(second.get("skipped"), "snapshot_exists")

    def test_force_invalidates_stale_universe_caches(self) -> None:
        # Reproduces the 2026-05-24 bootstrap bug: a stale spot/stock_basic/
        # constituents/trading_calendar CSV on disk caused TushareProvider's
        # load_cache short-circuit to re-serve the bad data even with --force.
        # The fix deletes those CSVs at the top of prepare_market_data when
        # force=True, so the provider falls through to a real fetch.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold(root)
            cache_dir = root / "data" / "shared" / "cache"
            cache_dir.mkdir(parents=True)

            stale_marker = pd.DataFrame([{"STALE_MARKER": 1}])
            stale_files = [
                cache_dir / "spot_20260522.csv",
                cache_dir / "stock_basic_20260522.csv",
                cache_dir / "constituents_000300.SH_20260522.csv",
                cache_dir / "trading_calendar.csv",
            ]
            for path in stale_files:
                stale_marker.to_csv(path, index=False, encoding="utf-8-sig")
                self.assertTrue(path.exists())  # sanity-check the seed

            # Untouched-by-fix caches: per-stock files must NOT get nuked, since
            # --force is scoped to universe-level staleness. Use yesterday's stamp
            # so the file isn't relevant to this run but proves the glob doesn't
            # over-match.
            untouched = cache_dir / "basic_600519_20260521.csv"
            stale_marker.to_csv(untouched, index=False, encoding="utf-8-sig")

            fake = FakeProvider(cache_dir=cache_dir)
            self._patch_provider(fake)

            snapshot = prepare_market_data(
                as_of="2026-05-22", repo_root=root, force=True, max_workers=1
            )

            self.assertEqual(snapshot["status"], "success")
            # FakeProvider does not save_cache, so any file deleted by the fix
            # stays gone; if the fix is absent the file still has STALE_MARKER.
            for path in stale_files:
                self.assertFalse(
                    path.exists(),
                    f"--force should have deleted stale cache {path.name}",
                )
            self.assertTrue(untouched.exists(), "per-stock caches must not be deleted")


if __name__ == "__main__":
    unittest.main()
