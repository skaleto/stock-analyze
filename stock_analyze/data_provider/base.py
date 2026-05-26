"""Shared types, exceptions, and constants for the data-provider package.

Extracted from ``data_provider/__init__.py`` as part of the I2 split
(2026-05-26 audit). These symbols are small, isolated, and have no
dependencies on the abstract provider class — so moving them is safe and
unblocks future per-vendor extracts.

The abstract :class:`DataProvider` class itself and the concrete
``TushareProvider`` / ``BaostockProvider`` / ``AkshareProvider`` subclasses
remain in ``data_provider/__init__.py`` for now (extracting them carries
non-trivial import-order risk because they reference each other and the
helpers in the package root).

Public symbols are re-exported from ``stock_analyze.data_provider`` so any
caller doing ``from stock_analyze.data_provider import PriceSnapshot``
keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


# Index code → 6-digit canonical form lookup. Used everywhere benchmark
# data is referenced (config files, store dtype hints, dashboard panels).
INDEX_CODES = {
    "hs300": "000300",
    "zz500": "000905",
    "zz1000": "000852",
    "cyb": "399006",
    "kcb": "000688",
}

# Tushare requires the exchange suffix on every ts_code; this map covers the
# four common index codes we care about. ``ts_code_for_index`` extends this to
# raw stock codes by deriving the suffix from the first digit.
INDEX_TUSHARE_SUFFIX = {
    "000300": "SH",
    "000905": "SH",
    "000852": "SH",
    "000688": "SH",
    "399006": "SZ",
}

RETRY_DELAYS = [2.0, 5.0, 10.0]
TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"

# Sleep between consecutive Tushare HTTP calls to keep us well below the
# 200-calls-per-minute ceiling at the 2000-credit tier. The largest burst
# we issue is per-stock ``fina_indicator`` during prepare-market-data
# (~800 codes), where 0.35s × 800 ≈ 4.7 min — still under the budget.
TUSHARE_RATE_SLEEP_S = 0.35


@dataclass
class PriceSnapshot:
    code: str
    trade_date: str | None
    close: float | None
    open: float | None
    high: float | None
    low: float | None
    amount: float | None
    momentum_20: float | None
    momentum_60: float | None
    avg_amount_20: float | None
    low_volatility_60: float | None = None
    paused: bool = False
    limit_up: bool = False
    limit_down: bool = False
    source: str = ""
    warning: str = ""


@dataclass
class ExecutionQuote:
    code: str
    trade_date: str | None
    price: float | None
    paused: bool = False
    limit_up: bool = False
    limit_down: bool = False
    source: str = ""
    reason: str = ""


class CacheMiss(RuntimeError):
    """Raised when an offline provider call finds no cached entry.

    Carries enough metadata for callers / RunLedger to identify which method
    and which cache key failed without scraping the message text.
    """

    def __init__(self, method: str, cache_name: str) -> None:
        super().__init__(f"cache_miss:{method}:{cache_name}")
        self.method = method
        self.cache_name = cache_name


class TushareTokenMissing(RuntimeError):
    """Raised by :class:`TushareProvider` when no ``TUSHARE_TOKEN`` is set.

    The message intentionally avoids printing the env-var value so a
    stack-trace dump cannot leak the token. See ``docs/tushare-token-setup.md``
    for setup instructions.
    """

    def __init__(self) -> None:
        super().__init__(
            f"{TUSHARE_TOKEN_ENV} env var not set; see docs/tushare-token-setup.md"
        )
