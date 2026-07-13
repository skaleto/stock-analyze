import unittest
from datetime import datetime

from stock_analyze.research.external_events import DisabledEventAdapter, TushareEventAdapter


class ResearchExternalEventsTest(unittest.TestCase):
    def test_disabled_sources_emit_health_without_neutral_events(self):
        for source in ("news", "announcement", "policy"):
            result = DisabledEventAdapter(source).fetch(datetime(2026, 7, 1), datetime(2026, 7, 10))
            self.assertEqual(result.health.status, "source_unavailable")
            self.assertEqual(result.events, ())

    def test_tushare_adapter_does_not_call_network_when_disabled(self):
        class ExplodingClient:
            def __getattr__(self, name):
                raise AssertionError(f"network call attempted: {name}")

        adapter = TushareEventAdapter("announcement", ExplodingClient(), endpoint="anns_d", enabled=False)
        result = adapter.fetch(datetime(2026, 7, 1), datetime(2026, 7, 10))
        self.assertEqual(result.health.status, "source_unavailable")
        self.assertEqual(result.events, ())


if __name__ == "__main__":
    unittest.main()
