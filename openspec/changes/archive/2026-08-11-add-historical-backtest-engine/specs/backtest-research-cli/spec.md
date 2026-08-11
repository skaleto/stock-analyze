## ADDED Requirements

### Requirement: CLI provides `backtest` subcommand for arbitrary-window research

`python3 -m stock_analyze backtest` SHALL accept the following options and dispatch to `stock_analyze.backtest.engine.run_backtest`:

- `--agent {claude,codex}` — required. Determines the default `out_dir` prefix (`data/<agent>/backtest/`) and is recorded in the run's `meta.json`.
- `--start YYYY-MM-DD` — required. Start of the backtest window (inclusive).
- `--end YYYY-MM-DD` — required. End of the backtest window (inclusive); must be `>= --start`.
- `--overlay PATH` — required. Path to an overlay YAML file (typically `configs/agents/<agent>.yaml` or a hand-crafted YAML).
- `--output PATH` — required. Directory into which `daily_nav.csv`, `trades.csv`, `signals.csv`, `factor_runs/`, `performance_summary.json`, `report.md`, and `meta.json` are written.
- `--in-memory` — optional flag. When set, skips per-day disk writes (results returned via `BacktestResult`, not persisted to `daily_nav.csv`/`trades.csv`). Default off (disk-persisted).
- `--universe {hs300,zz500,both}` — optional. Default `both`. Restricts the candidate stock pool.
- `--cache-root PATH` — optional. Default `data/shared/backtest_cache`. Where to read historical market data from.

The CLI SHALL parse arguments, load and validate the overlay (via `overlay_guard.validate` on the agent's identity if the overlay is the live agent yaml), invoke `engine.run_backtest`, render `report.md` via `report.render_markdown_report`, and exit with code 0 on success.

#### Scenario: CLI parses required arguments and dispatches to engine

- **GIVEN** a valid overlay at `configs/agents/claude.yaml` and a prepared cache under `data/shared/backtest_cache/`
- **WHEN** the operator runs `python3 -m stock_analyze backtest --agent claude --start 2023-01-01 --end 2024-12-31 --overlay configs/agents/claude.yaml --output /tmp/bt_run`
- **THEN** the CLI parses `start=date(2023, 1, 1)`, `end=date(2024, 12, 31)`, `agent='claude'`, `overlay=Path('configs/agents/claude.yaml')`, `output=Path('/tmp/bt_run')`
- **AND** `engine.run_backtest(overlay=<loaded>, start=date(2023, 1, 1), end=date(2024, 12, 31), universe=['hs300', 'zz500'], market_data_root=Path('data/shared/backtest_cache'), out_dir=Path('/tmp/bt_run'), in_memory=False)` is invoked exactly once
- **AND** the process exits with code 0

#### Scenario: `--in-memory` flag is forwarded to engine

- **WHEN** the operator runs `python3 -m stock_analyze backtest --agent claude --start 2023-01-01 --end 2023-01-31 --overlay configs/agents/claude.yaml --output /tmp/bt --in-memory`
- **THEN** `engine.run_backtest` is invoked with `in_memory=True`

#### Scenario: `--universe hs300` restricts the candidate pool

- **WHEN** the operator passes `--universe hs300`
- **THEN** `engine.run_backtest` is invoked with `universe=['hs300']`
- **AND** when the operator passes `--universe both` (or omits `--universe`), `universe=['hs300', 'zz500']` is used

### Requirement: CLI validates inputs and reports errors clearly

The CLI SHALL validate inputs before dispatching to the engine and SHALL exit with a non-zero status and a human-readable stderr message when:

- `--start > --end` → exit 2 with message naming both dates.
- `--overlay` path does not exist → exit 2 with the missing path.
- `--cache-root` does not exist or is missing required subdirectories (`daily/`, `trade_cal.csv`) → exit 2 with a hint to run `prepare-backtest-data`.
- The overlay fails `overlay_guard.validate` (when validated as the named `--agent`'s overlay) → exit 1 with the guard's error string.

#### Scenario: Bad date order rejected before engine call

- **WHEN** the operator runs `python3 -m stock_analyze backtest --agent claude --start 2024-12-31 --end 2023-01-01 --overlay configs/agents/claude.yaml --output /tmp/bt`
- **THEN** the CLI prints an error message to stderr referencing both `2024-12-31` and `2023-01-01`
- **AND** the process exits with code 2
- **AND** `engine.run_backtest` is not called

#### Scenario: Missing cache root surfaces a prepare-backtest-data hint

- **GIVEN** the directory `data/shared/backtest_cache/` does not exist
- **WHEN** the operator runs `python3 -m stock_analyze backtest --agent claude --start 2023-01-01 --end 2023-01-31 --overlay configs/agents/claude.yaml --output /tmp/bt`
- **THEN** stderr contains the string `prepare-backtest-data` and references `data/shared/backtest_cache`
- **AND** the process exits with code 2

### Requirement: CLI persists the run with self-describing artifacts

After the engine returns, the CLI SHALL ensure the `--output` directory contains, in addition to the engine's data files:

- `report.md` — human-readable markdown report rendered via `report.render_markdown_report`.
- `meta.json` — a JSON file recording the resolved CLI args, the overlay snapshot, the current `git` SHA, and the wall-clock duration of the run.

#### Scenario: CLI writes report.md and meta.json after run

- **GIVEN** a successful backtest run finishes in 4.2 seconds
- **WHEN** the engine returns and the CLI finalises
- **THEN** `out_dir/report.md` exists and contains the markdown sections `## 总结`, `## 因子贡献分解`, `## 月度热力图`, `## 风险归因`
- **AND** `out_dir/meta.json` exists with keys `cmd`, `overlay`, `git_sha`, `duration_sec`
- **AND** `meta.json.duration_sec` is approximately `4.2`

### Requirement: `prepare-backtest-data` subcommand fetches historical data idempotently

`python3 -m stock_analyze prepare-backtest-data` SHALL accept `--start YYYY-MM-DD`, `--end YYYY-MM-DD`, `--cache-root PATH` (default `data/shared/backtest_cache`), and `--force` (optional). It SHALL dispatch to `stock_analyze.backtest.data_prep.prepare_backtest_data`, which performs a one-time batch fetch from Tushare Pro into `cache_root/{daily,daily_basic,fina_indicator,index_weight,adj_factor,stock_basic.csv,trade_cal.csv}` and is idempotent — already-fetched dates / codes are skipped via `_meta.json` unless `--force` is passed.

#### Scenario: CLI dispatches to `prepare_backtest_data` with parsed args

- **WHEN** the operator runs `python3 -m stock_analyze prepare-backtest-data --start 2021-01-01 --end 2026-04-30`
- **THEN** `data_prep.prepare_backtest_data(start=date(2021, 1, 1), end=date(2026, 4, 30), cache_root=Path('data/shared/backtest_cache'), force=False)` is invoked exactly once
- **AND** the process exits with code 0 when the fetch completes

#### Scenario: `--force` flag is forwarded

- **WHEN** the operator runs `python3 -m stock_analyze prepare-backtest-data --start 2021-01-01 --end 2021-01-31 --force`
- **THEN** `data_prep.prepare_backtest_data` is invoked with `force=True`
