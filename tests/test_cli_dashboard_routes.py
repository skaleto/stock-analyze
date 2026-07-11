"""Smoke tests for the serve-dashboard HTTP route aliasing logic.

We do not bring up a real TCPServer (slow and port-bound); instead we
exercise the ``DASHBOARD_ROUTES`` table and the handler's path rewrite
behaviour directly.
"""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze.cli import (
    DASHBOARD_ROUTES,
    _DashboardRequestHandler,
    _is_dashboard_api_path,
    _resolve_dashboard_route,
)


class DashboardRoutesTableTests(unittest.TestCase):
    def test_root_maps_to_simple(self) -> None:
        self.assertEqual(DASHBOARD_ROUTES["/"], "/competition/simple.html")

    def test_pro_alias_points_at_existing_dashboard(self) -> None:
        self.assertEqual(DASHBOARD_ROUTES["/pro.html"], "/competition/dashboard.html")

    def test_react_app_alias_points_at_built_entry(self) -> None:
        self.assertEqual(DASHBOARD_ROUTES["/app.html"], "/app/index.html")
        self.assertEqual(DASHBOARD_ROUTES["/app/"], "/app/index.html")

    def test_market_agent_pro_routes(self) -> None:
        self.assertEqual(
            DASHBOARD_ROUTES["/pro/a_share/claude.html"],
            "/a_share/claude/dashboard.html",
        )
        self.assertEqual(
            DASHBOARD_ROUTES["/pro/cn_qdii_etf/codex.html"],
            "/cn_qdii_etf/codex/dashboard.html",
        )

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

    def test_a_share_agent_route_does_not_fall_back_to_legacy(self) -> None:
        with TemporaryDirectory() as tmp:
            reports = Path(tmp)
            legacy = reports / "claude" / "dashboard.html"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy", encoding="utf-8")

            self.assertEqual(
                _resolve_dashboard_route("/pro/a_share/claude.html", reports),
                "/a_share/claude/dashboard.html",
            )

    def test_compat_pro_agent_routes_point_to_a_share_namespace(self) -> None:
        self.assertEqual(DASHBOARD_ROUTES["/pro/claude.html"], "/a_share/claude/dashboard.html")
        self.assertEqual(DASHBOARD_ROUTES["/pro/codex.html"], "/a_share/codex/dashboard.html")

    def test_dynamic_market_agent_route(self) -> None:
        with TemporaryDirectory() as tmp:
            reports = Path(tmp)
            target = reports / "cn_qdii_etf" / "gemini" / "dashboard.html"
            target.parent.mkdir(parents=True)
            target.write_text("ok", encoding="utf-8")

            self.assertEqual(
                _resolve_dashboard_route("/pro/cn_qdii_etf/gemini.html", reports),
                "/cn_qdii_etf/gemini/dashboard.html",
            )

    def test_dashboard_summary_api_route(self) -> None:
        self.assertTrue(_is_dashboard_api_path("/api/dashboard/summary.json"))
        self.assertTrue(_is_dashboard_api_path("/api/dashboard.json"))
        self.assertTrue(_is_dashboard_api_path("/api/dashboard/instrument.json"))
        self.assertFalse(_is_dashboard_api_path("/pro.html"))


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

    def test_market_agent_pro_alias_rewrites(self) -> None:
        self.assertEqual(
            self._rewrite("/pro/cn_qdii_etf/codex.html"),
            "/cn_qdii_etf/codex/dashboard.html",
        )

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

    def _serve_api(self, root: Path, query: str) -> tuple[int, dict]:
        reports = root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        handler = object.__new__(_DashboardRequestHandler)
        handler.directory = str(reports)
        handler.wfile = io.BytesIO()
        statuses: list[int] = []
        handler.send_response = statuses.append
        handler.send_header = lambda *_args: None
        handler.end_headers = lambda: None

        handler._serve_dashboard_api("/api/dashboard/detail.json", query)

        return statuses[-1], json.loads(handler.wfile.getvalue().decode("utf-8"))

    def test_detail_api_returns_400_for_unknown_market(self) -> None:
        with TemporaryDirectory() as tmp:
            status, payload = self._serve_api(
                Path(tmp),
                "market=not-a-market&agent=codex",
            )

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "unknown_market")
        self.assertNotIn(tmp, payload["message"])

    def test_detail_api_returns_404_for_unknown_agent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs" / "agents").mkdir(parents=True)
            status, payload = self._serve_api(
                root,
                "market=cn_qdii_etf&agent=missing",
            )

        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "unknown_agent")
        self.assertNotIn(tmp, payload["message"])


if __name__ == "__main__":
    unittest.main()
