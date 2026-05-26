## ADDED Requirements

### Requirement: prepare-market-data CLI subcommand

CLI SHALL 提供 `prepare-market-data` 子命令，独立于任何 agent 运行；它负责拉取竞赛所需的全部外部数据并写入 `data/shared/cache/`，写一份 `data/shared/market_snapshot_<as_of>.json` 元数据，并通过 `RunLedger` 在 `data/shared/runs.csv` 留痕。

#### Scenario: Default invocation fetches today's snapshot for configured scopes
- **GIVEN** `configs/competition.yaml.accounts` 含 hs300 与 zz500
- **WHEN** 运行 `python3 -m stock_analyze prepare-market-data`
- **THEN** 命令以今日 (`date.today()`) 为 `as_of`
- **AND** 拉取范围覆盖 hs300 与 zz500 两个 scope 的候选池
- **AND** 写入 `data/shared/market_snapshot_<today>.json`
- **AND** 在 `data/shared/runs.csv` 追加一行（`command=prepare-market-data`、`status=success`）

#### Scenario: Explicit --as-of and --scopes overrides defaults
- **WHEN** 运行 `python3 -m stock_analyze prepare-market-data --as-of 2026-05-22 --scopes hs300`
- **THEN** 只拉取 hs300 的候选
- **AND** snapshot 文件名为 `market_snapshot_2026-05-22.json`

#### Scenario: --force re-fetches even if today's snapshot exists
- **GIVEN** `data/shared/market_snapshot_<today>.json` 已存在
- **WHEN** 不带 `--force` 跑 `prepare-market-data`
- **THEN** 命令快速 return；不重新打网络；`runs.csv` 写 `status=success`、`error_summary=` 含 `snapshot_already_exists`
- **WHEN** 带 `--force` 跑
- **THEN** 命令重拉全部数据，覆盖 snapshot 文件

### Requirement: market_snapshot.json schema

每次 `prepare-market-data` 成功完成 SHALL 写一份 `data/shared/market_snapshot_<as_of>.json`，至少包含以下字段：`as_of`、`started_at`、`finished_at`、`duration_ms`、`scopes`、`candidates_fetched`、`rows` (各方法行数 / 计数)、`errors` (每条 `{code, method, message}`)、`fetch_summary` (`ok` / `retried` / `failed` 计数)。

#### Scenario: Snapshot fields are populated even on partial failure
- **GIVEN** 拉取过程中某只候选的 `financial_metrics` 失败
- **WHEN** prepare-market-data 完成
- **THEN** snapshot.errors 含一条 `{"code": "<code>", "method": "financial_metrics", "message": "..."}`
- **AND** snapshot.fetch_summary.failed ≥ 1
- **AND** 其它候选与方法的数据仍然落 cache

#### Scenario: Critical failure aborts and snapshot records the cause
- **GIVEN** `spot()` 全部源都失败
- **WHEN** prepare-market-data 跑
- **THEN** 命令以非零退出
- **AND** snapshot 仍被写入，含 `fetch_summary.aborted_at = "spot"`、`errors` 含 spot 失败原因
- **AND** `runs.csv` 写 `status=failed`

### Requirement: Concurrent candidate fetching

`prepare-market-data` SHALL 使用 `ThreadPoolExecutor(max_workers=5)` 并发拉候选股票数据，单只股票内的 5 个接口按顺序串行（避免单股触发限流），不同股票间并行。

#### Scenario: 250 candidates complete within reasonable time
- **GIVEN** 250 只候选股
- **WHEN** prepare-market-data 跑
- **THEN** 总耗时（无网络异常情况下）≤ 15 分钟
- **AND** `snapshot.duration_ms < 900_000`

### Requirement: Idempotency under repeated invocation

`prepare-market-data` SHALL 在同一 `as_of` + 无 `--force` 的情况下 idempotent：第二次调用不重打网络、不覆盖 snapshot。

#### Scenario: Second invocation on same day skips network
- **GIVEN** 上午跑过一次 `prepare-market-data --as-of 2026-05-22`
- **WHEN** 下午再跑同样命令
- **THEN** 网络调用次数为 0
- **AND** snapshot 文件不变（`stat -c %Y` mtime 与上次相同）
- **AND** `runs.csv` 多一行，`status=skipped`、`error_summary=snapshot_already_exists`
