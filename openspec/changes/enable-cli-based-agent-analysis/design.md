## Context

竞赛框架已经能稳定跑数据 + 月度对比。下一步是让 agent 真正"思考"。约束：

- ECS 不能调 LLM API。
- 用户有本地开发机 + Claude Code / Codex CLI。
- agent 周度只分析，月度才允许提案；提案要人工审核才应用（应用本身是 Phase 2）。
- 流程不能让 agent 在没指引的情况下乱翻仓库——要有明确的"任务包"。

## Goals / Non-Goals

**Goals**

- 把"周分析"和"月度策略提案"做成可重复的 CLI 工作流。
- ECS 只产出"任务包"，本地 agent 读了就知道要做什么、写到哪、按什么格式。
- 工作流可被人类替代——任何会读 markdown 的人都能做同样的分析。
- 月度 proposal 是结构化 JSON，便于未来自动 diff + 应用。
- Dashboard 上能看到 agent 的笔记和提案。

**Non-Goals**

- 不调任何 LLM API。
- 不自动 apply proposal 到 `configs/agents/<agent>.yaml`（Phase 2）。
- 不做钉钉/邮件告警。
- 不要求 agent 在 ECS 上跑。

## Decisions

### 1. 任务包（briefing）的位置与命名

`data/<agent>/notes/briefings/<key>.md`：

- 周度 key：`<YYYY-MM-DD>-weekly`，日期=生成当天。
- 月度 key：`<YYYY-MM>-monthly`，月份=目标月。

Briefings 是 ECS 写、agent 读，归在 `notes/briefings/` 子目录避免与 agent 输出混在一起。agent 自己的输出走 `data/<agent>/notes/<key>.md`，提案走 `data/<agent>/proposals/<month>-strategy.json`。

### 2. Briefing 内容结构

固定 5 段：

1. **角色**：你是哪个 agent，你的策略目标，你的边界（不可改字段、不可越界目录）。
2. **数据快照**：本周/月数据摘要。markdown 表格优先。
3. **任务**：明确写"做 X，不做 Y"。周度禁止改 config。月度要求输出 JSON proposal。
4. **输出契约**：文件路径、格式、字段 schema、长度上限。
5. **可选参考**：上次同周期笔记的相对路径，让 agent 自己 read 进来。

### 3. 月度 proposal schema

JSON，schema 与上次设计一致：

```json
{
  "agent_id": "claude",
  "based_on_config_hash": "abc123def456",
  "proposed_at": "2026-06-01",
  "rationale": "中文 300 字内说明",
  "expected_effect": "一句话",
  "risks": ["风险 1", "风险 2"],
  "no_change": false,
  "patch": {
    "factors": { "momentum_60": {"weight": 0.05} },
    "factor_processing": {},
    "portfolio_controls": {},
    "filters": {}
  }
}
```

`no_change=true` 时 `patch` 为空对象。校验由下一 change（`enable-monthly-config-evolution`）应用 patch 前做；本 change 不强制校验，只在 dashboard 上展示 JSON。

### 4. 自动化挂钩

`run-weekly --agent X` 现在的末尾流程：
- generate_rebalance_orders / update_nav / forward IC / persist_health / generate_weekly_report / generate_dashboard
- **追加**：`build_weekly_briefing(X)` → 写 `data/X/notes/briefings/<today>-weekly.md`

`competition-monthly-review --month M`：
- 现有：compute_review / write_review
- **追加**：for agent in agents: `build_monthly_briefing(agent, M)` → 写 `data/<agent>/notes/briefings/<M>-monthly.md`

挂钩失败不阻塞主流程（briefing 是观察物，不是临界路径）。

### 5. Claude Code slash commands

`.claude/commands/weekly-review.md` 与 `.claude/commands/monthly-strategy.md`。

Claude Code 用 `$ARGUMENTS` 注入用户参数（slash 命令格式 `/weekly-review claude`）。命令体本质是一段"提示模板"：

- 让 Claude 读 `CLAUDE.md` 顶部 + 最新 briefing。
- 强约束输出路径。
- 明确禁止改 config / 改源码。

Codex CLI 没有等价的 slash 文件，但 AGENTS.md 里写"当用户说 do weekly review 时按以下步骤..."就够了。

### 6. CLAUDE.md vs AGENTS.md

两份对偶文件：

- `CLAUDE.md`：claude-side（Claude Code）入口，写 "你是 claude agent"。
- `AGENTS.md`：codex-side（Codex CLI）入口，写 "你是 codex agent"。

两者结构对称，方便维护。Claude Code 默认会读 `CLAUDE.md`；Codex CLI 默认会读 `AGENTS.md`。

### 7. 同步脚本

`scripts/sync-from-ecs.sh` 用 rsync：

