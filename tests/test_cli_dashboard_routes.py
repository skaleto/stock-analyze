"""Smoke tests for the serve-dashboard HTTP route aliasing logic.

We do not bring up a real TCPServer (slow and port-bound); instead we
exercise the ``DASHBOARD_ROUTES`` table and the handler's path rewrite
behaviour directly.
"""

from __future__ import annotations

import io
import json
import socketserver
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from stock_analyze.cli import (
    DASHBOARD_ROUTES,
    _DashboardRequestHandler,
    _is_dashboard_api_path,
    _resolve_dashboard_route,
)
from stock_analyze import cli


class DashboardRoutesTableTests(unittest.TestCase):
    def test_dashboard_server_handles_connections_concurrently(self) -> None:
        server_class = getattr(cli, "_DashboardHTTPServer", socketserver.TCPServer)

        self.assertTrue(issubclass(server_class, socketserver.ThreadingMixIn))

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
        self.assertTrue(_is_dashboard_api_path("/api/dashboard/research-universe-instrument.json"))
        for resource in (
            "system-overview",
            "model-research",
            "multi-agent-research",
            "research-universe",
            "data-intelligence",
            "operations-center",
            "permanent-portfolio",
            "overview",
            "performance",
            "portfolio",
            "predictions",
            "research",
            "operations",
        ):
            self.assertTrue(_is_dashboard_api_path(f"/api/dashboard/{resource}.json"))
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

    def test_handler_uses_persistent_http_connections(self) -> None:
        self.assertEqual(_DashboardRequestHandler.protocol_version, "HTTP/1.1")

    def test_handler_keeps_a_day_of_stale_api_data(self) -> None:
        self.assertEqual(
            _DashboardRequestHandler._response_cache.stale_seconds,
            86_400,
        )

    def _serve_api(
        self,
        root: Path,
        query: str,
        *,
        path: str = "/api/dashboard/detail.json",
    ) -> tuple[int, dict]:
        reports = root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        handler = object.__new__(_DashboardRequestHandler)
        handler.directory = str(reports)
        handler.wfile = io.BytesIO()
        statuses: list[int] = []
        handler.send_response = statuses.append
        handler.send_header = lambda *_args: None
        handler.end_headers = lambda: None

        handler._serve_dashboard_api(path, query)

        return statuses[-1], json.loads(handler.wfile.getvalue().decode("utf-8"))

    def test_model_research_api_dispatches_market_query(self) -> None:
        expected = {"market": "cn_qdii_etf", "stages": []}
        with TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api."
            "build_dashboard_model_research_data",
            return_value=expected,
        ) as builder:
            status, payload = self._serve_api(
                Path(tmp),
                "market=cn_qdii_etf",
                path="/api/dashboard/model-research.json",
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        builder.assert_called_once_with(
            repo_root=Path(tmp).resolve(),
            market="cn_qdii_etf",
        )

    def test_data_intelligence_api_dispatches_market_query(self) -> None:
        expected = {"market": "cn_qdii_etf", "usageMatrix": []}
        with TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api."
            "build_dashboard_data_intelligence_data",
            return_value=expected,
        ) as builder:
            status, payload = self._serve_api(
                Path(tmp),
                "market=cn_qdii_etf",
                path="/api/dashboard/data-intelligence.json",
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        builder.assert_called_once_with(
            repo_root=Path(tmp).resolve(),
            market="cn_qdii_etf",
        )

    def test_multi_agent_research_api_reads_completed_artifacts_only(self) -> None:
        expected = {"status": "empty", "latestRun": None}
        with TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_multi_agent_research."
            "build_dashboard_multi_agent_research_data",
            return_value=expected,
        ) as builder:
            status, payload = self._serve_api(
                Path(tmp),
                "",
                path="/api/dashboard/multi-agent-research.json",
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        builder.assert_called_once_with(repo_root=Path(tmp).resolve())

    def test_permanent_portfolio_api_reads_completed_artifacts_only(self) -> None:
        expected = {"status": "available", "windows": {}}
        with TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_permanent_portfolio."
            "build_dashboard_permanent_portfolio_data",
            return_value=expected,
        ) as builder:
            status, payload = self._serve_api(
                Path(tmp),
                "",
                path="/api/dashboard/permanent-portfolio.json",
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        builder.assert_called_once_with(repo_root=Path(tmp).resolve())

    def test_research_universe_api_dispatches_bounded_query(self) -> None:
        expected = {"status": "available", "records": []}
        with TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_multi_agent_research."
            "build_dashboard_research_universe_data",
            return_value=expected,
        ) as builder:
            status, payload = self._serve_api(
                Path(tmp),
                "kind=otc_fund&query=%E7%BA%B3%E6%96%AF&scope=nasdaq_100&page=2&page_size=50",
                path="/api/dashboard/research-universe.json",
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        builder.assert_called_once_with(
            repo_root=Path(tmp).resolve(),
            kind="otc_fund",
            query="纳斯",
            scope="nasdaq_100",
            page=2,
            page_size=50,
        )

    def test_research_universe_api_rejects_non_integer_pagination(self) -> None:
        with TemporaryDirectory() as tmp:
            for query in ("page=bad", "page_size=bad"):
                status, payload = self._serve_api(
                    Path(tmp),
                    query,
                    path="/api/dashboard/research-universe.json",
                )

                self.assertEqual(status, 400)
                self.assertEqual(payload["error"], "invalid_query")

    def test_research_universe_instrument_api_dispatches_catalog_scoped_query(self) -> None:
        expected = {"status": "available", "candles": []}
        with TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_multi_agent_research."
            "build_dashboard_research_universe_instrument_data",
            return_value=expected,
        ) as builder:
            status, payload = self._serve_api(
                Path(tmp),
                "kind=a_share&code=000001.SZ",
                path="/api/dashboard/research-universe-instrument.json",
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        builder.assert_called_once_with(
            repo_root=Path(tmp).resolve(),
            kind="a_share",
            code="000001.SZ",
        )

    def test_operations_center_api_dispatches_scope_query(self) -> None:
        expected = {"scope": "exceptions", "mainChain": []}
        with TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api."
            "build_dashboard_operations_center_data",
            return_value=expected,
        ) as builder:
            status, payload = self._serve_api(
                Path(tmp),
                "scope=exceptions",
                path="/api/dashboard/operations-center.json",
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        builder.assert_called_once_with(
            repo_root=Path(tmp).resolve(),
            scope="exceptions",
        )

    def test_operations_center_api_rejects_unknown_scope(self) -> None:
        with TemporaryDirectory() as tmp:
            status, payload = self._serve_api(
                Path(tmp),
                "scope=unknown",
                path="/api/dashboard/operations-center.json",
            )

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_query")

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
