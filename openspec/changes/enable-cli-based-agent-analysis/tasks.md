## 1. OpenSpec Foundation

- [x] 1.1 `openspec new change enable-cli-based-agent-analysis`。
- [x] 1.2 `proposal.md`、`design.md` 写就，明确无 API 调用约束。
- [x] 1.3 `specs/agent-cli-analysis-workflow/spec.md` 完整 scenario。
- [x] 1.4 `openspec validate --strict` 通过。

**Quality Gate:**
- [x] OpenSpec 校验通过。

---

## 2. Briefing 生成与 CLI 入口

- [x] 2.1 `stock_analyze/agent_briefing.py`：`build_weekly_briefing`、`build_monthly_briefing`、`write_briefing`、`weekly_briefing_path`、`monthly_briefing_path`。
- [x] 2.2 Briefing 五段：角色 / 数据快照 / 任务 / 输出契约 / 可选参考。
- [x] 2.3 月度 briefing 含 `BASELINE_LOCKED_PATHS` 清单和 `competition.yaml` 关键值。
- [x] 2.4 CLI `agent-prepare-weekly --agent <id> [--as-of YYYY-MM-DD]`。
- [x] 2.5 CLI `agent-prepare-monthly --agent <id> [--month YYYY-MM]`。
- [x] 2.6 `run-weekly --agent <id>` 末尾自动调用 `_auto_write_weekly_briefing`；失败仅 log。
- [x] 2.7 `competition-monthly-review` 末尾对每个 agent 自动写月度 briefing。

**Quality Gate:**
- [x] CLI 烟囱：`agent-prepare-weekly --agent claude` 与 `agent-prepare-monthly --agent codex --month 2026-05` 都成功生成对应 markdown。

---

## 3. CLAUDE.md + AGENTS.md CLI workflow + slash commands

- [x] 3.1 `CLAUDE.md` 写就（claude-side Claude Code 入口）。
- [x] 3.2 `AGENTS.md` 追加 §5b "CLI analysis workflow"。
- [x] 3.3 `.claude/commands/weekly-review.md` slash command（参数化 agent_id）。
- [x] 3.4 `.claude/commands/monthly-strategy.md` slash command（参数 agent_id [+ month]）。
- [x] 3.5 slash command 文件首行 frontmatter `description` + `argument-hint`，Claude Code 列表用。

**Quality Gate:**
- [x] CLAUDE.md / AGENTS.md 在身份 / 可改文件 / 不可改文件 / 锁字段 / CLI workflow 五节上对偶。

---

## 4. 同步脚本与 runbook

- [x] 4.1 `scripts/sync-from-ecs.sh` rsync `data/`、`configs/`、`reports/` 拉到本地；`--exclude-cache` 跳过 `data/shared/cache/`。
- [x] 4.2 `scripts/sync-to-ecs.sh` 推 `data/<agent>/notes/`、`data/<agent>/proposals/` 回 ECS。
- [x] 4.3 脚本入口校验 `SA_ECS_REMOTE` 环境变量；`-h` 打印用法（通过注释 grep 实现）。
- [x] 4.4 `docs/competition-runbook.md` 新增 "本地分析工作流" 章节 + briefing 五段表 + 推荐节奏更新。
- [x] 4.5 README 顶部"双 agent 竞赛模式" 提示加 "本地分析见 `CLAUDE.md` / `AGENTS.md`"。

**Quality Gate:**
- [x] `bash -n scripts/sync-*.sh` 语法检查通过。
- [x] 缺 `SA_ECS_REMOTE` 时退出码 2 + 打印示例。

---

## 5. Dashboard 笔记面板

- [x] 5.1 `reporting.render_agent_notes_panel(data_dir, limit=5)` 渲染折叠 `<details>` 列表，HTML-escape 内容。
- [x] 5.2 `generate_dashboard` 在 page 与 fragment 末尾追加 `<h2>近期 agent 笔记</h2>` + panel。
- [x] 5.3 排除 `notes/briefings/` 与 `proposals/`；只列 `data/<agent>/notes/*.md`。
- [x] 5.4 缺目录/为空时显示占位 "尚无 agent 笔记。跑过 /weekly-review 后会出现。"

**Quality Gate:**
- [x] Page 与 fragment 都含 `<h2>近期 agent 笔记</h2>` 标记。

---

## 6. 测试与验证

- [x] 6.1 `tests/test_agent_briefing.py`：7 个用例（weekly 五段 / weekly 写盘 / monthly locked / monthly baseline excerpt / monthly review excerpt / 笔记面板空态 / 笔记面板列出最新 5 篇并排除 briefings）。
- [x] 6.2 `python3 -m py_compile stock_analyze/*.py tests/*.py`。
- [x] 6.3 `python3 -m unittest discover -s tests` 全绿（61 测试：54 既有 + 7 新增）。
- [x] 6.4 `openspec validate enable-cli-based-agent-analysis --strict` 通过。

**Quality Gate:**
- [x] 三件套通过；既有 54 测试 + 新增 7 测试全绿。

---

## Completion Checklist

- [x] 所有 Phase 完成并通过 Quality Gate。
- [x] `CLAUDE.md` / `AGENTS.md` / slash commands / runbook 一致。
- [x] 既有单 agent 模式不受影响。
- [x] 留下 `enable-monthly-config-evolution` change 作为下一步（apply approved proposal）。已落在 `openspec/changes/enable-monthly-config-evolution/`。
