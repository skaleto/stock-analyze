from __future__ import annotations

import gzip
import threading
import time
import unittest

from stock_analyze.dashboard_http import (
    DashboardResponseCache,
    build_http_response,
)


class DashboardHttpTests(unittest.TestCase):
    def test_cache_builds_compact_json_once_during_ttl(self) -> None:
        now = [100.0]
        calls = []
        cache = DashboardResponseCache(ttl_seconds=15, clock=lambda: now[0])

        first, first_status = cache.get_or_build(
            "overview",
            lambda: calls.append("build") or {"market": "A股", "rows": [1, 2]},
        )
        now[0] = 110.0
        second, second_status = cache.get_or_build(
            "overview",
            lambda: calls.append("rebuild") or {"market": "changed"},
        )

        self.assertEqual(calls, ["build"])
        self.assertIs(first, second)
        self.assertEqual(first_status, "miss")
        self.assertEqual(second_status, "hit")
        self.assertNotIn(b"\n", first.identity)
        self.assertNotIn(b": ", first.identity)

    def test_cache_rebuilds_after_ttl(self) -> None:
        now = [100.0]
        cache = DashboardResponseCache(ttl_seconds=15, clock=lambda: now[0])
        first, _ = cache.get_or_build("key", lambda: {"version": 1})
        now[0] = 116.0
        second, status = cache.get_or_build("key", lambda: {"version": 2})

        self.assertEqual(status, "miss")
        self.assertNotEqual(first.etag, second.etag)

    def test_response_uses_gzip_and_cache_headers(self) -> None:
        cache = DashboardResponseCache(ttl_seconds=15)
        entry, cache_status = cache.get_or_build(
            "predictions",
            lambda: {"rows": [{"code": "000001"}] * 100},
        )
        response = build_http_response(
            entry,
            accept_encoding="br, gzip",
            if_none_match=None,
            cache_status=cache_status,
            request_id="request-1",
            elapsed_ms=12.5,
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(response.body), entry.identity)
        self.assertEqual(response.headers["ETag"], entry.etag)
        self.assertEqual(response.headers["Vary"], "Accept-Encoding")
        self.assertEqual(response.headers["X-Cache"], "MISS")
        self.assertEqual(response.headers["X-Request-ID"], "request-1")
        self.assertIn("max-age=15", response.headers["Cache-Control"])
        self.assertIn("app;dur=12.5", response.headers["Server-Timing"])

    def test_matching_etag_returns_304_without_body(self) -> None:
        cache = DashboardResponseCache(ttl_seconds=15)
        entry, _ = cache.get_or_build("overview", lambda: {"ok": True})
        response = build_http_response(
            entry,
            accept_encoding="gzip",
            if_none_match=entry.etag,
            cache_status="hit",
            request_id="request-2",
            elapsed_ms=0.2,
        )

        self.assertEqual(response.status, 304)
        self.assertEqual(response.body, b"")
        self.assertNotIn("Content-Encoding", response.headers)
        self.assertEqual(response.headers["Content-Length"], "0")

    def test_different_cache_keys_build_concurrently(self) -> None:
        cache = DashboardResponseCache(ttl_seconds=15)
        slow_started = threading.Event()
        release_slow = threading.Event()

        def slow_builder():
            slow_started.set()
            release_slow.wait(timeout=0.3)
            return {"resource": "slow"}

        thread = threading.Thread(target=lambda: cache.get_or_build("slow", slow_builder))
        thread.start()
        self.assertTrue(slow_started.wait(timeout=0.1))
        started = time.perf_counter()
        cache.get_or_build("fast", lambda: {"resource": "fast"})
        elapsed = time.perf_counter() - started
        release_slow.set()
        thread.join(timeout=0.5)

        self.assertLess(elapsed, 0.1)

    def test_gzip_quality_value_is_respected(self) -> None:
        cache = DashboardResponseCache(ttl_seconds=15)
        entry, _ = cache.get_or_build("large", lambda: {"rows": ["value"] * 100})
        accepted = build_http_response(
            entry,
            accept_encoding="gzip;q=0.5",
            if_none_match=None,
            cache_status="hit",
            request_id="accepted",
            elapsed_ms=0,
        )
        rejected = build_http_response(
            entry,
            accept_encoding="gzip;q=0",
            if_none_match=None,
            cache_status="hit",
            request_id="rejected",
            elapsed_ms=0,
        )

        self.assertEqual(accepted.headers["Content-Encoding"], "gzip")
        self.assertNotIn("Content-Encoding", rejected.headers)


if __name__ == "__main__":
    unittest.main()
