from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import pandas as pd

from stock_analyze.research.permanent_portfolio.data import (
    build_total_return_frame,
    load_market_publication,
    materialize_market_data,
    validate_market_frame,
    write_partitioned_market_publication,
    write_market_publication,
)


def _valid_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["510300.SH"],
            "trade_date": ["20180102"],
            "open": [4.0],
            "high": [4.1],
            "low": [3.9],
            "close": [4.0],
            "vol": [1000.0],
            "amount": [4000.0],
            "adj_factor": [1.0],
            "adjusted_close": [4.0],
            "distribution_cash_per_share": [0.0],
            "distribution_reference_error": [0.0],
            "is_open": [True],
        }
    )


class _FixtureProvider:
    def fund_daily(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del start_date, end_date
        return pd.DataFrame(
            {
                "ts_code": [ts_code],
                "trade_date": ["20180102"],
                "open": [4.0],
                "high": [4.1],
                "low": [3.9],
                "close": [4.0],
                "vol": [1000.0],
                "amount": [4000.0],
            }
        )

    def fund_adj(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del start_date, end_date
        return pd.DataFrame(
            {
                "ts_code": [ts_code],
                "trade_date": ["20180102"],
                "adj_factor": [1.0],
            }
        )

    def trade_cal(
        self,
        *,
        exchange: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del exchange, start_date, end_date
        return pd.DataFrame(
            {
                "cal_date": ["20180102"],
                "is_open": [1],
            }
        )


class _MissingOpenDateProvider(_FixtureProvider):
    def trade_cal(
        self,
        *,
        exchange: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del exchange, start_date, end_date
        return pd.DataFrame(
            {
                "cal_date": ["20180102", "20180103"],
                "is_open": [1, 1],
            }
        )


class _SuspendedProvider(_MissingOpenDateProvider):
    def suspend_d(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del start_date, end_date
        return pd.DataFrame(
            {
                "ts_code": [ts_code],
                "trade_date": ["20180103"],
                "suspend_timing": [""],
                "suspend_type": ["S"],
            }
        )


class _ZeroTradeGapProvider(_MissingOpenDateProvider):
    def fund_daily(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del start_date, end_date
        return pd.DataFrame(
            {
                "ts_code": [ts_code, ts_code],
                "trade_date": ["20180102", "20180104"],
                "pre_close": [3.9, 4.0],
                "open": [4.0, 4.2],
                "high": [4.1, 4.3],
                "low": [3.9, 4.1],
                "close": [4.0, 4.2],
                "vol": [1000.0, 1200.0],
                "amount": [4000.0, 5040.0],
            }
        )

    def fund_adj(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del start_date, end_date
        return pd.DataFrame(
            {
                "ts_code": [ts_code, ts_code],
                "trade_date": ["20180102", "20180104"],
                "adj_factor": [1.0, 1.0],
            }
        )

    def trade_cal(
        self,
        *,
        exchange: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del exchange, start_date, end_date
        return pd.DataFrame(
            {
                "cal_date": ["20180102", "20180103", "20180104"],
                "is_open": [1, 1, 1],
            }
        )


class _DescendingMidListingProvider(_FixtureProvider):
    def fund_daily(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del start_date, end_date
        return pd.DataFrame(
            {
                "ts_code": [ts_code, ts_code],
                "trade_date": ["20180104", "20180103"],
                "pre_close": [10.0, 9.9],
                "open": [10.0, 10.0],
                "high": [10.1, 10.1],
                "low": [9.9, 9.9],
                "close": [10.0, 10.0],
                "vol": [1000.0, 1000.0],
                "amount": [10000.0, 10000.0],
            }
        )

    def fund_adj(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del start_date, end_date
        return pd.DataFrame(
            {
                "ts_code": [ts_code, ts_code],
                "trade_date": ["20180104", "20180103"],
                "adj_factor": [1.0, 1.0],
            }
        )

    def trade_cal(
        self,
        *,
        exchange: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        del exchange, start_date, end_date
        return pd.DataFrame(
            {
                "cal_date": ["20180104", "20180103", "20180102"],
                "is_open": [1, 1, 1],
            }
        )

class PermanentPortfolioDataTests(unittest.TestCase):
    def test_total_return_uses_adjustment_factor_but_keeps_raw_open(self) -> None:
        daily = pd.DataFrame(
            {
                "ts_code": ["510300.SH", "510300.SH"],
                "trade_date": ["20180102", "20180103"],
                "open": [4.0, 3.9],
                "high": [4.1, 4.0],
                "low": [3.9, 3.8],
                "close": [4.0, 3.9],
                "vol": [1000.0, 1200.0],
                "amount": [4000.0, 4680.0],
            }
        )
        adjustment = pd.DataFrame(
            {
                "ts_code": ["510300.SH", "510300.SH"],
                "trade_date": ["20180102", "20180103"],
                "adj_factor": [1.0, 1.1],
            }
        )

        result = build_total_return_frame(daily, adjustment)

        self.assertEqual(result["open"].tolist(), [4.0, 3.9])
        self.assertAlmostEqual(result.iloc[1]["adjusted_close"], 4.29)

    def test_total_return_infers_auditable_cash_distribution(self) -> None:
        daily = pd.DataFrame(
            {
                "ts_code": ["510300.SH", "510300.SH"],
                "trade_date": ["20180102", "20180103"],
                "pre_close": [10.0, 9.0],
                "open": [10.0, 9.0],
                "high": [10.0, 9.0],
                "low": [10.0, 9.0],
                "close": [10.0, 9.0],
                "vol": [1000.0, 1000.0],
                "amount": [10000.0, 9000.0],
            }
        )
        adjustment = pd.DataFrame(
            {
                "ts_code": ["510300.SH", "510300.SH"],
                "trade_date": ["20180102", "20180103"],
                "adj_factor": [1.0, 10.0 / 9.0],
            }
        )

        result = build_total_return_frame(daily, adjustment)

        self.assertIn("distribution_cash_per_share", result.columns)
        self.assertEqual(result.iloc[0]["distribution_cash_per_share"], 0.0)
        self.assertAlmostEqual(
            result.iloc[1]["distribution_cash_per_share"],
            1.0,
        )
        self.assertLessEqual(
            result.iloc[1]["distribution_reference_error"],
            0.005,
        )

    def test_unexplained_factor_jump_fails_closed(self) -> None:
        daily = pd.DataFrame(
            {
                "ts_code": ["510300.SH", "510300.SH"],
                "trade_date": ["20180102", "20180103"],
                "pre_close": [10.0, 8.0],
                "open": [10.0, 9.0],
                "high": [10.0, 9.0],
                "low": [10.0, 9.0],
                "close": [10.0, 9.0],
                "vol": [1000.0, 1000.0],
                "amount": [10000.0, 9000.0],
            }
        )
        adjustment = pd.DataFrame(
            {
                "ts_code": ["510300.SH", "510300.SH"],
                "trade_date": ["20180102", "20180103"],
                "adj_factor": [1.0, 10.0 / 9.0],
            }
        )

        with self.assertRaisesRegex(ValueError, "distribution_reference"):
            build_total_return_frame(daily, adjustment)

    def test_materializer_does_not_backfill_before_first_listing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = materialize_market_data(
                provider=_DescendingMidListingProvider(),
                codes=("511260.SH",),
                source_start="20180102",
                end_date="20180104",
                output_root=root,
            )
            frame, _ = load_market_publication(
                root / manifest["publication_id"]
            )

            self.assertEqual(frame["trade_date"].tolist(), ["20180103", "20180104"])
            self.assertTrue(frame["is_open"].all())

    def test_duplicate_code_date_fails_closed(self) -> None:
        frame = pd.concat([_valid_frame(), _valid_frame()], ignore_index=True)

        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_market_frame(frame, expected_codes={"510300.SH"})

    def test_non_positive_price_fails_closed(self) -> None:
        frame = _valid_frame()
        frame.loc[0, "open"] = 0.0

        with self.assertRaisesRegex(ValueError, "price"):
            validate_market_frame(frame, expected_codes={"510300.SH"})

    def test_publication_is_checksummed_and_round_trips_identifiers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = write_market_publication(
                root,
                _valid_frame(),
                source_start="20180102",
                end_date="20180102",
            )
            loaded, verified = load_market_publication(root / manifest["publication_id"])

            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(len(manifest["data_sha256"]), 64)
            self.assertEqual(loaded["ts_code"].tolist(), ["510300.SH"])
            self.assertEqual(loaded["trade_date"].tolist(), ["20180102"])
            self.assertEqual(verified["manifest_sha256"], manifest["manifest_sha256"])
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(
                manifest["accounting_version"],
                "cash_distributions_v2",
            )
            self.assertEqual(manifest["distribution_count"], 0)
            self.assertIn("distribution_evidence", manifest)
            self.assertEqual(
                manifest["distribution_reference_tolerance"], 0.005
            )

    def test_modified_publication_fails_checksum(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = write_market_publication(
                root,
                _valid_frame(),
                source_start="20180102",
                end_date="20180102",
            )
            manifest_path = root / manifest["publication_id"] / "manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["end_date"] = "20180103"
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "manifest_checksum"):
                load_market_publication(manifest_path.parent)

    def test_materializer_collects_every_frozen_asset(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = materialize_market_data(
                provider=_FixtureProvider(),
                codes=("510300.SH", "511260.SH", "511880.SH", "518880.SH"),
                source_start="20180102",
                end_date="20180102",
                output_root=root,
            )
            loaded, _ = load_market_publication(root / manifest["publication_id"])

            self.assertEqual(
                sorted(loaded["ts_code"].unique().tolist()),
                ["510300.SH", "511260.SH", "511880.SH", "518880.SH"],
            )
            self.assertTrue(loaded["is_open"].all())

    def test_materializer_rejects_missing_exchange_open_date(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "missing_open_date"):
                materialize_market_data(
                    provider=_MissingOpenDateProvider(),
                    codes=("510300.SH",),
                    source_start="20180102",
                    end_date="20180103",
                    output_root=Path(tmp),
                )

    def test_materializer_accepts_verified_full_day_suspension(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = materialize_market_data(
                provider=_SuspendedProvider(),
                codes=("510300.SH",),
                source_start="20180102",
                end_date="20180103",
                output_root=root,
            )
            frame, _ = load_market_publication(
                root / manifest["publication_id"]
            )

            suspended = frame.loc[frame["trade_date"].eq("20180103")].iloc[0]
            self.assertFalse(bool(suspended["is_open"]))
            self.assertEqual(float(suspended["close"]), 4.0)

    def test_materializer_accepts_price_continuous_zero_trade_gap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = materialize_market_data(
                provider=_ZeroTradeGapProvider(),
                codes=("511260.SH",),
                source_start="20180102",
                end_date="20180104",
                output_root=root,
            )
            frame, _ = load_market_publication(
                root / manifest["publication_id"]
            )

            zero_trade = frame.loc[
                frame["trade_date"].eq("20180103")
            ].iloc[0]
            self.assertFalse(bool(zero_trade["is_open"]))
            self.assertEqual(float(zero_trade["close"]), 4.0)

    def test_partitioned_publication_keeps_holdout_out_of_development(self) -> None:
        frame = pd.concat(
            [
                _valid_frame().assign(trade_date="20240102"),
                _valid_frame().assign(trade_date="20250102"),
            ],
            ignore_index=True,
        )
        with TemporaryDirectory() as tmp:
            manifest = write_partitioned_market_publication(
                Path(tmp),
                frame,
                source_start="20240102",
                end_date="20250102",
                holdout_start="20250101",
            )
            development, _ = load_market_publication(
                Path(tmp)
                / "development"
                / manifest["development"]["publication_id"]
            )
            holdout, _ = load_market_publication(
                Path(tmp)
                / "holdout"
                / manifest["holdout"]["publication_id"]
            )

            self.assertEqual(development["trade_date"].max(), "20240102")
            self.assertEqual(holdout["trade_date"].min(), "20240102")
            self.assertEqual(holdout["trade_date"].max(), "20250102")

    def test_partitioned_publication_binds_raw_source_manifest(self) -> None:
        frame = pd.concat(
            [
                _valid_frame().assign(trade_date="20240102"),
                _valid_frame().assign(trade_date="20250102"),
            ],
            ignore_index=True,
        )
        source_sha256 = "a" * 64
        with TemporaryDirectory() as tmp:
            manifest = write_partitioned_market_publication(
                Path(tmp),
                frame,
                source_start="20240102",
                end_date="20250102",
                holdout_start="20250101",
                source_manifest_sha256=source_sha256,
            )

            self.assertEqual(
                manifest["source_manifest_sha256"], source_sha256
            )
            self.assertEqual(
                manifest["development"]["source_manifest_sha256"],
                source_sha256,
            )
            self.assertEqual(
                manifest["holdout"]["source_manifest_sha256"],
                source_sha256,
            )


if __name__ == "__main__":
    unittest.main()