- 拉 `data/`、`configs/`、`reports/`。
- 排除 `__pycache__`、`.git`、巨大的 `data/shared/cache/`（可选）。
- 入口环境变量 `SA_ECS_REMOTE=user@host:/opt/stock-analyze/app`。

`scripts/sync-to-ecs.sh` 反方向，但只推 `data/<agent>/notes/` 和 `data/<agent>/proposals/`（agent 写入区），避免覆盖 ECS 的状态。

push 完用户在 ECS 上跑 `python3 -m stock_analyze competition-dashboard` 刷新（或等下次 timer）。

### 8. Dashboard 笔记面板

`reports/<agent>/dashboard.html` 末尾、`reports/<agent>/dashboard_fragment.html` 同样位置追加：

```
<h2>近期 agent 笔记</h2>
<div class="panel">
  <details><summary>2026-05-15-weekly-review.md · 2.3KB</summary><pre>...</pre></details>
  <details><summary>...</summary><pre>...</pre></details>
</div>
```

最多 5 篇，按 mtime 倒序。只展示 `data/<agent>/notes/*.md`，不展示 `notes/briefings/` 与 `proposals/`。

Markdown 内容直接以 `<pre>` 显示（不解析），原因：避免引入 markdown→HTML 转换依赖；笔记结构本就规整。

### 9. 测试范围

- `tests/test_agent_briefing.py`：人造 data 目录，断言 briefing 包含必备段（角色 / 数据快照 / 任务 / 输出契约）+ baseline-locked 字段在月度 briefing 中可见。
- 现有 54 测试 + 新增 3-5 个 = 60 内。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 用户忘记 rsync 就跑 slash，分析的是旧数据 | 误判 | briefing 顶部写"数据截止日"`as_of`，agent 自己看一眼就能识别陈旧 |
| 笔记和提案在本地写完没推回 ECS | dashboard 看不到 | 提供 sync-to-ecs.sh + runbook 明确步骤；不强制（用户可本地看） |
| agent 不按 schema 输出 JSON | 自动 apply（Phase 2）会失败 | Phase 1 不 apply；dashboard 展示 raw JSON 让人眼检查 |
| briefing 太长（>20K tokens）超出 Claude Code context | 一次会话装不下 | 月度 briefing 控制在 ~15K tokens 内；如果超长，agent 可分段 read |
| Claude Code 跨 agent 执行（用 claude 视角分析 codex） | 角色混乱 | slash command 参数化 agent 名；CLAUDE.md 写明"你只代表 claude"；用户主动 `/weekly-review codex` 时由 Claude Code 模拟 codex 视角，文件路径仍隔离 |
| 大缓存 rsync 慢 | 同步成本高 | sync-from-ecs.sh 提供 `--exclude-cache` 选项；常态下用户只需要 `data/{<agent>,competition}/` 而非 `data/shared/cache/` |
| ECS 与本地代码版本不一致 | briefing schema 不兼容 | briefing 顶部写 `code_version`；agent 看到不一致时报警 |

## Migration Plan

1. 把本 change 落地到 worktree 后跑 `python3 -m stock_analyze --agent claude run-weekly`，确认 ECS 端 briefing 自动生成。
2. 在 ECS 上拉一次 git pull / 重启 systemd。
3. 在本地仓库克隆同一份 worktree 或仓库主干。
4. 设环境变量 `SA_ECS_REMOTE=user@host:/opt/stock-analyze/app`。
5. 跑 `scripts/sync-from-ecs.sh` 同步数据。
6. 在 Claude Code 里：`/weekly-review claude` → 产出笔记 → 检查 `data/claude/notes/*.md`。
7. 在 Codex CLI 里（同一仓库目录）：让它 "do weekly review for codex side"。
8. 跑 `scripts/sync-to-ecs.sh` 把笔记推回。
9. 月底跑 `/monthly-strategy claude` 与对应 Codex 任务，看 proposal JSON。
10. 回滚：直接删 `data/<agent>/notes/`、`data/<agent>/proposals/`、`.claude/commands/`、`CLAUDE.md`、`scripts/sync-*.sh`、`stock_analyze/agent_briefing.py` 即可，不影响既有竞赛产物。

## Open Questions

- Phase 2 apply 时是用 git commit 还是 in-place 改？倾向 in-place 改 + `configs/agents/_history/<hash>.yaml` 归档，留给下一 change。
- 是否要给 Codex CLI 也准备 `.codex/` 配置？现在 Codex CLI 还在演进；先靠 AGENTS.md 通用约定，等 Codex CLI 稳定再补特化。
- 月度 briefing 是否要带 baseline_locked 字段的"哪些尝试会被拒"清单？倾向带，省得 agent 输出 reject 的 patch。
