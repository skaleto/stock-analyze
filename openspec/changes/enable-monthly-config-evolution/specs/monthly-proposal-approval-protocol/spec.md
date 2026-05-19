## ADDED Requirements

### Requirement: Referee decision JSON written per proposal per agent

每次月度裁判 SHALL 在 `data/competition/decisions/<month>-<agent>.json` 写一份决定记录，至少含 `decision`（`approved` / `rejected` / `needs_human`）、`reviewer`、`reviewed_at`、`risk_level`、`reasons`、`warnings`、`violations` 和 `patch`。

#### Scenario: Referee approves a small valid proposal
- **GIVEN** `data/<agent>/proposals/2026-06-strategy.json` 已存在
- **AND** proposal 未触碰 baseline locked 字段，且 patch 变化小、理由完整、月度 review 已存在
- **WHEN** 运行 `python3 -m stock_analyze agent-judge-proposals --month 2026-06`
- **THEN** `data/competition/decisions/2026-06-<agent>.json` 出现
- **AND** 文件含 `decision="approved"`、`reviewer="referee"`、`risk_level="low"`

#### Scenario: Referee rejects forbidden patch
- **GIVEN** proposal patch 含 `initial_cash` 或其它非 overlay 字段
- **WHEN** 运行 `agent-judge-proposals`
- **THEN** decision JSON 含 `decision="rejected"`
- **AND** `violations` 说明被拒绝的路径

#### Scenario: Referee escalates ambiguous patch
- **GIVEN** proposal patch 单个因子权重变化超过阈值
- **WHEN** 运行 `agent-judge-proposals`
- **THEN** decision JSON 含 `decision="needs_human"`
- **AND** `required_human_attention=true`
