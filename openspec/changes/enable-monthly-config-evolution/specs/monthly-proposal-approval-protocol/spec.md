## ADDED Requirements

### Requirement: Decision JSON written per proposal per agent

每次月度审批 SHALL 在 `data/competition/decisions/<month>-<agent>.json` 写一份决定记录，至少含 `decision`（`approved` / `rejected` / `edited`）、`reviewer`、`reviewed_at`，以及（仅当 `edited` 时）`edited_patch`。

#### Scenario: Approving a proposal
- **GIVEN** `data/<agent>/proposals/2026-06-strategy.json` 已存在
- **WHEN** 用户在 dashboard 上点击 Approve（或手工放置决定 JSON）
- **THEN** `data/competition/decisions/2026-06-<agent>.json` 出现
- **AND** 文件含 `decision="approved"`、`reviewer`、`reviewed_at`

> Stub: 本 capability 在 change 启动后会扩展更多 scenario（rejected / edited / 重复 review）。
