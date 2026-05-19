## Why

`enable-cli-based-agent-analysis` 已经让 agent 能每月产出结构化策略提案
（`data/<agent>/proposals/<month>-strategy.json`），但**应用环节仍是纯手工**：
你必须打开 JSON、复制 `patch`、手动改 `configs/agents/<agent>.yaml`、git
commit + push 一次。两个 agent 同时跑、proposal 每月一次，长期手工合入
不可持续，而且容易：

- 漏改字段、改错值；
- 没记录 "这一版 overlay 是哪个 proposal 触发的"；
- 改完想回滚发现没留旧版。

本 change 把这一步流程化：dashboard 显示 proposal 后给一个明确的"审批"
动作，调度器读到 approve 决定后应用 patch，并在 git 历史和本地
`config_evolution.csv` 双重留痕，提供 `agent-rollback` 命令把任一 agent 回到
某个 `config_hash`。

## What Changes

- **审批协议**：用户在 dashboard 上点 Approve / Reject 后，写一份
  `data/competition/decisions/<month>-<agent>.json`，记录
  `decision: approved | rejected | edited`、`reviewer`、`reviewed_at`、
  `edited_patch`（可选，允许人手改 patch 后再 approve）。
- **应用命令**：新 CLI `agent-apply-approved-proposals [--month YYYY-MM]`
  扫描 `data/competition/decisions/<month>-*.json`，对每个 `approved` 决定：
  1. 校验 patch 不含锁字段；
  2. 把当前 `configs/agents/<agent>.yaml` 备份到 `configs/agents/_history/<config_hash>.yaml`；
  3. 把 patch 深度合并到当前 overlay，写回；
  4. 在 `data/<agent>/config_evolution.csv` 追加一行
     （`applied_at, month, source_proposal, from_hash, to_hash, decision_path`）。
- **回滚**：`agent-rollback --agent <id> --to <config_hash>` 从
  `_history/` 恢复对应快照到 `configs/agents/<id>.yaml`，并在
  `config_evolution.csv` 追加一行 `rollback`。
- **Dashboard 审批 UI**：每个月度 proposal 行追加 `Approve / Reject /
  Edit` 三个按钮；按钮提交到本地小型 HTTP endpoint（`competition-decision-server`）
  写决定 JSON。MVP 阶段也允许用户**直接手动放置**决定 JSON 跳过 UI。
- **测试与文档**：apply / rollback 单元测试；CLAUDE.md / AGENTS.md 加
  "审批后会自动 apply"提示；docs/competition-runbook.md 描述完整审批流。

## Capabilities

### New Capabilities

- `monthly-proposal-approval-protocol`：决定 JSON schema 与位置约定。
- `agent-config-evolution-apply`：apply 命令、history 备份、evolution log。
- `agent-config-rollback`：rollback 命令与审计。
- `dashboard-proposal-approval-ui`：dashboard 上的审批按钮与提交端点。

### Modified Capabilities

- `agent-strategy-evolution-view`：策略演进时间线行需附加"状态"列，从决定
  JSON 与 evolution log 拉数据。

## Impact

- **代码**：~3 个新模块（`stock_analyze/proposal_apply.py`、`stock_analyze/agent_rollback.py`、
  `stock_analyze/decision_server.py`）+ `reporting.py` 与 `cli.py` 微调。
- **配置**：无新字段；仅约定 `configs/agents/_history/` 子目录用于备份。
- **数据**：新增 `data/competition/decisions/<month>-<agent>.json` 与
  `data/<agent>/config_evolution.csv`。
- **文档**：更新 runbook + system-overview + CLAUDE.md/AGENTS.md。
- **不在范围**：
  - 自动产生 proposal（agent 主动创造）。
  - 多人协同审批（当前假设单一人类 reviewer）。
  - dashboard 上展示完整 patch diff 视图（先用文本，未来再做 syntax-highlighted diff）。

## Status

**STUB** — 本 change 当前是占位，等 Phase 2 启动时再补 design / specs。
近期不消化。
