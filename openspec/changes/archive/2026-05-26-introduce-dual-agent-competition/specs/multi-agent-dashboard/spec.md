## ADDED Requirements

### Requirement: Fragment-mode dashboard rendering

`stock_analyze/reporting.generate_dashboard` SHALL 接受 `mode` 参数，默认 `"page"`（产出完整 HTML 文档），`mode="fragment"` 时产出可嵌入的 HTML 片段（不含 `<html>/<head>/<body>` 外壳，但保留 `<style>` 与 `<script>` 块以便容器页直接拼装）。

#### Scenario: Default mode is page
- **WHEN** `generate_dashboard(config, store, reports_dir)` 调用
- **THEN** 输出包含 `<!doctype html>` 与 `<html>` 起始标记
- **AND** 文件名为 `dashboard.html`

#### Scenario: Fragment mode returns embeddable HTML
- **WHEN** `generate_dashboard(config, store, reports_dir, mode="fragment")` 调用
- **THEN** 输出文件名为 `dashboard_fragment.html`
- **AND** 文件首字符不是 `<!doctype html>`
- **AND** 输出包含一个根 `<section class="agent-dashboard">` 元素

### Requirement: Competition dashboard CLI command

CLI SHALL 提供 `competition-dashboard` 子命令，调用 `dashboard_aggregator.generate_competition_dashboard` 并写入 `reports/competition/dashboard.html`。

#### Scenario: Command writes competition dashboard
- **GIVEN** 两侧均存在 `reports/<agent>/dashboard_fragment.html`
- **WHEN** 运行 `python3 -m stock_analyze competition-dashboard`
- **THEN** 文件 `reports/competition/dashboard.html` 存在
- **AND** 文件包含三个 tab 锚点 `tab-claude`、`tab-codex`、`tab-compare`

### Requirement: Three-tab competition dashboard

聚合 dashboard SHALL 在单个 HTML 页面里包含三个 tab：`Claude` / `Codex` / `对比`；tab 切换使用 CSS `:target` 或同等效果的纯 CSS 方案，不依赖第三方 JS 框架。

#### Scenario: Three tabs present
- **WHEN** 打开 `reports/competition/dashboard.html`
- **THEN** 页面顶部三个 tab 链接分别为 `#tab-claude`、`#tab-codex`、`#tab-compare`
- **AND** 每个 tab 内容区使用 `:target` 控制显示

#### Scenario: Claude and Codex tab embed agent fragments
- **WHEN** dashboard 生成
- **THEN** `tab-claude` 区块包含 `reports/claude/dashboard_fragment.html` 的内容
- **AND** `tab-codex` 区块包含 `reports/codex/dashboard_fragment.html` 的内容

### Requirement: Comparison tab content

`对比` tab SHALL 包含：
- 顶部 4 张并列卡片（Claude 累计收益、Codex 累计收益、累计差、本月胜方）
- 双线 NAV 曲线（颜色固定）
- 横向指标对比表（累计收益/年化/Sharpe/IR/跟踪误差/最大回撤/换手/成本 bps/Win Rate 共 9 行）
- 最近持仓重叠条（三段：共有/Claude only/Codex only）
- 滚动战绩条（按月叠加，胜方着色）
- 月度报告链接列表

#### Scenario: Four metric cards at top
- **WHEN** 对比 tab 渲染
- **THEN** 含 4 个 `class="metric-card"` 元素
- **AND** 卡片标题分别为 "Claude 累计收益"、"Codex 累计收益"、"累计差"、"本月胜方"

#### Scenario: Comparison table has the required rows
- **WHEN** 对比 tab 渲染
- **THEN** 含一张 `<table class="comparison">`
- **AND** 表格行至少含 `累计收益, 年化收益, Sharpe, 信息比率, 跟踪误差, 最大回撤, 周换手率, 成本(bps), Win Rate` 九个标签

#### Scenario: NAV chart contains two series
- **WHEN** 对比 tab 渲染
- **THEN** `<canvas id="comparisonNav">` 存在
- **AND** 配套 JS 数据数组同时含 `claude` 与 `codex` 两条 NAV 序列

#### Scenario: Leaderboard strip shows monthly wins
- **GIVEN** `data/competition/leaderboard.csv` 含至少一行
- **WHEN** 对比 tab 渲染
- **THEN** 存在 `<section class="leaderboard-strip">`
- **AND** 每个月一个色块，颜色按 `winner_return` 决定

#### Scenario: Monthly report links
- **GIVEN** `reports/competition/monthly_review_*.md` 存在多个
- **WHEN** 对比 tab 渲染
- **THEN** 存在一个 `<ul class="monthly-review-links">`
- **AND** 列出按月份降序的所有月度报告链接

### Requirement: Empty-state graceful rendering

聚合 dashboard SHALL 在缺少某 agent fragment、缺少 leaderboard、缺少 NAV 数据时输出占位说明而非崩溃。

#### Scenario: Missing Codex fragment
- **GIVEN** `reports/codex/dashboard_fragment.html` 不存在
- **WHEN** 运行 `competition-dashboard`
- **THEN** Codex tab 显示占位 "尚未生成 Codex 仪表盘；请先跑 `--agent codex run-weekly`"
- **AND** Claude tab 与对比 tab 仍可渲染

#### Scenario: Empty leaderboard
- **GIVEN** `data/competition/leaderboard.csv` 不存在
- **WHEN** 对比 tab 渲染
- **THEN** 滚动战绩条显示占位 "尚未生成月度对比"
