## ADDED Requirements

### Requirement: Pure-memory overlay validation

`stock_analyze.competition` SHALL 提供公共函数 `validate_overlay(agent_id, overlay, repo_root=None)` 校验内存中的 overlay，并返回与 baseline 合并后的完整配置；调用过程中 SHALL NOT 修改 `configs/agents/<agent>.yaml` 或任何其它磁盘文件。

#### Scenario: Calling validate_overlay does not modify disk
- **GIVEN** `configs/agents/codex.yaml` 已存在
- **WHEN** 调用 `competition.validate_overlay("codex", overlay_dict, repo_root=root)`
- **THEN** `configs/agents/codex.yaml` 的 mtime 保持不变
- **AND** `configs/agents/_history/` 没有新文件
- **AND** 返回的 dict 等同于 `competition.load("codex")` 的结构（合并 baseline + 应用 migrate_strategy_config）

#### Scenario: Invalid overlay raises CompetitionBaselineLocked
- **GIVEN** overlay 试图覆盖 baseline-locked 字段（如 `initial_cash`）
- **WHEN** 调用 `validate_overlay`
- **THEN** 抛 `CompetitionBaselineLocked`
- **AND** 磁盘上的 overlay 文件未被读写
