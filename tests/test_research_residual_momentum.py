import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.residual_momentum import (
    ResidualMomentumConfig,
    build_exante_residual_momentum,
)


class ResidualMomentumTest(unittest.TestCase):
    def _frame(self, periods: int = 180) -> tuple[pd.DataFrame, pd.DataFrame]:
        dates = pd.bdate_range("2020-01-01", periods=periods)
        market = 0.004 * np.sin(np.arange(periods) / 7.0)
        benchmark = pd.DataFrame({
            "trade_date": dates.strftime("%Y%m%d"),
            "benchmark_return_1": market,
        })
        rows = []
        for code, industry, alpha in (
            ("000001", "bank", 0.0),
            ("000002", "tech", 0.0015),
        ):
            idiosyncratic = (
                np.zeros(periods, dtype=float)
                if alpha == 0.0
                else alpha + 0.00001 * np.arange(periods)
            )
            returns = 1.4 * market + idiosyncratic
            for index, day in enumerate(dates):
                rows.append({
                    "code": code,
                    "trade_date": day.strftime("%Y%m%d"),
                    "return_1": returns[index],
                    "industry": industry,
                    "total_mv": 100.0 + index + (50.0 if code == "000002" else 0.0),
                })
        return pd.DataFrame(rows), benchmark

    def test_removes_market_beta_and_retains_idiosyncratic_momentum(self):
        frame, benchmark = self._frame()
        result = build_exante_residual_momentum(
            frame,
            benchmark,
            config=ResidualMomentumConfig(
                regression_window=80,
                minimum_history=40,
                windows=((20, 5), (60, 5)),
            ),
        )
        latest = result.loc[result["trade_date"].eq(result["trade_date"].max())]
        pure_beta = latest.loc[
            latest["code"].eq("000001"), "exante_residual_momentum_60_5_raw"
        ].iloc[0]
        persistent = latest.loc[
            latest["code"].eq("000002"), "exante_residual_momentum_60_5"
        ].iloc[0]
        self.assertLess(abs(float(pure_beta)), 0.20)
        self.assertGreater(float(persistent), float(pure_beta))

    def test_current_return_does_not_change_current_signal(self):
        frame, benchmark = self._frame()
        config = ResidualMomentumConfig(
            regression_window=80,
            minimum_history=40,
            windows=((20, 5),),
        )
        baseline = build_exante_residual_momentum(frame, benchmark, config=config)
        changed = frame.copy()
        mask = (
            changed["code"].eq("000002")
            & changed["trade_date"].eq(changed["trade_date"].max())
        )
        changed.loc[mask, "return_1"] = 0.90
        rerun = build_exante_residual_momentum(changed, benchmark, config=config)
        key = ["code", "trade_date"]
        final_key = ("000002", frame["trade_date"].max())
        left = baseline.set_index(key).loc[final_key, "exante_residual_momentum_20_5"]
        right = rerun.set_index(key).loc[final_key, "exante_residual_momentum_20_5"]
        self.assertAlmostEqual(float(left), float(right), places=12)

    def test_insufficient_history_is_missing(self):
        frame, benchmark = self._frame(periods=30)
        result = build_exante_residual_momentum(
            frame,
            benchmark,
            config=ResidualMomentumConfig(
                regression_window=80,
                minimum_history=40,
                windows=((20, 5),),
            ),
        )
        self.assertTrue(result["exante_residual_momentum_20_5"].isna().all())


if __name__ == "__main__":
    unittest.main()
