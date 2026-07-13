import unittest

import pandas as pd

from stock_analyze.research.events import detect_events


class ResearchEventsTest(unittest.TestCase):
    def test_detects_supported_technical_flow_and_breadth_events(self):
        frame = pd.DataFrame(
            {
                "code": ["000001"] * 4,
                "trade_date": ["20260707", "20260708", "20260709", "20260710"],
                "close": [10.0, 9.0, 9.5, 11.0],
                "macd_dif": [-0.2, -0.1, 0.1, 0.2],
                "macd_dea": [-0.1, 0.0, 0.05, 0.1],
                "macd_hist": [-0.1, -0.1, 0.05, 0.1],
                "macd_hist_slope": [-0.1, -0.05, 0.15, 0.05],
                "sma_5": [10.0, 9.8, 9.9, 10.5],
                "sma_20": [10.2, 10.0, 9.95, 10.0],
                "rsi_14": [25.0, 28.0, 35.0, 72.0],
                "adx_14": [18.0, 22.0, 27.0, 30.0],
                "bollinger_width": [0.20, 0.08, 0.07, 0.25],
                "bollinger_upper": [11.0, 10.0, 10.0, 10.5],
                "bollinger_lower": [9.0, 8.5, 8.7, 9.0],
                "volume_ratio_5_20": [0.8, 0.7, 1.2, 1.8],
                "flow_net_large": [-5.0, -3.0, 4.0, 8.0],
                "industry_breadth": [0.25, 0.28, 0.52, 0.61],
                "regime": ["down", "down", "recovery", "up"],
                "industry": ["银行"] * 4,
            }
        )

        events = detect_events(frame, market="a_share")

        names = set(events["event"])
        self.assertTrue(
            {
                "macd_golden_cross",
                "macd_zero_cross_up",
                "macd_hist_reversal_up",
                "ma_golden_cross_5_20",
                "rsi_oversold_exit",
                "adx_trend_strengthening",
                "bollinger_breakout_up",
                "flow_price_confirmation_up",
                "industry_breadth_reversal_up",
            }.issubset(names)
        )
        self.assertEqual(len(events["event_id"]), events["event_id"].nunique())

    def test_same_input_produces_stable_event_ids(self):
        frame = pd.DataFrame(
            [{"code": "000001", "trade_date": "20260710", "macd_cross": 1.0}]
        )
        first = detect_events(frame, market="a_share")
        second = detect_events(frame, market="a_share")
        self.assertEqual(first.iloc[0]["event_id"], second.iloc[0]["event_id"])


if __name__ == "__main__":
    unittest.main()
