## MODIFIED Requirements

### Requirement: Strategy evolution panel renders expected_effect

dashboard 策略演进时间线 SHALL 包含"预期效果"列展示每条 proposal 的 `expected_effect` 字段（HTML-escape + 必要时截断）。

#### Scenario: Proposal with expected_effect
- **GIVEN** `data/claude/proposals/2026-06-strategy.json` 含 `"expected_effect": "提高 ROE 暴露"`
- **WHEN** 渲染 `reports/claude/dashboard.html`
- **THEN** 策略演进表的该月行包含 `提高 ROE 暴露` 文本
- **AND** 表头含 `预期效果` 列

#### Scenario: Proposal missing expected_effect
- **GIVEN** proposal 文件没有 `expected_effect` 字段
- **WHEN** 渲染 dashboard
- **THEN** 该列显示 `-`
