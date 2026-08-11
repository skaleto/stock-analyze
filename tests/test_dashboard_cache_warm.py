from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_module():
    path = Path("scripts/warm-dashboard-cache.py")
    spec = importlib.util.spec_from_file_location("warm_dashboard_cache", path)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load dashboard cache warmer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DashboardCacheWarmTests(unittest.TestCase):
    def test_manifest_covers_global_and_strategy_first_screen_only(self) -> None:
        module = _load_module()
        endpoints = module.dashboard_endpoints()

        self.assertEqual(len(endpoints), 23)
        self.assertEqual(len(set(endpoints)), len(endpoints))
        self.assertIn("/api/dashboard/system-overview.json", endpoints)
        self.assertIn(
            "/api/dashboard/operations-center.json?scope=all",
            endpoints,
        )
        for market in ("a_share", "cn_qdii_etf"):
            self.assertIn(
                f"/api/dashboard/model-research.json?market={market}",
                endpoints,
            )
            self.assertIn(
                f"/api/dashboard/data-intelligence.json?market={market}",
                endpoints,
            )
            for agent in ("claude", "codex"):
                prefix = f"market={market}&agent={agent}"
                for resource in ("overview", "performance", "portfolio"):
                    self.assertIn(
                        f"/api/dashboard/{resource}.json?{prefix}",
                        endpoints,
                    )
                self.assertIn(
                    "/api/dashboard/predictions.json?"
                    f"{prefix}&limit_per_horizon=12",
                    endpoints,
                )

        heavy = (
            "/api/dashboard/research.json?",
            "/api/dashboard/governance.json?",
            "/api/dashboard/intelligence.json",
        )
        self.assertFalse(any(token in endpoint for endpoint in endpoints for token in heavy))

    def test_warm_continues_after_one_endpoint_fails(self) -> None:
        module = _load_module()

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"{}"

        calls: list[str] = []

        def opener(url: str, *, timeout: float):
            del timeout
            calls.append(url)
            if url.endswith("/broken"):
                raise OSError("unavailable")
            return Response()

        results = module.warm_endpoints(
            "http://127.0.0.1:8765",
            ("/ready", "/broken", "/still-ready"),
            timeout=1.0,
            opener=opener,
        )

        self.assertEqual(calls, [
            "http://127.0.0.1:8765/ready",
            "http://127.0.0.1:8765/broken",
            "http://127.0.0.1:8765/still-ready",
        ])
        self.assertEqual([item.ok for item in results], [True, False, True])


if __name__ == "__main__":
    unittest.main()
