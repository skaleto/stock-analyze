## ADDED Requirements

### Requirement: Strategy evolution rows expose approval actions

Dashboard 的"策略演进时间线"面板（来自 `agent-strategy-evolution-view`）SHALL 在每个尚未审批的 proposal 行旁渲染 `Approve / Reject / Edit` 按钮；点击后通过 `competition-decision-server` 写出对应的决定 JSON（也支持用户手工放置决定 JSON 跳过 UI）。

#### Scenario: Approve button writes a decision file
- **GIVEN** `data/claude/proposals/2026-06-strategy.json` 存在且 `data/competition/decisions/2026-06-claude.json` 不存在
- **WHEN** 用户在 dashboard 上点击该行的 Approve 按钮
- **THEN** `data/competition/decisions/2026-06-claude.json` 出现且 `decision="approved"`
- **AND** dashboard 下次渲染时该行状态变为"已批准（待应用）"

> Stub: 后续 scenario 覆盖 Reject / Edit 弹窗、决定后状态显示、并发审批保护、未启动 server 时手工流程提示。
