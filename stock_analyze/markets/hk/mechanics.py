"""HK trading mechanics constants.

All values lifted from the Phase 1 design spec
(``docs/superpowers/specs/2026-05-27-multi-market-competition-design.md`` §2).
HK / A-share differences are concentrated here so simulator + strategy
can stay market-agnostic via lookups into this module.
"""

from __future__ import annotations


SETTLEMENT_DAYS: int = 2          # T+2 (vs A-share T+1)
DAILY_LIMIT_PCT: float | None = None   # No daily limit in HK
DEFAULT_LOT_SIZE: int = 100       # Fallback when per-stock lot unknown
ALLOW_SHORTING: bool = True       # Simplified shorting allowed
SHORTING_COLLATERAL_RATIO: float = 1.0  # 100% cash collateral
STAMP_TAX_RATE: float = 0.0013    # HK stamp duty (0.13% both sides)
COMMISSION_RATE: float = 0.0003   # Broker commission
SLIPPAGE_BPS: float = 5.0         # Same as A-share for v1
TRADING_HOURS_TZ: str = "Asia/Hong_Kong"
MARKET_CLOSE_LOCAL: str = "16:00"
MARKET_CLOSE_BJT: str = "16:00"   # HK is in the same timezone as Beijing


def lot_size_for(code: str, default: int = DEFAULT_LOT_SIZE) -> int:
    """Return the lot size for an HK stock code.

    Phase 2 (v1) returns the default 100 for every stock. A future
    enhancement would consult yfinance ``info.lotSize`` per stock and
    cache the result; for v1 we use the most common HK lot size as the
    fallback and accept some sizing imprecision on the variable-lot
    long tail.
    """
    return default
