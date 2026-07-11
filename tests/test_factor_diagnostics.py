from __future__ import annotations

import tempfile
import unittest

import pandas as pd

from stock_analyze.markets.a_share.data_provider import CacheMiss
from stock_analyze.markets.a_share.diagnostics import compute_pending_forward_ic
from stock_analyze.store import PortfolioStore


class StaticHistoryProvider:
    """Minimal provider returning a canned per-code price history."""

    def __init__(self, history_by_code: dict[str, pd.DataFrame]) -> None:
        self.history_by_code = history_by_code

    def price_history(self, code: str, as_of: str | None = None, days: int = 260) -> pd.DataFrame:
        return self.history_by_code.get(code, pd.DataFrame()).copy()


class PartiallyMissingHistoryProvider(StaticHistoryProvider):
    def __init__(self, history_by_code: dict[str, pd.DataFrame], missing_codes: set[str]) -> None:
        super().__init__(history_by_code)
        self.missing_codes = missing_codes

    def price_history(self, code: str, as_of: str | None = None, days: int = 260) -> pd.DataFrame:
        if code in self.missing_codes:
            raise CacheMiss(method="price_history", cache_name=f"history_{code}_missing")
        return super().price_history(code, as_of=as_of, days=days)


def make_history(code: str, daily_return: float, days: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=days, freq="B").strftime("%Y-%m-%d")
    closes = [10.0]
    for _ in range(1, days):
        closes.append(closes[-1] * (1 + daily_return))
    return pd.DataFrame({"日期": dates, "收盘": closes})


class ForwardIcTests(unittest.TestCase):
    def test_perfect_positive_correlation_returns_ic_close_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            snapshot = pd.DataFrame(
                [
                    {"code": "000001", "signal_date": "2026-04-01", "account_id": "acc",
                     "factor": "pe", "zscore": 1.0},
                    {"code": "000002", "signal_date": "2026-04-01", "account_id": "acc",
                     "factor": "pe", "zscore": 0.5},
                    {"code": "000003", "signal_date": "2026-04-01", "account_id": "acc",
                     "factor": "pe", "zscore": 0.0},
                    {"code": "000004", "signal_date": "2026-04-01", "account_id": "acc",
                     "factor": "pe", "zscore": -0.5},
                    {"code": "000005", "signal_date": "2026-04-01", "account_id": "acc",
                     "factor": "pe", "zscore": -1.0},
                ]
            )
            store.write_factor_snapshot(snapshot, "test_run")
            provider = StaticHistoryProvider(
                {
                    "000001": make_history("000001", 0.020),
                    "000002": make_history("000002", 0.015),
                    "000003": make_history("000003", 0.000),
                    "000004": make_history("000004", -0.015),
                    "000005": make_history("000005", -0.020),
                }
            )
            new_rows = compute_pending_forward_ic({}, store, provider, as_of="2026-05-01")
            ok_rows = [row for row in new_rows if row["ic_status"] == "ok"]
            self.assertEqual(len(ok_rows), 1)
            self.assertGreaterEqual(float(ok_rows[0]["ic"]), 0.95)

    def test_cache_miss_is_treated_as_missing_forward_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            snapshot = pd.DataFrame(
                [
                    {"code": "000001", "signal_date": "2026-04-01", "account_id": "acc",
                     "factor": "pe", "zscore": 1.0},
                    {"code": "000002", "signal_date": "2026-04-01", "account_id": "acc",
                     "factor": "pe", "zscore": 0.5},
                    {"code": "000003", "signal_date": "2026-04-01", "account_id": "acc",
                     "factor": "pe", "zscore": 0.0},
                    {"code": "300782", "signal_date": "2026-04-01", "account_id": "acc",
                     "factor": "pe", "zscore": -0.5},
                ]
            )
            store.write_factor_snapshot(snapshot, "test_run_missing")
            provider = PartiallyMissingHistoryProvider(
                {
                    "000001": make_history("000001", 0.020),
                    "000002": make_history("000002", 0.015),
                    "000003": make_history("000003", 0.000),
                },
                {"300782"},
            )
            new_rows = compute_pending_forward_ic({}, store, provider, as_of="2026-05-01")

            ok_rows = [row for row in new_rows if row["ic_status"] == "ok"]
            self.assertEqual(len(ok_rows), 1)
            self.assertEqual(ok_rows[0]["sample_size"], 3)

    def test_insufficient_history_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            snapshot = pd.DataFrame(
                [
                    {"code": "000001", "signal_date": "2026-04-30", "account_id": "acc",
                     "factor": "pe", "zscore": 1.0},
                ]
            )
            store.write_factor_snapshot(snapshot, "test_run_recent")
            provider = StaticHistoryProvider({"000001": make_history("000001", 0.01, days=5)})
            new_rows = compute_pending_forward_ic({}, store, provider, as_of="2026-05-01")
            self.assertEqual(len(new_rows), 1)
            self.assertEqual(new_rows[0]["ic_status"], "insufficient_history")

    def test_coverage_log_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            store.append_factor_coverage(
                [
                    {"signal_date": "2026-04-25", "account_id": "acc", "factor": "pe",
                     "coverage_pct": 0.40, "missing_count": 6, "mean": 1.0, "p5": 0.5, "p50": 1.0, "p95": 2.0, "std": 0.1}
                ]
            )
            store.append_factor_coverage(
                [
                    {"signal_date": "2026-05-02", "account_id": "acc", "factor": "pe",
                     "coverage_pct": 0.92, "missing_count": 1, "mean": 1.0, "p5": 0.5, "p50": 1.0, "p95": 2.0, "std": 0.1}
                ]
            )
            coverage = store.read_factor_coverage()
            self.assertEqual(len(coverage), 2)
            self.assertEqual(set(coverage["signal_date"]), {"2026-04-25", "2026-05-02"})


if __name__ == "__main__":
    unittest.main()
