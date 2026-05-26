## ADDED Requirements

### Requirement: Single-industry weight cap

组合构建 SHALL 在选 TopN 时强制单行业权重不超过 `portfolio_controls.max_industry_weight`（默认 0.30）。

#### Scenario: Top-ranked stock skipped when industry cap is hit
- **GIVEN** 当前已入选股票中“银行”行业权重已达上限
- **WHEN** 下一只综合分最高的候选股属于“银行”
- **THEN** 该股票被跳过
- **AND** 从下一行业里综合分最高的候选中补位

#### Scenario: Cap is relaxed only when needed to fill top_n
- **GIVEN** 应用单行业上限后剩余候选不足 `top_n`
- **WHEN** 选股逻辑无法在所有行业里凑齐 `top_n`
- **THEN** 选股放宽上限并按综合分继续补位
- **AND** 当周信号写 warning `industry_cap_relaxed`

#### Scenario: Unclassified stocks are an isolated bucket
- **GIVEN** 多只候选股票的 `industry` 字段为空
- **WHEN** 上限检查执行
- **THEN** 这些股票被视为同一桶 `未分类` 并各自计入该桶占比
- **AND** 该桶占比也受 `max_industry_weight` 约束

### Requirement: Holding buffer reduces churn

组合构建 SHALL 对当前持有的股票启用 buffer 区间：排名在 `[top_n, top_n × (1 + portfolio_controls.hold_buffer_pct)]`（默认 buffer 0.5）之间的持仓不卖出。

#### Scenario: Existing position stays inside buffer zone
- **GIVEN** 当前持仓 A 的最新综合分排名为 13，`top_n=10`、`hold_buffer_pct=0.5`
- **WHEN** 选股完成
- **THEN** A 仍在持仓列表中（不被卖出）
- **AND** 不挤占新入选名额

#### Scenario: Existing position outside buffer is sold
- **GIVEN** 当前持仓 B 的最新综合分排名为 17，`top_n=10`、`hold_buffer_pct=0.5`
- **WHEN** 选股完成
- **THEN** B 出现在卖出订单里
- **AND** 卖出原因为 `dropped_outside_buffer`

#### Scenario: Maximum holding days forces re-evaluation
- **GIVEN** 当前持仓 C 已持有 ≥ `portfolio_controls.max_holding_days`（默认 60 个交易日）
- **WHEN** 周度选股运行
- **THEN** C 即使在 buffer 区间内也按当周综合分重新决定是否保留
- **AND** 重新评估的事件被写入 `data_warnings`

### Requirement: Market cap demoted from alpha to risk filter

策略默认 `factors` SHALL 不包含 `market_cap_yi`；市值 SHALL 通过 `filters.min_market_cap_yi` 与可选 `filters.max_market_cap_yi` 控制候选池范围。

#### Scenario: Default config excludes market cap as alpha
- **GIVEN** `configs/strategy_v1.yaml` v2
- **WHEN** 配置被加载
- **THEN** `factors` 不含 `market_cap_yi`
- **AND** `filters.min_market_cap_yi=30`（亿）作为流动性下限

#### Scenario: Legacy v1 config with market cap is migrated
- **GIVEN** 用户配置仍把 `market_cap_yi` 放在 `factors`
- **WHEN** `load_config()` 解析后调用迁移层
- **THEN** `market_cap_yi` 被移出 `factors`
- **AND** `filters.min_market_cap_yi` 被设为 30（如未配置）
- **AND** 进程内打印一次性 warning `config_v1_market_cap_demoted`

### Requirement: Optional defensive factors are available

策略 SHALL 支持 `low_volatility_60` 与 `dividend_yield` 两个可选因子，默认权重 0；通过 preset 配置开启时不需改动代码。

#### Scenario: Low volatility factor computed from price history
- **GIVEN** 一只股票具有 ≥ 60 个交易日的复权日 K
- **WHEN** 信号生成
- **THEN** `low_volatility_60` = 过去 60 个交易日日收益率的样本标准差
- **AND** 在 `factors` 中配置 `direction=low` 后纳入综合分

#### Scenario: Dividend yield falls back to derived value
- **GIVEN** AkShare 个股信息没有直接给出 TTM 股息率
- **WHEN** 数据层尝试拼接
- **THEN** 从最近一年现金分红除以最新价派生 TTM 股息率
- **AND** 派生数据被记录在 `data_health.json` 并写入 `dividend_yield`

### Requirement: Documented strategy presets

仓库 SHALL 至少提供一份替代 preset 配置文件，演示如何在不改代码的前提下切换因子组合。

#### Scenario: Quality + low volatility preset exists
- **GIVEN** 仓库根目录
- **WHEN** 在 `configs/` 下查找
- **THEN** 存在 `preset_quality_low_vol.yaml`
- **AND** 该 preset 的 `factors` 至少启用 `roe`、`gross_margin`、`low_volatility_60` 三个因子并配权重总和为 1.0

#### Scenario: Preset can be selected through --config without code changes
- **GIVEN** 用户运行 `python -m stock_analyze --config configs/preset_quality_low_vol.yaml run-weekly`
- **WHEN** 命令执行
- **THEN** 命令使用 preset 中定义的因子和约束生成信号
- **AND** dashboard 和报告反映 preset 名称与对应的 `strategy_id`
