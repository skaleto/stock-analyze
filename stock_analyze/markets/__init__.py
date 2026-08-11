"""Per-market subpackages.

Active subpackages are ``a_share`` and ``cn_qdii_etf``. Retired direct ``hk``
and ``us`` implementations live under ``archive/direct-overseas`` and are
absent from runtime dispatch. Shared logic stays at the top level of
``stock_analyze`` and accepts a ``market`` parameter for dispatch.

The active and archived lists live in ``stock_analyze.competition``.
"""
