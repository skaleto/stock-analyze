## ADDED Requirements

### Requirement: Monthly review CLI command

CLI SHALL 提供 `competition-monthly-review --month YYYY-MM` 子命令；月份缺省时取上个月（运行日的上个自然月）。

#### Scenario: Default month is previous calendar month
- **GIVEN** 今天是 `2026-06-03`
- **WHEN** 运行 `python3 -m stock_analyze competition-monthly-review`
- **THEN** 命令默认处理月份 `2026-05`
- **AND** 写入 `data/competition/monthly_reviews/2026-05.json` 与 `reports/competition/monthly_review_2026-05.md`

#### Scenario: Explicit month
- **WHEN** 运行 `python3 -m stock_analyze competition-monthly-review --month 2026-04`
- **THEN** 命令处理月份 `2026-04`

### Requirement: Per-agent metric block

月度 review SHALL 为每个参赛 agent 输出一份指标块，含至少以下字段：`cumulative_return, annualized_return, annualized_volatility, sharpe_ratio, sortino_ratio, max_drawdown, information_ratio, tracking_error, weekly_turnover_avg, cost_bps, round_trip_win_rate, factor_ic_top3, industry_exposure_top3, active_factors, config_hash`。

#### Scenario: Both agents produce a metric block
- **GIVEN** 两侧均跑过若干次 run-weekly 并积累了至少一周 NAV
- **WHEN** 运行 `competition-monthly-review --month <month>`
- **THEN** 输出 JSON 的 `agents.claude` 与 `agents.codex` 均存在
- **AND** 两个块都包含上述字段（缺数据时为 `null`）

#### Scenario: factor_ic_top3 sourced from forward_ic.csv
- **GIVEN** `data/claude/factor_diagnostics/forward_ic.csv` 含本月 `ic_status="ok"` 的多行
- **WHEN** review 计算 `agents.claude.factor_ic_top3`
- **THEN** 该字段是按本月平均 IC 降序排列的前 3 个因子（list of `[factor, mean_ic]`）

### Requirement: Comparison block

月度 review SHALL 输出 comparison 块，含 `winner_cumulative_return, winner_information_ratio, spread_cumulative_return, position_overlap_ratio, daily_return_correlation, shared_factor_drivers, divergent_factor_drivers`。

#### Scenario: winner_cumulative_return reflects higher cumulative_return
- **GIVEN** `agents.claude.cumulative_return=0.08`、`agents.codex.cumulative_return=0.05`
- **WHEN** comparison 计算
- **THEN** `winner_cumulative_return="claude"`
- **AND** `spread_cumulative_return=0.03`

#### Scenario: position_overlap_ratio is Jaccard on latest positions
- **GIVEN** 双方各持 10 只股票，其中 4 只代码重叠
- **WHEN** 计算 `position_overlap_ratio`
- **THEN** 值约等于 `4 / (10 + 10 - 4) = 0.25`

#### Scenario: daily_return_correlation uses overlapping dates
- **GIVEN** 双方在本月有 20 个共同 NAV 日期
- **WHEN** 计算相关性
- **THEN** 取双方 daily_return 序列在共同日期的 Pearson 相关系数

#### Scenario: divergent_factor_drivers lists asymmetric top factors
- **GIVEN** `claude.factor_ic_top3=[pe, roe, momentum_60]`、`codex.factor_ic_top3=[roe, low_volatility_60, dividend_yield]`
- **WHEN** 计算 `divergent_factor_drivers`
- **THEN** 输出 `{claude_only:[pe, momentum_60], codex_only:[low_volatility_60, dividend_yield]}`
- **AND** `shared_factor_drivers=[roe]`

### Requirement: Human-readable monthly report

月度 review SHALL 同时输出 Markdown 报告 `reports/competition/monthly_review_<month>.md`，含：
- 顶部 metadata（`competition_id`, 月份, baseline_hash, 两侧 config_hash）
- 双方指标横向对比表
- 比较结果摘要
- 因子有效性与持仓重叠度说明
- 一段非命令性的"差异化建议"自然语言段（基于对比数据机械生成，不构成投资建议）

#### Scenario: Markdown contains both agents in a single table
- **WHEN** 月度 review 输出 Markdown
- **THEN** 报告中存在一张表，列为 `指标 | Claude | Codex | 胜方`
- **AND** 至少包含累计收益、年化、Sharpe、IR、最大回撤、换手、成本、Win Rate 八行

#### Scenario: Markdown disclaimer present
- **WHEN** 月度 review 输出 Markdown
- **THEN** 报告底部含一段说明"本报告仅基于模拟交易数据，不构成投资建议"

### Requirement: Rolling leaderboard CSV

月度 review SHALL 维护 `data/competition/leaderboard.csv`：每次成功 review 追加一行 `month, claude_return, codex_return, winner_return, claude_ir, codex_ir, winner_ir`，并对同 month 进行 upsert（保留最新）。

#### Scenario: First review writes new row
- **GIVEN** `data/competition/leaderboard.csv` 不存在
- **WHEN** 跑首次 `competition-monthly-review --month 2026-05`
- **THEN** 文件被创建，包含表头与一行 `2026-05` 数据

#### Scenario: Re-run same month upserts
- **GIVEN** leaderboard 已含 `2026-05` 行
- **WHEN** 重新运行 `competition-monthly-review --month 2026-05`（例如修复了数据后）
- **THEN** `2026-05` 行被新数据替换
- **AND** 文件仍仅含一行 `2026-05`
