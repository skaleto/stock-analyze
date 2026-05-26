## Why

P0 修正（`harden-forward-simulation-correctness`）已经让模拟成交不再吃未来行情、不再静默丢单、不再重复污染 NAV。现在前向模拟可以稳定跑，但策略本身离“主流多因子最佳实践”仍有明显差距，导致即使结果好看也很难判断是策略起作用还是巧合：

- 因子打分只做横截面百分位排名，没有 winsorize、z-score、行业中性化，极端值和行业风格暴露会主导排名。
- `market_cap_yi` 被当作 alpha 因子且方向是“越大越好”，把组合往大盘股推，相当于偷偷下了一个风格押注，而不是在赚价值/质量/动量的钱。
- 缺失因子按 0 加分，等价于把缺失股票排到该因子的最低分；这会混淆“数据缺失”和“因子确实差”，并对部分小盘股不公平。
- 每周完全重排 TopN，没有持仓缓冲，单股排名波动 1 名就会触发换手；扣完佣金/印花税/滑点后预期收益被吃光。
- 选股没有行业上限；同一周期可能 10 只里有 4-5 只集中在一个行业，行业 beta 会主导收益。
- 绩效摘要只有累计收益和最大回撤，看不到年化收益、夏普、超额收益、跟踪误差、信息比率、换手、win rate、成本占比。新手看完 dashboard 也不知道是策略赚了、市场涨了，还是被成本拖累。
- 没有因子诊断（覆盖率、IC、分组收益），无法回答“这些因子在当前股票池里到底有没有解释力”。
- 没有运行账本，`latest_signals.csv` 会被覆盖；同一份 dashboard 没法复现到当时跑的配置与代码版本。

这些不是 P0 正确性 bug，而是“新手入门量化方案应当具备的工程与方法学基线”。在补这些之前，加更多因子或换更复杂模型只会放大噪音。

## What Changes

引入一个聚焦的“多因子基础对齐”能力包，覆盖 5 个可独立验收的能力域：

- **因子处理流水线**：对每个截面做 winsorize → z-score → 行业内中性化 → 加权汇总；按股票实际可用因子重新归一权重，覆盖度低于阈值的股票直接剔除而不是补 0。
- **组合构建控制**：把 `market_cap_yi` 从 alpha 移到流动性/风险过滤；新增低波、股息率两个新手友好的防御因子（默认关闭，可在 preset 里开启）；加单行业权重上限、持仓缓冲（hold buffer）减少换手。
- **策略绩效与归因**：扩充 `performance_summary.json` 与 dashboard，补齐年化收益、年化波动、Sharpe、Sortino、回撤天数、累计超额、年化超额、跟踪误差、信息比率、加权换手率、成本占比（bps）、完整 round-trip 胜率。
- **因子诊断输出**：每周写入按 `run_id` 归档的因子明细 CSV（原值、winsorized、z-score、neutralized、权重、贡献）；按周累积每个因子的覆盖率、分位分布；当 NAV 历史足够时计算前向 5 日 RankIC 并写入 `forward_ic.csv`。
- **运行账本与配置快照**：每次 CLI 命令写入 `data/runs.csv`（`run_id, command, as_of, started_at, finished_at, status, error_summary, config_hash, code_version`）；新出现的 `config_hash` 把完整配置快照写入 `data/configs/<hash>.json`。

新增配置项：`factor_processing.*`、`portfolio_controls.*`、`performance.risk_free_rate`、`filters.min_market_cap_yi` / `max_market_cap_yi`。`market_cap_yi` 不再出现在默认 `factors`；现有配置如果仍含该键，会按 v1→v2 迁移提示给出 warning 并按风险过滤处理。

新增 preset 示例：`configs/preset_quality_low_vol.yaml`（质量+低波，关闭动量、降低单行业上限），用于演示新手在不写代码的前提下切换风格。

## Capabilities

### New Capabilities

