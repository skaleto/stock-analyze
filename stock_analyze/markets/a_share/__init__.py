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
