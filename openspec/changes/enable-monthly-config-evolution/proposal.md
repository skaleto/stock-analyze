## Why

`enable-cli-based-agent-analysis` 已经让 agent 能每月产出结构化策略提案
（`data/<agent>/proposals/<month>-strategy.json`），但**应用环节仍是纯手工**：
你必须打开 JSON、复制 `patch`、手动改 `configs/agents/<agent>.yaml`、git
commit + push 一次。两个 agent 同时跑、proposal 每月一次，长期手工合入
不可持续，而且容易：

- 漏改字段、改错值；
- 没记录 "这一版 overlay 是哪个 proposal 触发的"；
- 改完想回滚发现没留旧版。

本 change 把这一步流程化：先由确定性"裁判"审查 proposal 是否合规、小步、
可解释，再只应用 `approved` 的 patch，并在 git 历史和本地
`config_evolution.csv` 双重留痕，提供 `agent-rollback` 命令把任一 agent 回到
某个 `config_hash`。裁判不预测收益，只判断改动是否安全、克制、符合竞赛规则。

## What Changes

- **裁判协议**：`agent-judge-proposals [--month YYYY-MM]` 读取
  `data/<agent>/proposals/<month>-strategy.json`，写
  `data/competition/decisions/<month>-<agent>.json`，记录
  `decision: approved | rejected | needs_human`、`reviewer`、`reviewed_at`、
  `risk_level`、`reasons`、`warnings`、`violations` 和最终 `patch`。
- **应用命令**：新 CLI `agent-apply-approved-proposals [--month YYYY-MM]`
  扫描 `data/competition/decisions/<month>-*.json`，对每个 `approved` 决定：
  1. 校验 patch 不含锁字段；
  2. 把当前 `configs/agents/<agent>.yaml` 备份到 `configs/agents/_history/<config_hash>.yaml`；
  3. 把 patch 深度合并到当前 overlay，写回；
  4. 在 `data/<agent>/config_evolution.csv` 追加一行
     （`event_at, month, source_proposal, from_hash, to_hash, decision_path`）。
- **回滚**：`agent-rollback --agent <id> --to <config_hash>` 从
  `_history/` 恢复对应快照到 `configs/agents/<id>.yaml`，并在
  `config_evolution.csv` 追加一行 `rollback`。
- **Dashboard 状态展示**：每个月度 proposal 行展示裁判结论，用户看到的是
  "裁判通过 / 需要人工 / 裁判拒绝" 与风险等级，而不是复杂金融指标。
- **运行链路**：月度 systemd service 在 review 后追加 judge/apply/dashboard；
  `scripts/sync-to-ecs.sh` 在推回本地 agent 产出的 notes/proposals 后也默认触发
  ECS 端 judge/apply/dashboard，避免 proposal 晚于月度 timer 生成时漏处理。
- **测试与文档**：apply / rollback 单元测试；CLAUDE.md / AGENTS.md 加
  "裁判后会自动 apply"提示；docs/competition-runbook.md 描述完整裁判流。

## Capabilities

### New Capabilities

- `strategy-proposal-judge`：裁判规则、决定 JSON schema 与位置约定。
- `agent-config-evolution-apply`：apply 命令、history 备份、evolution log。
- `agent-config-rollback`：rollback 命令与审计。
- `dashboard-proposal-approval-ui`：dashboard 上的裁判状态展示。

### Modified Capabilities

- `agent-strategy-evolution-view`：策略演进时间线行需附加"状态"列，从决定
  JSON 与 evolution log 拉数据。

## Impact

- **代码**：3 个新模块（`stock_analyze/proposal_judge.py`、`stock_analyze/proposal_apply.py`、
  `stock_analyze/agent_rollback.py`）+ `reporting.py`、`cli.py` 与 systemd 微调。
- **配置**：无新字段；仅约定 `configs/agents/_history/` 子目录用于备份。
- **数据**：新增 `data/competition/decisions/<month>-<agent>.json` 与
  `data/<agent>/config_evolution.csv`。
- **文档**：更新 runbook + system-overview + CLAUDE.md/AGENTS.md。
- **不在范围**：
  - 自动产生 proposal（agent 主动创造）。
  - 真实 LLM 裁判 API 调用（当前裁判是确定性硬规则）。
  - 多人协同审批。
  - dashboard 上展示完整 patch diff 视图（先用文本，未来再做 syntax-highlighted diff）。

## Status

**ACTIVE** — 本 change 已启动，MVP 实现确定性裁判、自动应用 approved patch、
历史归档、回滚和 dashboard 裁判状态展示。
