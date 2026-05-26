## ADDED Requirements

### Requirement: Strategy evolution timeline panel

`reporting.generate_dashboard` SHALL 在每个 agent 视图（page 与 fragment 模式都生效）的"近期 agent 笔记"之后渲染一个"策略演进时间线"面板。面板列出 `data/<agent>/proposals/*-strategy.json` 中全部提案，按月份倒序，每行展示：月份、状态（`no_change` / 有变更）、`rationale` 摘要、`patch` 涉及的键路径列表、`risks`、当月与次月的实际累计收益（来自 `data/competition/leaderboard.csv`）。

#### Scenario: Timeline lists proposals in descending month order
- **GIVEN** `data/claude/proposals/` 含 `2026-04-strategy.json`、`2026-05-strategy.json`
- **WHEN** dashboard 渲染策略演进面板
- **THEN** 第一行的月份是 `2026-05`，第二行是 `2026-04`

#### Scenario: no_change proposals are visually flagged
- **GIVEN** 某月 proposal 含 `no_change=true` 且 `patch={}`
- **WHEN** 面板渲染该行
- **THEN** 该行带 `class="proposal-no-change"` 标记
- **AND** 行内显示 "本月维持" 文案

#### Scenario: Patch key paths are summarised
- **GIVEN** 某月 proposal 的 `patch={"factors":{"momentum_60":{"weight":0.05}},"portfolio_controls":{"max_industry_weight":0.25}}`
- **WHEN** 面板渲染该行
- **THEN** "改了哪些键" 列含 `factors.momentum_60` 与 `portfolio_controls.max_industry_weight`
- **AND** 不展开完整 JSON 值

#### Scenario: Leaderboard return is matched per month
- **GIVEN** `data/competition/leaderboard.csv` 含 `2026-05` 行（claude_return=0.06）
- **WHEN** 渲染 `2026-05` 提案行
- **THEN** "当月收益" 单元格显示 `+6.00%` 之类格式
- **AND** "次月收益" 列查 `2026-06` 行；若不存在则显示 `-`

#### Scenario: Empty proposals directory
- **GIVEN** `data/<agent>/proposals/` 不存在或为空
- **WHEN** 面板渲染
- **THEN** 显示占位 "尚未生成策略提案。月度 `/monthly-strategy <agent>` 跑完后会出现。"
- **AND** dashboard 其它部分继续渲染

### Requirement: Latest briefing panel

`reporting.generate_dashboard` SHALL 在策略演进面板之后渲染一个"本期分析任务包"面板，折叠展示 `data/<agent>/notes/briefings/` 中最新一份 `*-weekly.md`（如果存在）与最新一份 `*-monthly.md`（如果存在）的完整内容。

#### Scenario: Most recent weekly briefing is shown
- **GIVEN** `data/claude/notes/briefings/2026-05-22-weekly.md` 存在
- **WHEN** dashboard 渲染最新任务包面板
- **THEN** 面板含一个 `<details>` 块，summary 是文件名
- **AND** `<pre>` 内是该 markdown 完整 escape 后内容

#### Scenario: Monthly briefing also surfaced when present
- **GIVEN** `data/claude/notes/briefings/` 同时含 `2026-05-22-weekly.md` 与 `2026-05-monthly.md`
- **WHEN** 面板渲染
- **THEN** 含两个 `<details>` 块：一个 weekly，一个 monthly
- **AND** 周度排在月度之前

#### Scenario: Empty briefings directory
- **GIVEN** `data/<agent>/notes/briefings/` 不存在或为空
- **WHEN** 面板渲染
- **THEN** 显示占位 "ECS 还没生成 briefing。下次 `run-weekly --agent <agent>` 跑完会出现。"

### Requirement: Cross-agent weekly observation pairing in compare tab

`dashboard_aggregator.generate_competition_dashboard` SHALL 在"对比"tab 末尾渲染一个"本周双方观察对照"区段，并列展示 `data/{claude,codex}/notes/` 下最新一份非 briefing 的 `*-weekly-review.md`（按 mtime 取最新）。

#### Scenario: Both agents have a latest weekly note
- **GIVEN** 两侧均有 `*-weekly-review.md`
- **WHEN** 聚合 dashboard 渲染对比 tab
- **THEN** "本周双方观察对照" 区段含两个并列的 `<details>` 面板
- **AND** 左侧 summary 写 "Claude · `<filename>`"，右侧写 "Codex · `<filename>`"

#### Scenario: Only one agent has a note
- **GIVEN** 只有 `data/claude/notes/2026-05-22-weekly-review.md` 存在
- **WHEN** 渲染对比 tab
- **THEN** Claude 一侧渲染笔记；Codex 一侧显示 "Codex 本周无笔记"

#### Scenario: Neither agent has a note
- **GIVEN** 两侧 `notes/` 都没有非-briefing 文件
- **WHEN** 渲染对比 tab
- **THEN** 该区段显示 "尚未生成 agent 周笔记。运行 `/weekly-review claude` / `do weekly review for codex` 后会出现。"

### Requirement: HTML safety for new panels

所有新面板 SHALL 对 markdown 文本内容进行 HTML escape（至少处理 `&`、`<`、`>`），并在内容超长（≥ 16KB）时截断并附 `…(truncated)` 标记，避免 dashboard 解析失败或文件过大。

#### Scenario: Markdown angle brackets are escaped
- **GIVEN** 一份笔记含 `<not-a-tag>` 文本
- **WHEN** 渲染到 dashboard
- **THEN** HTML 中显示为 `&lt;not-a-tag&gt;`
- **AND** 浏览器不会把它解析为标签

#### Scenario: Long content is truncated
- **GIVEN** 一份笔记长度超过 16KB
- **WHEN** 渲染
- **THEN** 面板显示前 16KB 内容并附加 `…(truncated)` 提示
