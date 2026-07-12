# Monthly Strategy Evolution

Use the optional argument as `month=YYYY-MM`; otherwise select the previous
natural month.

1. Read `AGENTS.md`, the repository workflow skill, and the target monthly
   review. Gather both active markets for both strategy slots.
2. Compare net return, benchmark excess, Sharpe, drawdown, volatility, turnover,
   costs, holdings overlap, return correlation, factor diagnostics, QDII
   selection quality, and data gaps.
3. Preserve `稳健防守` and `趋势进攻` as materially different hypotheses.
4. Record a no-change decision when evidence is insufficient. Do not change a
   strategy merely because a month has ended.
5. For a real change, create an immutable four-overlay release manifest under
   `configs/strategy_versions/<release>/manifest.json`.
6. Apply it atomically with `python3 -m stock_analyze apply-strategy-release`.
7. Run both market pair guards, focused tests, full tests, and A-share historical
   gates. A failed gate means no active overlay is published.
8. Deploy in the two phases documented in `AGENTS.md`, then verify the deployed
   version, four ECS accounts, timers, dashboard APIs, and notification status.
9. Report the target month, no-change/change decision, exact diffs, gate metrics,
   release ID, and ECS verification.
