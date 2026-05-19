## ADDED Requirements

### Requirement: CLI --agent flag

`stock_analyze/cli.py` SHALL 支持顶层 `--agent <agent_id>` 参数。提供 `--agent` 时，CLI 自动解析配置文件、数据目录、报告目录，等价于：

- `--config configs/agents/<agent_id>.yaml`（经 `competition.load(agent_id)` 合并 baseline）
- `--data-dir data/<agent_id>`
- `--reports-dir reports/<agent_id>`
- 共享缓存目录指向 `data/shared/cache`

#### Scenario: --agent claude resolves paths
- **GIVEN** `configs/agents/claude.yaml` 与 `configs/competition.yaml` 存在
- **WHEN** 运行 `python3 -m stock_analyze --agent claude run-weekly`
- **THEN** 命令使用 `data/claude/` 作为数据目录
- **AND** 使用 `reports/claude/` 作为报告目录
- **AND** AkshareProvider 的 cache_dir 指向 `data/shared/cache`
- **AND** 加载的 config 是 baseline + claude overlay 的合并结果

#### Scenario: --agent codex resolves paths
- **GIVEN** `configs/agents/codex.yaml` 与 `configs/competition.yaml` 存在
- **WHEN** 运行 `python3 -m stock_analyze --agent codex run-weekly`
- **THEN** 命令使用 `data/codex/` 与 `reports/codex/`
- **AND** AkshareProvider 的 cache_dir 仍指向 `data/shared/cache`

#### Scenario: --agent and explicit --config conflict resolution
- **GIVEN** 用户同时提供 `--agent claude` 与 `--config configs/strategy_v1.yaml`
- **WHEN** 命令执行
- **THEN** 显式 `--config` 优先生效
- **AND** 命令在 stderr 写一行 warning `--agent ignored because --config is explicit`

### Requirement: Backward compatibility for single-agent mode

CLI SHALL 在不带 `--agent` 时保留既有行为：使用 `--config configs/strategy_v1.yaml`、`--data-dir data`、`--reports-dir reports` 作为默认，不要求 `configs/competition.yaml` 存在。

#### Scenario: Single-agent run with default paths
- **GIVEN** 没有 `configs/competition.yaml`
- **WHEN** 运行 `python3 -m stock_analyze run-weekly`（不带 `--agent`）
- **THEN** 命令使用 `configs/strategy_v1.yaml`、`data/`、`reports/`
- **AND** 不要求竞赛模式产物存在

#### Scenario: Existing P0/P1 tests continue to pass
- **WHEN** 在没有 `configs/competition.yaml` 与 `configs/agents/*` 的仓库快照上运行单 agent 单元测试
- **THEN** 既有测试套件继续全绿

### Requirement: Shared cache namespace

竞赛模式下，AkshareProvider SHALL 把价格历史、估值、财务、指数成分等公开数据缓存写到 `data/shared/cache/`，并把 `data_health.json` 写到 `data/shared/data_health.json`。

#### Scenario: Two agents share cache writes
- **GIVEN** 已经 `competition-init`
- **WHEN** `--agent claude run-weekly` 运行后，再运行 `--agent codex run-weekly`
- **THEN** 第二次运行命中相同股票的缓存
- **AND** `data/shared/cache/history_*.csv` 仅写入一次（按文件 mtime 判断）

#### Scenario: Health log is shared
- **WHEN** 两侧分别跑过 `run-weekly`
- **THEN** `data/shared/data_health.json` 同时包含来自双侧的健康记录
- **AND** `data/claude/data_health.json` 与 `data/codex/data_health.json` 不被创建

### Requirement: Agent path resolution helper

`stock_analyze/competition.resolve_agent_paths(agent_id)` SHALL 返回 `AgentPaths` 对象，含 `agent_id, config_path, data_dir, reports_dir, shared_cache_dir`，CLI 与测试均使用该 helper。

#### Scenario: resolve_agent_paths returns expected fields
- **WHEN** 调用 `competition.resolve_agent_paths("claude")`
- **THEN** 返回值的 `agent_id="claude"`, `config_path` 指向 `configs/agents/claude.yaml`, `data_dir` 指向 `data/claude`, `reports_dir` 指向 `reports/claude`, `shared_cache_dir` 指向 `data/shared/cache`

#### Scenario: Unknown agent raises
- **WHEN** 调用 `competition.resolve_agent_paths("unknown")`
- **THEN** raise `KeyError`（或更专属的 `UnknownAgent`）
- **AND** 异常消息列出所有合法 agent_id
