import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.regime_policy import apply_regime_policy, load_regime_decision


class RegimePolicyTest(unittest.TestCase):
    def test_fresh_risk_off_penalizes_momentum_and_reduces_trend_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "research" / "regimes" / "a_share" / "20260710.parquet"
            path.parent.mkdir(parents=True)
            pd.DataFrame([{
                "trade_date": "20260710",
                "scope": "market",
                "composite_regime": "risk_off",
                "regime_coverage": 1.0,
            }]).to_parquet(path, index=False)
            decision = load_regime_decision(
                root,
                market="a_share",
                as_of="2026-07-10",
                profile="trend",
            )
            candidates = pd.DataFrame([
                {"code": "000001", "score": 1.0, "momentum_20": 0.20, "low_volatility_60": 0.40, "roe": 0.05},
                {"code": "000002", "score": 1.0, "momentum_20": -0.02, "low_volatility_60": 0.10, "roe": 0.20},
            ])

            adjusted = apply_regime_policy(candidates, decision, profile="trend")

        self.assertEqual(decision.state, "risk_off")
        self.assertEqual(decision.gross_exposure, 0.55)
        self.assertGreater(adjusted.iloc[1]["score"], adjusted.iloc[0]["score"])
        self.assertTrue(adjusted["regime_applied"].all())

    def test_stale_regime_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "research" / "regimes" / "cn_qdii_etf" / "20260601.parquet"
            path.parent.mkdir(parents=True)
            pd.DataFrame([{
                "trade_date": "20260601",
                "scope": "market",
                "composite_regime": "risk_on",
                "regime_coverage": 1.0,
            }]).to_parquet(path, index=False)

            decision = load_regime_decision(
                root,
                market="cn_qdii_etf",
                as_of="2026-07-10",
                profile="defensive",
            )

        self.assertEqual(decision.state, "unknown")
        self.assertTrue(decision.stale)
        self.assertEqual(decision.gross_exposure, 0.70)
        self.assertIn("stale", decision.warning)


if __name__ == "__main__":
    unittest.main()
