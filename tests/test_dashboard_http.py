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
        cache = DashboardResponseCache(
            ttl_seconds=15,
            stale_seconds=0,
            clock=lambda: now[0],
        )
        first, _ = cache.get_or_build("key", lambda: {"version": 1})
        now[0] = 116.0
        second, status = cache.get_or_build("key", lambda: {"version": 2})

        self.assertEqual(status, "miss")
        self.assertNotEqual(first.etag, second.etag)

    def test_stale_entry_returns_immediately_and_refreshes_once(self) -> None:
        now = [100.0]
        cache = DashboardResponseCache(
            ttl_seconds=15,
            stale_seconds=300,
            clock=lambda: now[0],
        )
        first, _ = cache.get_or_build("key", lambda: {"version": 1})
        now[0] = 116.0
        refresh_started = threading.Event()
        release_refresh = threading.Event()
        refresh_finished = threading.Event()
        calls = []

        def slow_builder():
            calls.append("refresh")
            refresh_started.set()
            release_refresh.wait(timeout=1.0)
            refresh_finished.set()
            return {"version": 2}

        started = time.perf_counter()
        stale, stale_status = cache.get_or_build("key", slow_builder)
        duplicate, duplicate_status = cache.get_or_build("key", slow_builder)
        elapsed = time.perf_counter() - started

        self.assertTrue(refresh_started.wait(timeout=0.2))
        self.assertLess(elapsed, 0.1)
        self.assertIs(first, stale)
        self.assertIs(first, duplicate)
        self.assertEqual(stale_status, "stale")
        self.assertEqual(duplicate_status, "stale")
        self.assertEqual(calls, ["refresh"])

        release_refresh.set()
        self.assertTrue(refresh_finished.wait(timeout=0.2))
        for _ in range(20):
            refreshed, refreshed_status = cache.get_or_build(
                "key",
                lambda: {"version": 3},
            )
            if refreshed_status == "hit" and refreshed.etag != first.etag:
                break
            time.sleep(0.01)

        self.assertEqual(refreshed_status, "hit")
        self.assertNotEqual(first.etag, refreshed.etag)

    def test_generated_at_does_not_invalidate_semantic_etag(self) -> None:
        now = [100.0]
        cache = DashboardResponseCache(
            ttl_seconds=15,
            stale_seconds=0,
            clock=lambda: now[0],
        )
        first, _ = cache.get_or_build(
            "key",
            lambda: {"generated_at": "2026-07-13T10:00:00", "value": 1},
        )
        now[0] = 116.0
        second, _ = cache.get_or_build(
            "key",
            lambda: {"generated_at": "2026-07-13T10:01:00", "value": 1},
        )

        self.assertNotEqual(first.identity, second.identity)
        self.assertEqual(first.etag, second.etag)

    def test_cache_ttl_starts_after_slow_build_finishes(self) -> None:
        now = [100.0]
        calls = []
        cache = DashboardResponseCache(ttl_seconds=15, clock=lambda: now[0])

        def slow_builder():
            calls.append("build")
            now[0] = 120.0
            return {"value": 1}

        first, _ = cache.get_or_build("key", slow_builder)
        now[0] = 121.0
        second, status = cache.get_or_build(
            "key", lambda: calls.append("rebuild") or {"value": 2}
        )

        self.assertIs(first, second)
        self.assertEqual(status, "hit")
        self.assertEqual(calls, ["build"])

    def test_cache_evicts_old_entries_and_releases_key_locks(self) -> None:
        cache = DashboardResponseCache(ttl_seconds=15, max_entries=2)
        cache.get_or_build("one", lambda: {"value": 1})
        cache.get_or_build("two", lambda: {"value": 2})
        cache.get_or_build("three", lambda: {"value": 3})

        self.assertEqual(list(cache._entries), ["two", "three"])
        self.assertEqual(cache._key_locks, {})

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
        self.assertIn(
            "stale-while-revalidate=86400",
            response.headers["Cache-Control"],
        )
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

    def test_weak_or_listed_etag_returns_304(self) -> None:
        cache = DashboardResponseCache(ttl_seconds=15)
        entry, _ = cache.get_or_build("overview", lambda: {"ok": True})

        for condition in (f'"other", W/{entry.etag}', "*"):
            with self.subTest(condition=condition):
                response = build_http_response(
                    entry,
                    accept_encoding="gzip",
                    if_none_match=condition,
                    cache_status="hit",
                    request_id="request-list",
                    elapsed_ms=0.2,
                )
                self.assertEqual(response.status, 304)

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

    def test_same_cache_key_builds_only_once_concurrently(self) -> None:
        cache = DashboardResponseCache(ttl_seconds=15)
        build_started = threading.Event()
        release_build = threading.Event()
        calls = []
        results = []

        def builder():
            calls.append("build")
            build_started.set()
            release_build.wait(timeout=0.5)
            return {"resource": "shared"}

        first = threading.Thread(
            target=lambda: results.append(cache.get_or_build("shared", builder))
        )
        second = threading.Thread(
            target=lambda: results.append(cache.get_or_build("shared", builder))
        )
        first.start()
        self.assertTrue(build_started.wait(timeout=0.1))
        second.start()
        release_build.set()
        first.join(timeout=0.5)
        second.join(timeout=0.5)

        self.assertEqual(calls, ["build"])
        self.assertEqual(sorted(status for _, status in results), ["hit", "miss"])
        self.assertIs(results[0][0], results[1][0])
        self.assertEqual(cache._key_locks, {})

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