- `factor-processing-pipeline`：横截面 winsorize、z-score 标准化、行业内中性化、缺失值按可用因子归一、composite score 计算与可复现因子明细。
- `portfolio-construction-controls`：单行业权重上限、持仓缓冲、市值降级为风险过滤、可选低波/股息因子、preset 配置示例。
- `strategy-performance-metrics`：年化收益/波动、Sharpe/Sortino、回撤持续时间、累计/年化超额、跟踪误差、信息比率、换手、成本 bps、win rate 等指标计算与展示。
- `factor-diagnostics-output`：按 run_id 输出因子明细 CSV、累计因子覆盖率与分布、前向 RankIC 累加器与 dashboard 面板。
- `run-ledger-and-config-snapshot`：`data/runs.csv` 运行账本、按 `config_hash` 归档的完整配置快照、`code_version` 捕获、dashboard 最近运行面板。

### Modified Capabilities

- 无（本变更不删除或修改 `forward-simulation-runbook`、`data-source-resilience` 里已收口的需求；它们继续生效，新需求作为补充）。

## Impact

- **代码影响**：
  - `stock_analyze/strategy.py`：替换 `score_candidates` 中的简单排名为标准化流水线；新增 `winsorize`、`zscore`、`neutralize_by_industry` 函数；调整 `apply_hard_filters` 接受可选市值上下限。
  - `stock_analyze/simulator.py`：`build_target_orders` 加入行业上限与持仓缓冲；保留旧路径作为 `portfolio_controls` 全部关闭时的行为。
  - `stock_analyze/data_provider.py`：扩展 `financial_metrics`/`price_snapshot` 输出股息率与 60 日波动，提供行业字段；行业字段缺失时降级标签为 "未分类"。
  - `stock_analyze/reporting.py`：`compute_performance` 重写为带超额、跟踪误差、IR、换手、成本占比；dashboard 新增绩效解释面板、因子诊断面板、运行账本面板。
  - `stock_analyze/store.py`：新增 `append_run_ledger`、`write_factor_snapshot`、`write_config_snapshot`、`append_forward_ic`，并保留旧文件结构向后兼容。
  - `stock_analyze/cli.py`：每条命令包装 run-ledger 起止；命令失败时把 `failed` + `error_summary` 写回账本。

- **配置影响**：
  - 默认 `configs/strategy_v1.yaml` 升级为 v2：从 `factors` 移除 `market_cap_yi`，新增 `factor_processing`、`portfolio_controls` 节；保留 `filters.min_market_cap_yi` 留给后续 preset 使用。
  - 兼容性：当 v1 配置出现 `market_cap_yi` 时，加载器把它折叠到 `filters.min_market_cap_yi` 并打印一次性 warning。

- **数据/文件影响**：
  - 新增 `data/runs.csv`、`data/configs/<hash>.json`、`data/factor_runs/<run_id>.csv`、`data/factor_diagnostics/coverage.csv`、`data/factor_diagnostics/forward_ic.csv`。
  - 现有 `state.json`、`pending_orders.json`、`daily_nav.csv`、`trades.csv`、`positions.csv`、`latest_signals.csv` 保持向后兼容；新增字段为追加，旧读者忽略未识别列。

- **依赖影响**：无新增第三方依赖；所有计算仍走 `pandas`/`numpy`。

- **文档影响**：
  - 更新 `docs/forward-simulation-runbook.md` 说明新指标、新文件、新配置项。
  - 新增 `docs/quant-beginner-alignment-plan-2026-05-19.md`（人类可读的方案与时间线），与本 OpenSpec change 对应。
  - 更新 `docs/quant-model-gap-review-2026-05-18.md` 末尾追加一段“P1 落地状态指针”链接到本 change。

- **风险/范围控制**：
  - 不在本次范围：历史回测引擎、PIT 财务公告日、历史指数成分库、SQLite/DuckDB run ledger、组合优化器、券商接口。这些保留给后续 change（建议名 `add-historical-backtest-baseline`、`introduce-point-in-time-fundamentals`）。
