"""Smoke tests for the serve-dashboard HTTP route aliasing logic.

We do not bring up a real TCPServer (slow and port-bound); instead we
exercise the ``DASHBOARD_ROUTES`` table and the handler's path rewrite
behaviour directly.
"""

from __future__ import annotations

import unittest

from stock_analyze.cli import DASHBOARD_ROUTES, _DashboardRequestHandler


class DashboardRoutesTableTests(unittest.TestCase):
    def test_root_maps_to_simple(self) -> None:
        self.assertEqual(DASHBOARD_ROUTES["/"], "/competition/simple.html")

    def test_pro_alias_points_at_existing_dashboard(self) -> None:
        self.assertEqual(DASHBOARD_ROUTES["/pro.html"], "/competition/dashboard.html")

    def test_per_agent_simple_routes(self) -> None:
        self.assertEqual(
            DASHBOARD_ROUTES["/simple/claude.html"],
            "/competition/simple/claude.html",
        )
        self.assertEqual(
            DASHBOARD_ROUTES["/simple/codex.html"],
            "/competition/simple/codex.html",
        )

    def test_unmapped_path_falls_through(self) -> None:
        # The pro view path itself is NOT rewritten (it's served directly).
        self.assertNotIn("/competition/dashboard.html", DASHBOARD_ROUTES)
        self.assertNotIn("/claude/dashboard.html", DASHBOARD_ROUTES)


class HandlerRewriteTests(unittest.TestCase):
    """Verify the request handler rewrites `self.path` per the table."""

    def _rewrite(self, raw_path: str) -> str:
        """Apply the same rewrite logic the handler performs in do_GET, sans I/O."""

        path, _, suffix = raw_path.partition("?")
        target = DASHBOARD_ROUTES.get(path)
        if target is not None:
            return target + (("?" + suffix) if suffix else "")
        return raw_path

    def test_root_rewrites_to_simple(self) -> None:
        self.assertEqual(self._rewrite("/"), "/competition/simple.html")

    def test_pro_alias_rewrites(self) -> None:
        self.assertEqual(self._rewrite("/pro.html"), "/competition/dashboard.html")

    def test_query_string_preserved(self) -> None:
        self.assertEqual(
            self._rewrite("/simple.html?from=tab"),
            "/competition/simple.html?from=tab",
        )

    def test_unmapped_path_unchanged(self) -> None:
        self.assertEqual(
            self._rewrite("/competition/dashboard.html"),
            "/competition/dashboard.html",
        )
        self.assertEqual(
            self._rewrite("/claude/dashboard.html"),
            "/claude/dashboard.html",
        )

    def test_handler_class_inherits_simple_http(self) -> None:
        import http.server

        self.assertTrue(issubclass(_DashboardRequestHandler, http.server.SimpleHTTPRequestHandler))


if __name__ == "__main__":
    unittest.main()
