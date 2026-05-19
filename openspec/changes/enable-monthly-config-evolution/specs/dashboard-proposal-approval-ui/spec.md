## ADDED Requirements

### Requirement: Strategy evolution rows expose referee status

Dashboard 的"策略演进时间线"面板（来自 `agent-strategy-evolution-view`）SHALL 在每个 proposal 行展示裁判状态：`待裁判`、`裁判通过 / <risk>`、`裁判拒绝 / <risk>` 或 `需要人工 / <risk>`。

#### Scenario: Proposal has approved decision
- **GIVEN** `data/competition/decisions/2026-06-claude.json` 含 `decision="approved"` 与 `risk_level="low"`
- **WHEN** 渲染 `reports/claude/dashboard.html`
- **THEN** 策略演进时间线该月显示 `裁判通过 / low`

#### Scenario: Proposal has no decision yet
- **GIVEN** proposal 存在但 decision JSON 不存在
- **WHEN** 渲染 dashboard
- **THEN** 该月显示 `待裁判`
