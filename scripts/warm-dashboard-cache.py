#!/usr/bin/env python3
"""Warm bounded Dashboard snapshots after the HTTP service starts."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Iterable, Sequence
from urllib.request import urlopen


MARKETS = ("a_share", "cn_qdii_etf")
AGENTS = ("claude", "codex")
PRIMARY_STRATEGY_RESOURCES = ("overview", "performance", "portfolio")


class WarmResult:
    __slots__ = ("endpoint", "ok", "status", "size", "elapsed", "error")

    def __init__(
        self,
        *,
        endpoint: str,
        ok: bool,
        status: int | None,
        size: int,
        elapsed: float,
        error: str = "",
    ) -> None:
        self.endpoint = endpoint
        self.ok = ok
        self.status = status
        self.size = size
        self.elapsed = elapsed
        self.error = error


def dashboard_endpoints() -> tuple[str, ...]:
    endpoints = [
        "/api/dashboard/summary.json",
        "/api/dashboard/system-overview.json",
        "/api/dashboard/operations-center.json?scope=all",
    ]
    for market in MARKETS:
        endpoints.extend(
            (
                f"/api/dashboard/model-research.json?market={market}",
                f"/api/dashboard/data-intelligence.json?market={market}",
            )
        )
    for market in MARKETS:
        for agent in AGENTS:
            prefix = f"market={market}&agent={agent}"
            endpoints.extend(
                f"/api/dashboard/{resource}.json?{prefix}"
                for resource in PRIMARY_STRATEGY_RESOURCES
            )
            endpoints.append(
                "/api/dashboard/predictions.json?"
                f"{prefix}&limit_per_horizon=12"
            )
    return tuple(endpoints)


def warm_endpoints(
    base_url: str,
    endpoints: Iterable[str],
    *,
    timeout: float,
    opener: Callable[..., object] = urlopen,
) -> list[WarmResult]:
    base = base_url.rstrip("/")
    results: list[WarmResult] = []
    for endpoint in endpoints:
        started = time.monotonic()
        try:
            with opener(f"{base}{endpoint}", timeout=timeout) as response:
                payload = response.read()
                status = int(getattr(response, "status", 200))
            results.append(
                WarmResult(
                    endpoint=endpoint,
                    ok=200 <= status < 300,
                    status=status,
                    size=len(payload),
                    elapsed=time.monotonic() - started,
                )
            )
        except Exception as error:  # Every endpoint is deliberately non-fatal.
            results.append(
                WarmResult(
                    endpoint=endpoint,
                    ok=False,
                    status=None,
                    size=0,
                    elapsed=time.monotonic() - started,
                    error=f"{type(error).__name__}: {error}",
                )
            )
    return results


def _warm_when_ready(
    base_url: str,
    endpoints: Sequence[str],
    *,
    timeout: float,
    attempts: int,
    retry_delay: float,
) -> list[WarmResult]:
    first = endpoints[0]
    first_result: WarmResult | None = None
    for attempt in range(1, attempts + 1):
        first_result = warm_endpoints(
            base_url,
            (first,),
            timeout=timeout,
        )[0]
        if first_result.ok:
            break
        if attempt < attempts:
            time.sleep(retry_delay)
    assert first_result is not None
    if not first_result.ok:
        return [first_result]
    return [first_result, *warm_endpoints(
        base_url,
        endpoints[1:],
        timeout=timeout,
    )]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--ready-attempts", type=int, default=60)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return non-zero when any warm request fails",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    results = _warm_when_ready(
        args.base_url,
        dashboard_endpoints(),
        timeout=max(args.timeout, 0.1),
        attempts=max(args.ready_attempts, 1),
        retry_delay=max(args.retry_delay, 0.0),
    )
    print("endpoint\tstatus\tbytes\telapsed_seconds\terror")
    for item in results:
        print(
            f"{item.endpoint}\t{item.status or 'ERROR'}\t{item.size}\t"
            f"{item.elapsed:.3f}\t{item.error}"
        )
    failures = sum(not item.ok for item in results)
    print(f"dashboard_cache_warm total={len(results)} failures={failures}")
    return 1 if args.strict and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
