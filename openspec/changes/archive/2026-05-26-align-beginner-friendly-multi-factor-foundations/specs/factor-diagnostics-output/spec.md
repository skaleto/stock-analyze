## ADDED Requirements

### Requirement: Per-run factor snapshot CSV

每次 `run-weekly` SHALL 在 `data/factor_runs/<run_id>.csv` 中写入当周完整因子明细（raw、winsorized、zscore、neutralized、weight、contribution）。

#### Scenario: Snapshot is written after a successful weekly run
- **WHEN** `run-weekly` 成功完成
- **THEN** `data/factor_runs/<run_id>.csv` 存在
- **AND** 包含每只候选每个因子一行记录

#### Scenario: Snapshot retains rejected candidates for audit
- **GIVEN** 某只候选因覆盖不足或行业上限被剔除
- **WHEN** factor snapshot 写入
- **THEN** 该候选的所有因子原值与中间结果仍写入文件
- **AND** 同一行附加 `selected=false` 与 `rejected_reason` 列

### Requirement: Cumulative coverage and distribution log

策略 SHALL 维护 `data/factor_diagnostics/coverage.csv`，每周追加一行记录每个因子的覆盖率与分布。

#### Scenario: Coverage log appends one row per factor per week
- **WHEN** `run-weekly` 成功完成
- **THEN** `data/factor_diagnostics/coverage.csv` 中按当周 signal_date 与 account_id 追加每个因子一行
- **AND** 每行至少包含列 `signal_date, account_id, factor, coverage_pct, missing_count, mean, p5, p50, p95, std`

#### Scenario: Low coverage triggers warning
- **GIVEN** 某因子在某账户当周 `coverage_pct < 0.5`
- **WHEN** 信号写入
- **THEN** 当周 signal warnings 增加 `factor_low_coverage:<factor>`
- **AND** dashboard 在因子诊断面板高亮该因子

### Requirement: Forward IC accumulator

当 NAV 历史包含某 signal_date 之后 ≥ 5 个交易日的实际收益时，策略 SHALL 计算每个因子的 5 日前向 Spearman rank IC 并追加到 `data/factor_diagnostics/forward_ic.csv`。

#### Scenario: Forward IC is computed once enough forward returns exist
- **GIVEN** 一份 signal_date 为 T 的因子 snapshot 已写入
- **WHEN** 之后某次运行的 `as_of` ≥ T + 5 个交易日
- **THEN** 系统按当时各股票的 z-score 与 T 到 T+5 实际收益做 Spearman rank IC
- **AND** 结果 append 到 `forward_ic.csv`，列至少包含 `signal_date, account_id, factor, ic, sample_size, ic_status`

#### Scenario: IC is marked insufficient when history is short
- **GIVEN** signal_date 后不足 5 个交易日
- **WHEN** 系统尝试计算 IC
- **THEN** 写入一行 `ic=NaN, ic_status="insufficient_history"`
- **AND** 后续到达足够历史时按 signal_date 回填一行 `ic` 数值，原 `insufficient_history` 行保留

#### Scenario: IC uses Spearman rank correlation
- **GIVEN** 因子 z-score 序列与 5 日实际收益序列
- **WHEN** IC 计算
- **THEN** 使用 Spearman rank 相关而非 Pearson
- **AND** 完美正相关的人造样本返回 IC ≈ 1，完美反相关返回 IC ≈ −1

### Requirement: Factor diagnostics dashboard panel

Dashboard SHALL 在“因子贡献均值”之外，渲染因子覆盖率与最近 N 周前向 IC 两个新视图。

#### Scenario: Coverage panel shows recent N weeks
- **WHEN** dashboard 生成
- **THEN** 因子诊断面板渲染最近 ≤ 12 周的覆盖率，按因子横向列出
- **AND** 当周覆盖率低于阈值时该因子单元格高亮

#### Scenario: Forward IC panel shows recent N weeks per factor
- **WHEN** dashboard 生成
- **THEN** 渲染最近 ≤ 12 周的前向 IC 折线
- **AND** `ic_status="insufficient_history"` 的点显示为占位线段

#### Scenario: Diagnostics gracefully empty on first run
- **GIVEN** 系统刚初始化，无任何 signal 历史
- **WHEN** dashboard 生成
- **THEN** 因子诊断面板显示“尚无因子诊断数据，跑过至少一次 run-weekly 后再观察”
- **AND** dashboard 仍能完整渲染其它面板
