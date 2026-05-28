import inspect
import unittest


class SharedModulesAcceptMarketParamTests(unittest.TestCase):
    def _assert_has_market_kwarg(self, func, default="a_share"):
        sig = inspect.signature(func)
        self.assertIn("market", sig.parameters,
                       msg=f"{func.__qualname__} missing 'market' parameter")
        self.assertEqual(
            sig.parameters["market"].default, default,
            msg=f"{func.__qualname__} 'market' default != {default!r}",
        )

    def test_evolution_writer_write_evolution(self):
        from stock_analyze.evolution_writer import write_evolution
        self._assert_has_market_kwarg(write_evolution)

    def test_sanity_check_check_agent(self):
        from stock_analyze.sanity_check import check_agent
        self._assert_has_market_kwarg(check_agent)

    def test_agent_briefing_build_weekly_briefing(self):
        from stock_analyze.agent_briefing import build_weekly_briefing
        self._assert_has_market_kwarg(build_weekly_briefing)

    def test_agent_briefing_build_monthly_briefing(self):
        from stock_analyze.agent_briefing import build_monthly_briefing
        self._assert_has_market_kwarg(build_monthly_briefing)

    def test_monthly_review_compute_review(self):
        from stock_analyze.monthly_review import compute_review
        self._assert_has_market_kwarg(compute_review)

    def test_agent_rollback_rollback(self):
        from stock_analyze.agent_rollback import rollback
        self._assert_has_market_kwarg(rollback)

    def test_dashboard_aggregator_generate_competition_dashboard(self):
        from stock_analyze.dashboard_aggregator import (
            generate_competition_dashboard,
        )
        self._assert_has_market_kwarg(generate_competition_dashboard)


if __name__ == "__main__":
    unittest.main()
