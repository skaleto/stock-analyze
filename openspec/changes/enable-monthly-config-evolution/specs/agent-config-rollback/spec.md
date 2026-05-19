## ADDED Requirements

### Requirement: Rollback restores any historical overlay snapshot

`agent-rollback --agent <id> --to <config_hash>` CLI 子命令 SHALL 从 `configs/agents/_history/<config_hash>.yaml` 恢复对应快照写回 `configs/agents/<id>.yaml`，并在 `data/<agent>/config_evolution.csv` 追加一行 `rollback`。

#### Scenario: Rollback to an earlier hash
- **GIVEN** `configs/agents/_history/abc123def456.yaml` 存在
- **WHEN** 运行 `python3 -m stock_analyze agent-rollback --agent claude --to abc123def456`
- **THEN** `configs/agents/claude.yaml` 文本与历史快照一致
- **AND** `data/claude/config_evolution.csv` 末尾一行包含 `event="rollback"`、`to_hash="abc123def456"`

> Stub: 后续 scenario 覆盖未知 hash、锁字段不兼容（baseline 改变后老 overlay 不再合法）的情况。
