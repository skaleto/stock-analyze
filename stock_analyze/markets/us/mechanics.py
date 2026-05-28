"""US trading mechanics constants.

All values lifted from the Phase 1 design spec
(``docs/superpowers/specs/2026-05-27-multi-market-competition-design.md`` §2).
"""

from __future__ import annotations


SETTLEMENT_DAYS: int = 1          # T+1 (since May 2024)
DAILY_LIMIT_PCT: float | None = None   # No daily limit in US
DEFAULT_LOT_SIZE: int = 1         # Any whole share
ALLOW_SHORTING: bool = True       # Simplified shorting allowed
SHORTING_COLLATERAL_RATIO: float = 1.0  # 100% cash collateral
STAMP_TAX_RATE: float = 0.0       # No US stamp tax (FINRA fees are tiny, ignored)
COMMISSION_RATE: float = 0.0      # Commission-free retail
SLIPPAGE_BPS: float = 3.0         # Tighter than HK/A-share (deeper US books)
TRADING_HOURS_TZ: str = "America/New_York"
MARKET_CLOSE_LOCAL: str = "16:00"
MARKET_CLOSE_BJT: str = "04:00"   # +1 day; DST-aware (04:00 summer / 05:00 winter)


def lot_size_for(code: str, default: int = DEFAULT_LOT_SIZE) -> int:
    """US lot size is 1 for any whole share. v1 doesn't support fractional."""
    return default
