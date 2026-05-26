## ADDED Requirements

### Requirement: Run ledger captures every CLI invocation

每次 CLI 命令（`init / rebalance / execute / update-nav / report / dashboard / run-daily / run-weekly`）SHALL 在 `data/runs.csv` 中记录 `run_id`、命令名、`as_of`、起止时间、耗时、状态、错误摘要、`config_hash`、`code_version`。

#### Scenario: Running command writes a starting row
- **WHEN** CLI 命令开始执行
- **THEN** `data/runs.csv` 追加一行 `status=running` 且 `finished_at`、`duration_ms`、`error_summary` 为空
- **AND** 该行的 `run_id` 是该次执行的稳定标识

#### Scenario: Successful command updates the status row
- **WHEN** CLI 命令正常退出
- **THEN** `data/runs.csv` 追加同 `run_id` 的一行 `status=success`，并填入 `finished_at` 与 `duration_ms`
- **AND** dashboard 与读取层按 `run_id` group 后取最新一行

#### Scenario: Failed command records error summary
- **GIVEN** 命令在执行中抛出异常
- **WHEN** CLI 退出
- **THEN** `data/runs.csv` 追加 `status=failed` 行，`error_summary` 字段填入异常类型与首行消息（最多 300 字符）
- **AND** 异常仍按原行为向上抛或转为非零返回码（与现有行为一致）

#### Scenario: Ledger write failures do not break the command
- **GIVEN** 磁盘只读或 IO 异常
- **WHEN** 账本写入失败
- **THEN** 主命令继续执行，账本失败被记录到 `data_health.json`
- **AND** 不向用户抛出额外异常

### Requirement: Config snapshot keyed by hash

策略加载后 SHALL 计算 `config_hash = sha256(canonical_json(config))[:12]`；当 `data/configs/<hash>.json` 不存在时 SHALL 把当前完整配置写入该文件。

#### Scenario: New config hash creates a snapshot
- **GIVEN** 用户修改 `configs/strategy_v1.yaml` 中任一字段
- **WHEN** 下一次运行
- **THEN** `config_hash` 与上一次不同
- **AND** `data/configs/<new_hash>.json` 被新建

#### Scenario: Identical config does not duplicate snapshot
- **GIVEN** 已存在 `data/configs/<hash>.json`
- **WHEN** 同一份配置再次运行
- **THEN** 不会重新写入该文件
- **AND** `runs.csv` 中该次运行的 `config_hash` 仍能查到对应快照

### Requirement: Code version capture

`runs.csv` 的 `code_version` SHALL 在 git 仓库内取当前 HEAD 的 7 位短 SHA；不在 git 仓库或 git 不可用时填 `no_git`。

#### Scenario: Inside a git repo
- **GIVEN** 工作目录是 git 仓库且 HEAD 可解析
- **WHEN** 命令运行
- **THEN** `code_version` 是 HEAD 短 SHA
- **AND** 不依赖外部 `git` 二进制可用（可通过读 `.git/HEAD` 与 `.git/refs/heads/<branch>` 实现）

#### Scenario: Outside a git repo
- **GIVEN** 工作目录不是 git 仓库
- **WHEN** 命令运行
- **THEN** `code_version` 是 `no_git`
- **AND** 不打印额外 warning，避免噪音

### Requirement: Dashboard run ledger panel

Dashboard SHALL 在底部渲染最近 ≤ 10 次运行，列出 `run_id`、命令、`status`、耗时、`config_hash`、`code_version`。

#### Scenario: Latest 10 runs are visible
- **GIVEN** `runs.csv` 中已有超过 10 条记录
- **WHEN** dashboard 生成
- **THEN** 仅渲染最近 10 条（按 `started_at` 倒序），按 `run_id` group 后取每个 run 的最终状态
- **AND** 失败的运行用红色标签提示

#### Scenario: Weekly report metadata references run id
- **WHEN** `run-weekly` 生成 `reports/weekly_report.md`
- **THEN** 报告顶部出现一行 metadata：`run_id`、`config_hash`、`code_version`
- **AND** 该 `run_id` 与同次运行写入 `runs.csv` 的值一致
