## Why

The project has grown from a simple A-share screener into a forward simulation system with scheduled jobs, data-source fallback, dashboard reporting, and deployment assumptions. A written OpenSpec record is needed so another machine can pull the repository, recreate the runtime, and understand the simulated-trading boundary without relying on chat history.

## What Changes

- Document the forward simulation workflow: weekly signal generation, next-trading-day simulated execution, daily NAV refresh, reports, and dashboard.
- Document the public data-source strategy: AkShare as the primary provider, Tencent/Sina/Baostock/cache fallbacks where appropriate, and no real brokerage order placement.
- Document the minimum local runtime and server runtime needed to run the code from a fresh checkout.
- Add an operator runbook for local runs, ECS-style systemd deployment, verification, troubleshooting, and data-quality interpretation.
- Record the implemented reliability decisions, including retry behavior, fallback data sources, turnover unit normalization, and relaxed signal filters when strict data completeness would empty the pool.

## Capabilities

### New Capabilities

- `forward-simulation-runbook`: Documents how to install, run, verify, deploy, and operate the A-share forward simulation system.
- `data-source-resilience`: Documents the expected behavior for realtime, historical, valuation, financial, cache, and Baostock fallback data.

### Modified Capabilities

- None.

## Impact

- Documentation: OpenSpec proposal/design/spec/tasks plus a user-facing runtime runbook.
- Code contracts documented: `stock_analyze/cli.py`, `stock_analyze/data_provider.py`, `stock_analyze/strategy.py`, `stock_analyze/simulator.py`, `stock_analyze/reporting.py`, and `configs/strategy_v1.yaml`.
- Runtime dependencies documented: Python 3.10+, `akshare>=1.18.62`, `baostock>=0.9.1`, `pandas`, `numpy`, network access to public market-data providers, and optional systemd for Linux servers.
