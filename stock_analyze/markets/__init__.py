"""Per-market subpackages.

Each subpackage (a_share, hk, us, cn_qdii_etf) is self-contained: data_provider +
simulator + strategy + universe + mechanics. Shared logic
(factor_pipeline, overlay_guard, evolution_writer, etc.) stays at the
top level of stock_analyze/ and accepts a ``market`` parameter for
dispatch.

The list of supported markets lives in stock_analyze.competition.MARKETS.
"""
