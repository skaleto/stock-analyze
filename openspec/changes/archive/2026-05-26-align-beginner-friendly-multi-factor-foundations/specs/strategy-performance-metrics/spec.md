## ADDED Requirements

### Requirement: Daily return and benchmark return panel

`compute_performance` SHALL 从 `daily_nav.csv` 派生每个账户的日收益率与基准日收益率，并保留为后续计算的内部面板（不要求落盘成独立文件）。

#### Scenario: Daily return is computed from NAV deltas
- **GIVEN** 同一账户连续两个交易日的 `total_value`
- **WHEN** 绩效计算执行
- **THEN** 当日 `daily_return` = `total_value_t / total_value_{t-1} - 1`
- **AND** 第一天的 `daily_return` 为 NaN，不参与年化与超额计算

#### Scenario: Benchmark daily return aligned to account dates
- **GIVEN** 同一账户连续两个交易日的 `benchmark_close`
- **WHEN** 绩效计算执行
- **THEN** 基准日收益率按相同日期对齐
- **AND** 缺失基准价的日子被跳过，不参与超额收益累加

### Requirement: Annualized risk-adjusted metrics

`performance_summary.accounts[*]` SHALL 包含 `annualized_return`、`annualized_volatility`、`sharpe_ratio`、`sortino_ratio`、`max_drawdown`、`max_drawdown_days` 字段，使用 252 个交易日年化。

#### Scenario: Sharpe uses configured risk-free rate
- **GIVEN** `performance.risk_free_rate=0.02`
- **WHEN** 绩效计算执行
- **THEN** `sharpe_ratio = (annualized_return − 0.02) / annualized_volatility`
- **AND** 配置缺失时默认 0

#### Scenario: Sortino uses downside semideviation
- **GIVEN** 日收益序列含负值
- **WHEN** 绩效计算执行
- **THEN** `sortino_ratio = (annualized_return − risk_free_rate) / annualized_downside_volatility`
- **AND** `annualized_downside_volatility` 仅对负日收益样本计算标准差并年化

#### Scenario: Max drawdown days reports duration in calendar days
- **GIVEN** 净值曲线的最低点和创下该最低点之前的最高点
- **WHEN** 绩效计算执行
- **THEN** `max_drawdown_days` = 最高点到对应最低点之间的自然日差
- **AND** 净值尚未回到原高点时不重置该计数

### Requirement: Benchmark-relative metrics

`performance_summary.accounts[*]` SHALL 包含 `cumulative_excess_return`、`annualized_excess_return`、`tracking_error`、`information_ratio`。

#### Scenario: Cumulative excess return uses compounded difference
- **GIVEN** 账户与基准的日收益序列
- **WHEN** 绩效计算执行
- **THEN** `cumulative_excess_return = (∏(1 + r_account) − 1) − (∏(1 + r_benchmark) − 1)`
- **AND** 序列对齐到双方都有数据的日期集合

#### Scenario: Information ratio annualizes excess and tracking error
- **GIVEN** 日超额收益序列 `e_t = r_account_t − r_benchmark_t`
- **WHEN** 绩效计算执行
- **THEN** `tracking_error = std(e_t) × √252`
- **AND** `information_ratio = (mean(e_t) × 252) / tracking_error`

#### Scenario: Insufficient history returns null instead of crashing
- **GIVEN** 账户净值不足 2 个交易日
- **WHEN** 绩效计算执行
- **THEN** 风险/超额相关字段为 `null`
- **AND** dashboard 与报告显示 `-` 占位

### Requirement: Trading cost summary

`performance_summary.accounts[*]` SHALL 包含 `total_commission`、`total_stamp_tax`、`total_slippage`、`total_traded_value`、`cost_bps`（累计成本占累计成交金额）。

#### Scenario: Cost bps reflects all simulated trades
- **GIVEN** `trades.csv` 中累计成交金额 `T` 与成本之和 `C`
- **WHEN** 绩效计算执行
- **THEN** `cost_bps = (C / T) × 10000`
- **AND** 当 `T=0` 时 `cost_bps` 为 `null`

### Requirement: Turnover and win rate

`performance_summary.accounts[*]` SHALL 包含 `weekly_turnover_avg`、`avg_holding_days`、`round_trip_win_rate`、`round_trip_count`。

#### Scenario: Weekly turnover uses two-sided notional
- **GIVEN** 一周内所有 buy 与 sell 的 `gross_amount`
- **WHEN** 该周换手率计算
- **THEN** `turnover_week = (Σ|buy| + Σ|sell|) / 期初组合市值`
- **AND** 期初组合市值取该周第一个交易日的 `total_value`

#### Scenario: Round-trip uses FIFO matching
- **GIVEN** 同一账户同一代码的多笔买入与卖出
- **WHEN** 配对计算
- **THEN** 按 FIFO 顺序把卖出股数配对到最早未匹配的买入
- **AND** 每个完整 round-trip 记录 `entry_date`、`exit_date`、`holding_days`、`pnl_after_cost`

#### Scenario: Win rate counts profitable round-trips
- **GIVEN** N 个完整 round-trip
- **WHEN** 计算 win rate
- **THEN** `round_trip_win_rate = #(pnl_after_cost > 0) / N`
- **AND** 持仓未平仓的批次不计入

### Requirement: Dashboard performance panel

Dashboard SHALL 在净值曲线下方渲染绩效解释面板，按“收益 / 风险 / 超额 / 成本”四列展示本 capability 中新增的指标，每个数字附 tooltip 说明计算口径。

#### Scenario: Performance card lists annualized return, sharpe, IR, cost
- **WHEN** dashboard 生成
- **THEN** 至少出现 `年化收益`、`Sharpe`、`Sortino`、`年化超额`、`信息比率`、`换手率`、`成本(bps)`、`Win Rate` 八个数字卡片
- **AND** 每个卡片 hover 时显示计算口径与单位

#### Scenario: Missing metrics fall back to placeholder
- **GIVEN** 数据不足导致某指标为 `null`
- **WHEN** dashboard 渲染
- **THEN** 该卡片显示 `-` 并标记 `数据不足`
