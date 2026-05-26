## Why

`introduce-dual-agent-competition` 已经让 ECS 能稳定跑两个 agent 的纸面策略，并产生月度对比。但目前 agent 的"分析与策略改动"完全靠人翻 dashboard，没有形成闭环。

用户希望：

- **每周**：两个 agent 各自分析本周数据是否合理 + 下一步关注点。不改 config。
- **每月**：两个 agent 共享对比数据，各自分析并产出策略调整提案；由用户审核后应用。
- **运行环境约束**：ECS 上没有 LLM API key，只能跑数据 + 出报告。Agent 的"思考"在用户的开发机上完成，靠 Claude Code 和 Codex CLI 两个本地工具。

所以这个 change 不引入任何 API 调用，而是把"agent 工作流"做成一个 **CLI 协作约定**：

- ECS 跑完 `run-weekly` 后自动产出"周分析任务包"markdown（briefing），位置约定在 `data/<agent>/notes/briefings/<date>-weekly.md`。
- ECS 跑完 `competition-monthly-review` 后自动产出"月分析任务包"markdown，含对比数据与可改字段说明。
- 用户在开发机 rsync 拉取数据后，在仓库里跑 Claude Code (`/weekly-review claude`) 或 Codex CLI（在 AGENTS.md 指引下"do weekly review"），agent 读 briefing，把分析结果写回 `data/<agent>/notes/<date>-weekly-review.md`，月度时再产出 `data/<agent>/proposals/<month>-strategy.json`。
- 写完 rsync 推回 ECS，dashboard 下个刷新就能看到。

整个链路里 agent 是 Claude Code 或 Codex CLI 本身——它们已有 Read/Write/Bash 能力，由 `CLAUDE.md` / `AGENTS.md` 和 slash command 模板约束行为。不需要 API key。

## What Changes

- **新增模块 `stock_analyze/agent_briefing.py`**：纯函数 `build_weekly_briefing(agent_id)` / `build_monthly_briefing(agent_id, month)`，把当前状态（runs、信号、交易、持仓、净值、未成交、因子覆盖率、最近 IC、当前 overlay；月度时附加月度对比 JSON、近 4 篇周笔记、baseline 锁字段清单）汇总成 markdown 文档，开头明确"任务说明 + 输出路径 + 输出格式"。
- **CLI 子命令**：
  - `agent-prepare-weekly --agent <id>`：生成 `data/<id>/notes/briefings/<YYYY-MM-DD>-weekly.md`。
  - `agent-prepare-monthly --agent <id> [--month YYYY-MM]`：生成 `data/<id>/notes/briefings/<YYYY-MM>-monthly.md`。
- **自动化挂钩**：`run-weekly --agent <id>` 末尾自动调用 `agent-prepare-weekly`；`competition-monthly-review` 末尾对每个 agent 自动调用 `agent-prepare-monthly`。本地用户 rsync 后直接就有 briefing。
- **`CLAUDE.md`**：仓库根新增，Claude Code 入口；与现有 AGENTS.md 平行，定义 claude-side 身份、边界、CLI 工作流。
- **`AGENTS.md` 更新**：在现有内容上追加 "CLI analysis workflow" 一节，告诉 Codex 在收到 weekly/monthly 任务时怎么操作。
- **Claude Code slash commands**：`.claude/commands/weekly-review.md`、`.claude/commands/monthly-strategy.md`，参数化 agent 名，给 Claude Code 一个一键入口。
- **同步脚本**：
  - `scripts/sync-from-ecs.sh`：从 ECS rsync data/、configs/、reports/ 到本地。
  - `scripts/sync-to-ecs.sh`：把本地 `data/<agent>/notes/` 与 `data/<agent>/proposals/` 推回 ECS。
- **Dashboard 笔记面板**：`reporting.generate_dashboard` 在每个 agent 视图末尾追加"近期 agent 笔记"面板，以 `<details>` 折叠展示最近 5 篇 notes 的全文。
- **目录约定**：
  - `data/<agent>/notes/briefings/` — ECS 生成的输入任务包（agent 只读）。
  - `data/<agent>/notes/<YYYY-MM-DD>-weekly-review.md` — agent 周分析输出。
  - `data/<agent>/notes/<YYYY-MM>-monthly-review.md` — agent 月分析输出（文字版）。
  - `data/<agent>/proposals/<YYYY-MM>-strategy.json` — agent 月度策略提案（结构化 JSON）。

## Capabilities

### New Capabilities

- `agent-cli-analysis-workflow`：briefing 生成、CLI 入口、Claude Code/Codex CLI 操作契约、ECS↔本地同步约定、dashboard 笔记面板。

### Modified Capabilities

- 无新增 spec 修改；既有 dual-agent / runtime / dashboard 能力继续生效。

## Impact

- **代码**：1 个新模块 + 2 个新 CLI 子命令 + reporting.py 加一段笔记面板渲染。
- **配置**：无新增配置文件，无依赖变化。
- **文件**：
  - 新产物：`data/<agent>/notes/briefings/*.md`（ECS 自动生成）、`data/<agent>/notes/<date>-*.md`（agent 输出）、`data/<agent>/proposals/<month>-strategy.json`（agent 月度输出）。
  - 旧产物（state.json、daily_nav.csv 等）不变。
- **文档**：`CLAUDE.md`（新）、`AGENTS.md`（追加一节）、`docs/competition-runbook.md`（本地分析工作流章节）、`README.md` 顶部一行指引。
- **不在本次范围**：
  - agent 提案的"自动应用"。本次只产 proposal 文件 + dashboard 折叠展示；apply 留给下一 change `enable-monthly-config-evolution`。
  - 任何 LLM API 调用。
  - 钉钉/微信告警。
