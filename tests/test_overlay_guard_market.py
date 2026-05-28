import unittest

from stock_analyze.overlay_guard import (
    AVAILABLE_FACTORS_BY_MARKET,
    validate,
)


class OverlayGuardMarketTests(unittest.TestCase):
    def test_a_share_factor_set_includes_classic_factors(self):
        factors = AVAILABLE_FACTORS_BY_MARKET["a_share"]
        for name in ("pe", "pb", "roe", "momentum_20", "momentum_60",
                     "low_volatility_60", "dividend_yield"):
            self.assertIn(name, factors, msg=f"a_share missing factor {name}")

    def test_a_share_includes_claude_sentiment(self):
        factors = AVAILABLE_FACTORS_BY_MARKET["a_share"]
        self.assertIn("claude_market_sentiment_1w", factors)

    def test_validate_accepts_market_kwarg_default_a_share(self):
        # Backwards-compat: omitting market should behave like market='a_share'.
        # The overlay format matches the canonical schema enforced elsewhere
        # in overlay_guard tests (factors keyed by name -> {weight, direction}).
        valid_overlay = {
            "agent_id": "claude",
            "strategy_id": "test",
            "name": "Test",
            "factors": {"pe": {"weight": 1.0, "direction": "low"}},
            "factor_processing": {},
            "portfolio_controls": {},
            "filters": {},
        }
        # Should not raise: omitting `market` defaults to "a_share".
        validate("claude", valid_overlay, baseline={})
        # Equivalent explicit call.
        validate("claude", valid_overlay, baseline={}, market="a_share")


if __name__ == "__main__":
    unittest.main()
