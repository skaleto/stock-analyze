from __future__ import annotations

import unittest
import warnings
from tempfile import TemporaryDirectory

from stock_analyze.store import PortfolioStore


class StoreNavAppendTests(unittest.TestCase):
    def test_append_nav_first_write_emits_no_future_warning(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                store.append_nav(
                    [
                        {
                            "date": "2026-07-09",
                            "account_id": "us_exposure",
                            "cash": 100000.0,
                            "market_value": 200.0,
                            "total_value": 100200.0,
                            "benchmark_code": "513100.SH",
                            "benchmark_close": None,
                            "benchmark_date": "2026-07-09",
                            "notes": None,
                        }
                    ]
                )

        future_warnings = [w for w in caught if issubclass(w.category, FutureWarning)]
        self.assertEqual(future_warnings, [])


if __name__ == "__main__":
    unittest.main()
