# QDII Global Context PIT Contract

## Purpose

This contract repairs missing QDII global-index and USD/CNH history without
fabricating a product-index return. The machine-readable source of truth is
`configs/research/qdii_global_context_v1.yaml`.

## Sources and window

Tushare `index_global` provides SPX, IXIC, DJI, and HSI daily OHLC history from
2018-01-01. Tushare `fx_daily` provides `USDCNH.FXCM` over the same window. Raw
rows, provider identifiers, collection time, checksums, row counts, date bounds,
and the mapping contract hash are persisted under
`data/research/qdii_global_context/v1/`.

The asset fails closed unless each index has at least 1,500 rows, USD/CNH has at
least 2,000 rows, required schemas are present, requested end coverage is
complete, and every file matches its manifest hash.

## Availability semantics

All source market observations become usable on the next calendar day. This is
conservative for both Hong Kong and US closes relative to an A-share signal at
15:00. Same-source-day values must never be visible to that day's mainland
decision. There is no future backfill and no filling before a series begins.

## Mapping semantics

Exact mappings are limited to S&P 500→SPX, Dow Jones Industrial Average→DJI,
and Hang Seng Index→HSI. Tushare returns no NDX, HSCEI, or HSTECH history under
the current entitlement. Nasdaq-family products therefore use IXIC and other US
or Hong Kong themes use SPX or HSI only as a declared `family_proxy`. Every
feature row retains `global_source_index_code`, `global_mapping_kind`,
`global_source_trade_date`, and `global_available_date`.
USD/CNH separately retains `fx_source_code`, `fx_source_trade_date`, and
`fx_available_date`.

These proxy values describe broad overnight market context. They must not be
described as the tracked index return and must not replace fund NAV, premium, or
tracking-error fields. An unknown `index_key` remains missing.

## Repair boundary

Repairing an existing QDII feature snapshot may replace only
`global_index_momentum`, `global_volatility`, `rmb_depreciation`, and the four
provenance columns above. Row identity, prices, technical features, NAV fields,
universe membership, labels, model registries, and formal account state are
immutable. The prior feature file is retained once under its SHA-256.
