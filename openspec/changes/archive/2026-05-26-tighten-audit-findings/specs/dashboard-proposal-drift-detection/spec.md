## ADDED Requirements

### Requirement: Dashboard detects proposal hash drift

策略演进时间线 SHALL 在 proposal 当前内容与裁判决定记录的 `proposal_hash` 不一致时，于"裁判结论"单元格末尾追加视觉提示 `提案已变`。

#### Scenario: Proposal modified after referee judgment
- **GIVEN** `data/competition/decisions/2026-06-claude.json` 中 `proposal_hash` 为 `abc123`
- **AND** `data/claude/proposals/2026-06-strategy.json` 当前文件 sha256[:12] 为 `def456`
- **WHEN** 渲染 `reports/claude/dashboard.html`
- **THEN** 策略演进时间线该月的"裁判结论"列含 `提案已变`
- **AND** 该单元格挂有 `proposal-drift` 类用于红色高亮

#### Scenario: Proposal unchanged after judgment
- **GIVEN** decision 与当前 proposal 的 hash 相同
- **WHEN** 渲染 dashboard
- **THEN** 单元格内仅显示裁判结论文本，无 `提案已变` 后缀
