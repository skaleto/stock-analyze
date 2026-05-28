"""A-share market implementation.

Houses everything specific to the Shanghai + Shenzhen exchanges that
was previously at the top level of ``stock_analyze/``. Public surface
mirrors what HK and US subpackages must also expose:

    make_provider, execute_due_orders, update_nav,
    generate_rebalance_orders, initialize, build_signals.

Per-market mechanics constants live in a ``mechanics.py`` companion
module (to be added in a follow-up task when the modules are moved
into this package).
"""

from .data_provider import make_provider
from .simulator import (
    execute_due_orders,
    generate_rebalance_orders,
    initialize,
    update_nav,
)
from .strategy import build_signals

__all__ = [
    "build_signals",
    "execute_due_orders",
    "generate_rebalance_orders",
    "initialize",
    "make_provider",
    "update_nav",
]
