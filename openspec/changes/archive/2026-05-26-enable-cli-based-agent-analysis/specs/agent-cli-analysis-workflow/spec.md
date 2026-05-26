## ADDED Requirements

### Requirement: Weekly briefing markdown

系统 SHALL 提供 `stock_analyze.agent_briefing.build_weekly_briefing(agent_id, as_of=None, repo_root=None)`，返回一份 markdown，包含五个固定段：`# 角色`、`# 数据快照`、`# 任务`、`# 输出契约`、`# 可选参考`；并通过 `agent-prepare-weekly --agent <id>` CLI 子命令落到 `data/<id>/notes/briefings/<YYYY-MM-DD>-weekly.md`。

#### Scenario: Five mandatory sections present
- **GIVEN** 某 agent 已有合法的 `data/<id>/` 状态
- **WHEN** 调用 `build_weekly_briefing("claude")`
- **THEN** 返回的 markdown 含五个段标题：`# 角色`、`# 数据快照`、`# 任务`、`# 输出契约`、`# 可选参考`

#### Scenario: CLI writes briefing to canonical path
- **WHEN** 运行 `python3 -m stock_analyze agent-prepare-weekly --agent claude --as-of 2026-05-22`
- **THEN** 文件 `data/claude/notes/briefings/2026-05-22-weekly.md` 存在
- **AND** 文件以 `# 角色` 起始

#### Scenario: Briefing references output path
- **WHEN** Briefing 渲染
- **THEN** `# 输出契约` 段含目标路径 `data/<agent>/notes/<YYYY-MM-DD>-weekly-review.md`
- **AND** 明确禁止修改 `configs/`、`stock_analyze/`、`AGENTS.md`、`CLAUDE.md`

### Requirement: Monthly briefing markdown

系统 SHALL 提供 `build_monthly_briefing(agent_id, month, repo_root=None)`，月度 briefing 在周度结构之上额外包含：当月对比 JSON 摘要、近 4 篇周笔记的引用、`BASELINE_LOCKED_PATHS` 清单与 `competition.yaml` 关键值、`# 输出契约` 中规定的 JSON proposal schema；并通过 `agent-prepare-monthly --agent <id> [--month YYYY-MM]` CLI 落到 `data/<id>/notes/briefings/<YYYY-MM>-monthly.md`。

#### Scenario: Monthly briefing includes locked paths
- **WHEN** `build_monthly_briefing("claude", "2026-05")` 渲染
- **THEN** 输出文本包含字符串 `initial_cash`、`accounts.*.cash`、`trading.commission_rate` 等锁字段
- **AND** 明确告诉 agent "在 proposal 的 patch 中包含这些字段会被拒绝"

#### Scenario: JSON proposal schema is documented
- **WHEN** Monthly briefing 渲染
- **THEN** 输出含 JSON schema 描述：`agent_id, based_on_config_hash, proposed_at, rationale, expected_effect, risks, no_change, patch`
- **AND** 给出输出路径 `data/<agent>/proposals/<YYYY-MM>-strategy.json`

#### Scenario: Default month is previous calendar month
- **WHEN** 运行 `python3 -m stock_analyze agent-prepare-monthly --agent codex` 在 2026-06-03
- **THEN** 默认目标月份为 `2026-05`
- **AND** 文件 `data/codex/notes/briefings/2026-05-monthly.md` 存在

### Requirement: Auto-generate briefing in scheduled commands

`run-weekly --agent <id>` SHALL 在主流程完成后自动调用 `agent-prepare-weekly` 等价逻辑；`competition-monthly-review --month M` SHALL 对每个参赛 agent 调用 `agent-prepare-monthly` 等价逻辑。Briefing 写入失败仅记录到日志，不影响主命令退出码。

#### Scenario: run-weekly auto-writes briefing
- **WHEN** `python3 -m stock_analyze --agent claude run-weekly` 成功执行
- **THEN** 同次运行结束后 `data/claude/notes/briefings/<today>-weekly.md` 存在

#### Scenario: competition-monthly-review auto-writes briefings for all agents
- **WHEN** `python3 -m stock_analyze competition-monthly-review --month 2026-05` 成功执行
- **THEN** `data/claude/notes/briefings/2026-05-monthly.md` 与 `data/codex/notes/briefings/2026-05-monthly.md` 同时存在

#### Scenario: Briefing failure does not break main command
- **GIVEN** 写入 briefing 时抛 OSError（磁盘满或权限错）
- **WHEN** run-weekly 主流程已成功完成
- **THEN** CLI 仍以 0 退出
- **AND** `data/shared/data_health.json` 含一条 briefing 写入失败记录

### Requirement: Agent-side operating manuals

