# Direct Overseas Simulation Archive

Direct Hong Kong and US paper accounts were retired from active runtime on
2026-07-11. The product now models instruments that can be bought through a
mainland securities account: A-shares and mainland-listed cross-border ETFs.

The original local yfinance runner and its source, configuration, documentation,
and tests are preserved under `source/` for audit. They are not importable from
the active Python package and yfinance is no longer a production dependency.
Historical ECS positions, reports, and caches were removed from active runtime
storage during the 2026-07-19 consolidation.

Restoring either market requires an explicit product decision, a current legal
and data-source review, re-enabling runtime dispatch, and fresh integration
tests. Do not invoke this archived runner as an operational command.
