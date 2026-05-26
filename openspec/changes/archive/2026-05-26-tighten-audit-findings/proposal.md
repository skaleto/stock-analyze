## Why

代码审计（参见上一轮会话末尾的 10 项发现）暴露了若干现存隐患，最关键的是
`proposal_judge._validate_merged_overlay` 临时把待校验 overlay 写到磁盘再恢复，
这构成**文件级 TOCTOU 竞态**：另一个并发进程读 `configs/agents/<agent>.yaml`
时会看到不属于当前真实状态的"提议 overlay"。其余项是 git/systemd/dashboard
/代码卫生层面的小坑，独立来看都不阻塞当前功能，但放任则会慢慢累积成
难以诊断的怪问题。

本 change 把它们一次性收尾。

## What Changes

- **F1 修 TOCTOU**：
  - `stock_analyze/competition.py` 新增公共函数 `validate_overlay(agent_id, overlay, repo_root=None)`，
    在内存里跑 `_validate_overlay_top_level` + `_validate_locked_paths` + `_deep_merge` + `migrate_strategy_config`，返回 merged 配置。
  - `stock_analyze/proposal_judge._validate_merged_overlay` 改为 `competition.validate_overlay(...)`；
    彻底删掉写入临时配置的代码路径。
  - 测试新增 `tests/test_competition.py` 一个用例验证：调用 `validate_overlay` 时
    磁盘上的 `configs/agents/<agent>.yaml` mtime 不变。

- **F2 收紧 `.gitignore`**：
  - 从 `.claude/` 改为 `.claude/worktrees/`（精确忽略 Claude Code worktree 缓存）。
  - 这样未来新增 `.claude/commands/*.md` / `.claude/agents/*` / `.claude/skills/*` 会被默认追踪。

- **F3/F8 文档**：
  - 在 README 顶部 + `docs/forward-simulation-runbook.md` + `docs/competition-runbook.md`
    都加一句 "**只启用一套 timer**：单 agent 用 `stock-analyze-{daily,weekly}.timer`；
    双 agent 竞赛用 `stock-analyze-{claude,codex}-{daily,weekly}.timer` + `stock-analyze-monthly-review.timer`；
    不要同时启用，否则会重复拉行情、写入两套 NAV"。
  - 在 `docs/competition-runbook.md` 里加一段 `configs/agents/_history/` 说明：
    rollback 依赖这些文件，所以它们**进入 git**（不进 .gitignore），每月增长约 2 个小文件，
    需要回滚到任意历史 hash 时直接 `git checkout` 也能查看。

- **F5 dashboard 渲染 `expected_effect`**：
  - `reporting.render_strategy_evolution_panel` 在"理由摘要"后新增"预期效果"列；
    `expected = _escape_html(...)` 不再是死代码。

- **F9 systemd ExecStartPost 容错**：
  - `deploy/systemd/stock-analyze-monthly-review.service` 把 `agent-judge-proposals` 与
    `agent-apply-approved-proposals` 改成 `ExecStartPost=-...`（前缀 `-` 表示忽略退出码）。
  - 这样裁判 / apply 跑挂时，`competition-dashboard` 仍刷新，避免月度 dashboard 卡在旧数据。

- **F10 dashboard 显示 proposal drift**：
  - `reporting._load_decision` 已记录 decision JSON。新增 `_proposal_hash_drift(proposal, decision)`
    比对当前 proposal 的 hash 与 decision 中记录的 `proposal_hash`；不一致时
    在"裁判结论"列追加 `· 提案已变` 红色标记。

- **F6/F7 卫生**：
  - 删除 `tests/test_performance_metrics.py:31` 的未用变量 `ann_ret_zero_rf`。
  - 清理 pyflakes 列出的 11 项 unused imports（agent_briefing / dashboard_aggregator / diagnostics
    / monthly_review / portfolio_controls / reporting 等）。

不在范围：

- F4 历史快照文件扩展名 `.yaml`：与项目内 `configs/agents/<agent>.yaml`（JSON 语法）
  一贯惯例一致，不修。
- 任何行为变化、对 baseline 锁字段的修改、对 systemd 调度时间的修改。

## Capabilities

### New Capabilities

- `overlay-validation-public-api`：`competition.validate_overlay` 公共函数，无磁盘副作用。
- `dashboard-proposal-drift-detection`：dashboard 检测 proposal 与 decision 间的 hash 漂移并提示。

### Modified Capabilities

- `agent-strategy-evolution-view`：策略演进表新增"预期效果"列，且裁判结论列在 hash drift 时
  追加视觉提示。

## Impact

- **代码**：
  - `stock_analyze/competition.py`：新增 `validate_overlay`。
  - `stock_analyze/proposal_judge.py`：`_validate_merged_overlay` 改为内存调用，删除 try/finally 写盘块。
  - `stock_analyze/reporting.py`：策略演进面板加列；`_load_decision` 沿用；新增 hash drift 判定。
- **配置**：`.gitignore` 一行修改；不引入新字段。
- **数据**：行为不变；既有 `_history/`、`decisions/`、`config_evolution.csv` 不动。
- **systemd**：`monthly-review.service` 3 行修改。
- **文档**：3 处 runbook 增补、1 处 README 顶部提示。
- **测试**：1 个新用例（F1 防回归）。
- **依赖**：无。
- **不在范围**：F4 / 任何行为或调度变更 / 任何 baseline 修改。
