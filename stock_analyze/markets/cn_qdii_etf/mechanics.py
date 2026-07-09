"""Trading mechanics for mainland-listed cross-border ETF paper trading."""

from __future__ import annotations


SETTLEMENT_DAYS: int = 1
DAILY_LIMIT_PCT: float | None = None
DEFAULT_LOT_SIZE: int = 100
ALLOW_SHORTING: bool = False
SHORTING_COLLATERAL_RATIO: float = 1.0
STAMP_TAX_RATE: float = 0.0
COMMISSION_RATE: float = 0.0003
SLIPPAGE_BPS: float = 5.0
TRADING_HOURS_TZ: str = "Asia/Shanghai"
MARKET_CLOSE_LOCAL: str = "15:00"
MARKET_CLOSE_BJT: str = "15:00"


def lot_size_for(code: str, default: int = DEFAULT_LOT_SIZE) -> int:
    """ETF secondary-market lot size is 100 shares for this MVP."""
    return default