仓库根 SHALL 提供 `CLAUDE.md`（Claude Code 默认入口）与 `AGENTS.md`（Codex CLI 默认入口）。两份文件 SHALL 在身份、可改文件、不可改文件、CLI 工作流四节上语义对偶。

#### Scenario: CLAUDE.md identifies claude-side
- **WHEN** 打开仓库根 `CLAUDE.md`
- **THEN** 文件开头明确写 "You are operating as the **claude** competitor"
- **AND** 包含周/月 CLI workflow 章节

#### Scenario: AGENTS.md has parallel CLI workflow section
- **WHEN** 打开仓库根 `AGENTS.md`
- **THEN** 文件含 "CLI analysis workflow" 章节
- **AND** 描述"do weekly review for codex" / "do monthly strategy for codex" 时的标准动作

#### Scenario: Locked-field statements match competition.py
- **GIVEN** `stock_analyze/competition.py.BASELINE_LOCKED_PATHS`
- **WHEN** 对照 `CLAUDE.md` 与 `AGENTS.md` 中的锁字段说明
- **THEN** 两份手册涵盖的锁字段名称是 `BASELINE_LOCKED_PATHS` 的超集（可以更友好命名，但不可遗漏）

### Requirement: Claude Code slash commands

仓库 SHALL 提供 `.claude/commands/weekly-review.md` 与 `.claude/commands/monthly-strategy.md` 两个 slash command 模板，参数化 agent_id。命令体 SHALL 引导 Claude Code 读 `CLAUDE.md` + 最新 briefing + 写到约定路径。

#### Scenario: Slash command takes agent argument
- **GIVEN** 用户在 Claude Code 中输入 `/weekly-review claude`
- **WHEN** 命令体被注入
- **THEN** 命令体含 `$ARGUMENTS` 占位符（Claude Code 替换为 `claude`）
- **AND** 文档化"如果用户传 codex 也按相同流程操作"

#### Scenario: Slash command prevents config edits
- **WHEN** weekly-review slash 命令被使用
- **THEN** 命令体明确禁止修改 `configs/agents/*.yaml`、`configs/competition.yaml`、`stock_analyze/*.py`

### Requirement: ECS ↔ local sync scripts

仓库 SHALL 提供 `scripts/sync-from-ecs.sh` 与 `scripts/sync-to-ecs.sh`。`sync-from-ecs.sh` 从 ECS rsync `data/`、`configs/`、`reports/` 到本地；`sync-to-ecs.sh` 把本地 `data/<agent>/notes/` 与 `data/<agent>/proposals/` 推回 ECS。两脚本均通过环境变量 `SA_ECS_REMOTE` 指定远端路径，缺失时退出码非零。

#### Scenario: sync-from-ecs requires SA_ECS_REMOTE
- **GIVEN** 环境变量 `SA_ECS_REMOTE` 未设置
- **WHEN** 运行 `scripts/sync-from-ecs.sh`
- **THEN** 脚本以非零退出码结束
- **AND** stderr 含 "SA_ECS_REMOTE" 与示例值

#### Scenario: sync-to-ecs is scoped to agent writeable areas
- **GIVEN** `SA_ECS_REMOTE=user@host:/opt/stock-analyze/app`
- **WHEN** 运行 `scripts/sync-to-ecs.sh`
- **THEN** rsync 只覆盖 `data/<agent>/notes/` 和 `data/<agent>/proposals/`
- **AND** 不修改 ECS 上的 `data/<agent>/state.json`、`daily_nav.csv`、`trades.csv`

### Requirement: Dashboard agent notes panel

`generate_dashboard` SHALL 在 page 和 fragment 末尾追加 "近期 agent 笔记" 区段，按 mtime 倒序展示 `<data_dir>/notes/*.md` 最近 5 篇（不含 `notes/briefings/`、不含 `proposals/`），每篇以 `<details>` 折叠显示完整文本。当目录不存在或没有合规笔记时显示占位说明。

#### Scenario: Latest 5 notes shown
- **GIVEN** `data/claude/notes/` 含 8 个 `.md` 文件
- **WHEN** `generate_dashboard(config, store, reports_dir)` 运行
- **THEN** 生成的 dashboard 含最多 5 个 `<details>` 块
- **AND** 排序按 mtime 降序

#### Scenario: Briefings and proposals excluded
- **GIVEN** `data/claude/notes/briefings/2026-05-22-weekly.md` 与 `data/claude/proposals/2026-05-strategy.json` 存在
- **WHEN** dashboard 渲染笔记面板
- **THEN** briefing 与 proposal 文件不出现在笔记面板里

#### Scenario: Empty-state placeholder
- **GIVEN** `data/codex/notes/` 不存在
- **WHEN** dashboard 渲染笔记面板
- **THEN** 面板显示占位 "尚无 agent 笔记。跑过 /weekly-review 后会出现。"
- **AND** dashboard 其它部分继续渲染
