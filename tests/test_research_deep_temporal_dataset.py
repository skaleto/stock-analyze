import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.deep.temporal_dataset import prepare_temporal_dataset


class TemporalDatasetTest(unittest.TestCase):
    @staticmethod
    def _frames() -> tuple[pd.DataFrame, pd.DataFrame]:
        dates = pd.bdate_range("2023-01-02", periods=180)
        features = []
        labels = []
        for date_index, date in enumerate(dates):
            for code_index in range(8):
                code = f"{code_index + 1:06d}"
                features.append(
                    {
                        "code": code,
                        "trade_date": date.strftime("%Y%m%d"),
                        "return_1": 0.001 * (date_index + code_index),
                        "momentum_5": 0.002 * (date_index - code_index),
                        "rsi_14": 40.0 + code_index + date_index % 10,
                        "roe": 0.08 + code_index * 0.01,
                        "industry": "软件" if code_index < 4 else "银行",
                    }
                )
                for horizon in (3, 5, 10, 20):
                    end_index = date_index + horizon
                    if end_index >= len(dates):
                        continue
                    excess_return = 0.003 * (code_index - 3.5) + 0.0001 * date_index
                    labels.append(
                        {
                            "code": code,
                            "trade_date": date.strftime("%Y%m%d"),
                            "horizon": horizon,
                            "label_end_date": dates[end_index].strftime("%Y%m%d"),
                            "label": "up" if excess_return > 0.004 else "down" if excess_return < -0.004 else "flat",
                            "excess_return": excess_return,
                        }
                    )
        return pd.DataFrame(features), pd.DataFrame(labels)

    def test_sequences_are_code_local_point_in_time_and_multi_horizon(self):
        features, labels = self._frames()
        prepared = prepare_temporal_dataset(
            features,
            labels,
            sequence_length=10,
            minimum_sequence_observations=8,
            min_nonzero_rows=4,
            max_static_features=4,
        )

        self.assertEqual(prepared.horizons, (3, 5, 10, 20))
        self.assertEqual(prepared.train.y_class.shape[1], 4)
        self.assertGreater(len(prepared.train.metadata), 0)
        self.assertLess(
            prepared.train.metadata["label_end_date_20"].max(),
            prepared.calibration.metadata["trade_date"].min(),
        )
        self.assertLess(
            prepared.calibration.metadata["label_end_date_20"].max(),
            prepared.validation.metadata["trade_date"].min(),
        )

        for split in (prepared.train, prepared.calibration, prepared.validation):
            for row_index in range(min(20, len(split.metadata))):
                indices = split.sequence_indices[row_index]
                indices = indices[indices >= 0]
                history = prepared.history_metadata.iloc[indices]
                target = split.metadata.iloc[row_index]
                self.assertTrue(history["code"].eq(target["code"]).all())
                self.assertEqual(history["trade_date"].iloc[-1], target["trade_date"])
                self.assertTrue(history["trade_date"].le(target["trade_date"]).all())

    def test_context_is_same_date_and_sequence_storage_is_indexed(self):
        features, labels = self._frames()
        prepared = prepare_temporal_dataset(
            features,
            labels,
            sequence_length=10,
            minimum_sequence_observations=8,
            min_nonzero_rows=4,
            max_static_features=4,
        )

        self.assertEqual(prepared.train.sequence_indices.dtype, np.int32)
        self.assertEqual(prepared.history_values.ndim, 2)
        self.assertEqual(prepared.train.sequence_indices.shape[1], 10)
        self.assertEqual(
            prepared.train.industry_context.shape[1],
            len(prepared.sequence_columns),
        )
        self.assertEqual(
            prepared.train.market_context.shape,
            prepared.train.industry_context.shape,
        )
        self.assertTrue(np.isfinite(prepared.train.static_values).all())
        self.assertTrue(np.isfinite(prepared.train.industry_context).all())
        self.assertTrue(np.isfinite(prepared.train.market_context).all())


if __name__ == "__main__":
    unittest.main()
