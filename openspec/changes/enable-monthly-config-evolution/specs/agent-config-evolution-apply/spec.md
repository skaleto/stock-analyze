## ADDED Requirements

### Requirement: Apply command merges approved patches with history archive

`agent-apply-approved-proposals [--month YYYY-MM]` CLI 子命令 SHALL 扫描已 approve 的决定，逐个对应用 agent 做：备份当前 overlay 到 `configs/agents/_history/<config_hash>.yaml` → 校验 patch 不含 baseline-locked 字段 → 深度合并 patch 到 `configs/agents/<agent>.yaml` → 在 `data/<agent>/config_evolution.csv` 追加一行。

#### Scenario: Approved patch applied with history archive
- **GIVEN** `data/competition/decisions/2026-06-claude.json` 含 `decision="approved"` 与 `data/claude/proposals/2026-06-strategy.json` 中的 patch
- **WHEN** 运行 `python3 -m stock_analyze agent-apply-approved-proposals --month 2026-06`
- **THEN** `configs/agents/_history/<prev_hash>.yaml` 包含合并前的完整 overlay
- **AND** `configs/agents/claude.yaml` 包含 patch 字段
- **AND** `data/claude/config_evolution.csv` 追加一行 `applied_at, 2026-06, ..., from_hash, to_hash, decisions/2026-06-claude.json`

#### Scenario: Non-approved decisions are skipped
- **GIVEN** decision JSON 含 `decision="needs_human"` 或 `decision="rejected"`
- **WHEN** 运行 `agent-apply-approved-proposals --month 2026-06`
- **THEN** 对应 `configs/agents/<agent>.yaml` 不变
- **AND** 命令输出 `status=skipped`
