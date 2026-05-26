## ADDED Requirements

### Requirement: Baseline top_n raised to 50

`configs/competition.yaml` 中所有账户的 `top_n` SHALL 等于 `50`；`trading.max_single_weight` SHALL 等于 `0.05`。两项一起构成新的 baseline，不可在 overlay 中覆盖。

#### Scenario: Baseline accounts now hold 50 each
- **WHEN** 读取 `configs/competition.yaml.accounts`
- **THEN** 每个账户的 `top_n` 等于 `50`
- **AND** 总账户数仍是两个（hs300 + zz500），合计目标持仓 100 只

#### Scenario: max_single_weight aligned with new equal weight
- **WHEN** 读取 `configs/competition.yaml.trading.max_single_weight`
- **THEN** 值等于 `0.05`
- **AND** 该值 ≥ 单账户等权目标 `1 / top_n` = 0.02

#### Scenario: Overlay still cannot override top_n
- **GIVEN** `configs/agents/codex.yaml` 试图在 `accounts` 中重新声明 `top_n=30`
- **WHEN** 加载层运行
- **THEN** raise `CompetitionBaselineLocked`
- **AND** 异常的 `field` 涉及 `accounts.*.top_n` 或 `overlay_top_level:accounts`

### Requirement: Agent overlays widen the candidate funnel

每个 agent 的 `filters.max_fetch_candidates` SHALL 至少为 `250`，给 50 名头部留足 5× 漏斗深度。

#### Scenario: Claude overlay max_fetch_candidates ≥ 250
- **WHEN** 读取 `configs/agents/claude.yaml.filters.max_fetch_candidates`
- **THEN** 值不小于 `250`

#### Scenario: Codex overlay max_fetch_candidates ≥ 250
- **WHEN** 读取 `configs/agents/codex.yaml.filters.max_fetch_candidates`
- **THEN** 值不小于 `250`

### Requirement: Existing state survives the capacity bump

升级 `top_n` SHALL 不要求 `competition-init` 重置；既有 `state.json` 与 `daily_nav.csv` SHALL 继续有效，下一次 `run-weekly --agent <id>` SHALL 按新 `top_n` 自动选股并对齐既有持仓。

#### Scenario: Old state.json is reusable
- **GIVEN** `data/<agent>/state.json` 在 `top_n=10` 时代生成，记录现有 10 只持仓
- **WHEN** 运行新版本的 `python3 -m stock_analyze --agent <id> run-weekly`
- **THEN** `build_target_orders` 输出包含买入新增 40 只股票 + 可能卖出排名跌出 50 的旧持仓
- **AND** `account.cash` 与既有 positions 不被强制重置
