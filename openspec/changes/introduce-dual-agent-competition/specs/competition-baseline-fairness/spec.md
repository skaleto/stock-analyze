## ADDED Requirements

### Requirement: Shared baseline config

仓库 SHALL 提供 `configs/competition.yaml` 作为双 agent 共享的基线配置，包含 `competition_id`、`start_date`、`initial_cash`、`accounts`、`schedule`、`trading`、`performance` 全部字段。

#### Scenario: Baseline file exists with required fields
- **GIVEN** 仓库根目录
- **WHEN** 读取 `configs/competition.yaml`
- **THEN** 文件存在
- **AND** 顶层包含 `competition_id, start_date, initial_cash, accounts, schedule, trading, performance` 七个键

#### Scenario: Baseline accounts encode the locked fairness setup
- **WHEN** 读取 `configs/competition.yaml.accounts`
- **THEN** 至少存在两个账户 `hs300` 与 `zz500`
- **AND** 每个账户含 `scope, benchmark, cash, top_n`
- **AND** 总 cash 等于 `initial_cash`

### Requirement: Per-agent overlay config

每个参赛 agent SHALL 在 `configs/agents/<agent_id>.yaml` 提供自己的 overlay；overlay 只允许包含 `agent_id, strategy_id, factors, factor_processing, portfolio_controls, filters` 子集。

#### Scenario: Two agent overlays exist for MVP
- **WHEN** 读取 `configs/agents/`
- **THEN** 至少存在 `claude.yaml` 与 `codex.yaml`
- **AND** 两份文件均含 `agent_id` 与 `strategy_id`
- **AND** `agent_id` 与文件名 stem 一致

### Requirement: Locked baseline fields cannot be overridden

`stock_analyze/competition.py` 在加载 baseline + overlay 后 SHALL 强制检查"锁字段"未被 overlay 覆盖；覆盖时 `raise CompetitionBaselineLocked(field=<path>)`。锁路径包含：`initial_cash`, `accounts.*.cash`, `accounts.*.top_n`, `accounts.*.scope`, `accounts.*.benchmark`, `trading.*`, `schedule.execution`, `start_date`。

#### Scenario: Overlay attempts to override initial_cash
- **GIVEN** `configs/agents/codex.yaml` 含 `initial_cash: 2000000`
- **WHEN** 调用 `competition.load("codex")`
- **THEN** raise `CompetitionBaselineLocked`
- **AND** 异常的 `field` 属性等于 `initial_cash`

#### Scenario: Overlay attempts to override accounts.hs300.cash
- **GIVEN** overlay 含 `accounts: [{id: "hs300", cash: 600000}]`
- **WHEN** 调用 `competition.load("codex")`
- **THEN** raise `CompetitionBaselineLocked`
- **AND** 异常的 `field` 属性等于 `accounts.hs300.cash`

#### Scenario: Overlay overrides factors freely
- **GIVEN** overlay 含 `factors.{pe: {weight: 0.5}}`
- **WHEN** 调用 `competition.load("codex")`
- **THEN** 合并后的 config 含 `factors.pe.weight=0.5`
- **AND** 不抛异常

#### Scenario: Overlay overrides trading.commission_rate
- **GIVEN** overlay 含 `trading: {commission_rate: 0}`
- **WHEN** 调用 `competition.load("codex")`
- **THEN** raise `CompetitionBaselineLocked`
- **AND** 异常的 `field` 等于 `trading.commission_rate`

### Requirement: Competition init command

CLI SHALL 提供 `competition-init` 子命令，幂等地完成：检查 baseline + overlay 存在 → 创建 `data/{shared,claude,codex,competition}/` 与 `reports/{claude,codex,competition}/` → 调用 `simulator.initialize(merged_config, store)` 初始化每个 agent 的 `state.json` 与 `pending_orders.json` → 写 `data/competition/competition_metadata.json`。

#### Scenario: First competition-init creates all required directories
- **GIVEN** 一个空的 `data/` 与 `reports/`
- **WHEN** 运行 `python3 -m stock_analyze competition-init`
- **THEN** `data/shared`, `data/claude`, `data/codex`, `data/competition` 全部存在
- **AND** `reports/claude`, `reports/codex`, `reports/competition` 全部存在
- **AND** `data/claude/state.json` 与 `data/codex/state.json` 都含有 `accounts.hs300.cash=500000` 与 `accounts.zz500.cash=500000`
- **AND** `data/competition/competition_metadata.json` 含 `competition_id`、`start_date`、`baseline_hash`

#### Scenario: competition-init is idempotent for already-initialized agents
- **GIVEN** `competition-init` 已经成功跑过一次
- **WHEN** 再次运行 `competition-init`
- **THEN** 已存在的 `state.json` 不被重置
- **AND** 命令以 0 退出码结束
