## ADDED Requirements

### Requirement: Beginner dashboard rendered alongside pro dashboard

`stock_analyze.dashboard_aggregator.generate_competition_dashboard` SHALL output two HTML views in a single invocation:

1. `reports/competition/dashboard.html` — existing 3-tab professional view (unchanged content)
2. `reports/competition/simple.html` — NEW beginner-friendly view

#### Scenario: Both files written by one command
- **WHEN** `python3 -m stock_analyze competition-dashboard` runs
- **THEN** both `reports/competition/dashboard.html` and `reports/competition/simple.html` exist
- **AND** `dashboard.html` size unchanged from pre-change(within ±10%)
- **AND** `simple.html` ≤ 80 KB

### Requirement: Beginner view shows 8 sections in fixed order

`simple.html` SHALL contain exactly the following sections, in this order:

1. Tab bar with [简化版] / [专业版] / [策略演进] links
2. 账户总览卡片(总资产 / 今日变动 / 本月变动)
3. 双 agent 成绩卡片(累计收益 / vs 沪深300 / vs 中证500)
4. NAV 双线图(Claude / Codex / 沪深300 / 中证500)
5. Claude 持仓 Top 10
6. Codex 持仓 Top 10
7. 持仓重叠摘要(都买 / 都卖 / 各自独有)
8. 近期模拟成交 Top 5
9. 本月策略调整摘要(可选,如果当月有 evolution_log)

(注:tab bar 严格说是 section 0)

#### Scenario: Section order matches spec
- **GIVEN** simple.html 已生成
- **WHEN** 解析 HTML(BeautifulSoup 或 regex 提取 `<section data-id="N">`)
- **THEN** 8 个 section id 按 1..8 顺序出现

#### Scenario: Missing data renders graceful placeholder
- **GIVEN** Claude 账户尚无成交(positions.csv 为空)
- **WHEN** simple.html 渲染
- **THEN** Section 5 显示 "尚未开盘交易",不崩溃
- **AND** 整页大小仍 ≤ 80 KB

### Requirement: Beginner view excludes professional content

`simple.html` SHALL NOT include any of the following:

- Factor coverage heat map
- Forward RankIC charts
- Per-stock factor contribution breakdown
- runs.csv table
- data source health status
- agent notes content
- factor_runs/*.csv content
- briefings content

#### Scenario: Pro-only markers absent
- **WHEN** `grep` simple.html for 关键字 "因子覆盖率" / "前向 IC" / "数据源状态" / "因子贡献明细" / "运行账本"
- **THEN** 全部 0 命中

### Requirement: CNY and percentage formatted in Chinese style

数字 SHALL 用以下中文格式:

- 现金:`1,234元` / `1.23万元` / `1.23亿元`(自动选合适量级)
- 百分比:`+1.32%` / `-0.84%`(带正负号,2 位小数)
- 日期相对:`今天` / `昨天` / `上周二` / `上月15日` / `2025年12月18日`(超过一年前才带年份)

#### Scenario: cny() formats with appropriate scale
- **GIVEN** `cny(1234) == "1,234元"`
- **AND** `cny(12345) == "1.23万元"`
- **AND** `cny(123456789) == "1.23亿元"`

#### Scenario: pct() with signed
- **GIVEN** `pct(0.0132) == "+1.32%"`
- **AND** `pct(-0.0084) == "-0.84%"`
- **AND** `pct(0) == "0.00%"`

### Requirement: HTTP root routes to beginner by default

`serve-dashboard` HTTP server SHALL route `GET /` to `reports/competition/simple.html`(beginner view default),and SHALL provide aliases:

- `GET /pro.html` → `reports/competition/dashboard.html`
- `GET /simple.html` → `reports/competition/simple.html`
- `GET /simple/claude.html` → `reports/competition/simple/claude.html`
- `GET /simple/codex.html` → `reports/competition/simple/codex.html`
- 现有 `GET /competition/dashboard.html` 不变(向后兼容)

#### Scenario: Default route is beginner
- **GIVEN** server started at 127.0.0.1:8765
- **WHEN** `curl --noproxy '*' http://127.0.0.1:8765/`
- **THEN** HTTP 200 + Content-Type text/html
- **AND** body 包含 `<a class="tab active" href="/simple.html">简化版</a>`
- **AND** body 不包含 "因子覆盖率"(确认是 simple 不是 pro)
