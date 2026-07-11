# Direct Overseas Simulation Archive

Direct Hong Kong and US paper accounts were retired from active runtime on
2026-07-11. The product now models instruments that can be bought through a
mainland securities account: A-shares and mainland-listed cross-border ETFs.

The original local yfinance runner is preserved here for audit. The market
source packages, configs, reports, and historical data also remain in Git or on
disk, but `competition.MARKETS`, the CLI, Dashboard, and ECS sync workflow no
longer dispatch or publish `hk` and `us` accounts.

Restoring either market requires an explicit product decision, a current legal
and data-source review, re-enabling runtime dispatch, and fresh integration
tests. Do not invoke this archived runner as an operational command.
