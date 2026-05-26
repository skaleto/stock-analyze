## ADDED Requirements

### Requirement: Cross-sectional winsorization

策略在每个截面（signal date × account）内 SHALL 对每个数值因子按配置的下/上百分位（默认 0.01 / 0.99）做 winsorize 夹边后再进入后续标准化流水线。

#### Scenario: Extreme PE is clipped before standardization
- **GIVEN** 候选池里某只股票 PE 值远高于其它候选（例如 1000+）
- **WHEN** 周度信号生成
- **THEN** 该 PE 在 winsorize 后被夹到当周截面的 99 分位
- **AND** 该股的 z-score 不会显著拉宽其余股票的标准化分布

#### Scenario: Winsorize is configurable
- **GIVEN** 配置文件设置 `factor_processing.winsorize_lower=0.05` 与 `factor_processing.winsorize_upper=0.95`
- **WHEN** 策略读取配置
- **THEN** 截面 winsorize 在 5% / 95% 处夹边
- **AND** 缺失配置时使用默认 1% / 99%

### Requirement: Cross-sectional z-score standardization

策略在 winsorize 之后 SHALL 对每个因子做截面 z-score 标准化（`(x − mean) / std`），保证多因子加权前各因子量级一致。

#### Scenario: Each factor mean is zero after standardization
- **GIVEN** 一个截面内某因子的全部有效值
- **WHEN** 标准化流水线执行完毕
- **THEN** 该因子标准化后的截面均值约为 0（绝对值 < 1e-6）
- **AND** 标准化后的截面标准差约为 1

#### Scenario: All-equal factor does not divide by zero
- **GIVEN** 一个因子在当周截面内所有有效值相等
- **WHEN** 标准化执行
- **THEN** 该因子的 z-score 全部为 0
- **AND** 流水线不会抛出除以零异常

### Requirement: Industry-neutral demeaning

当行业字段可用时，标准化后的因子 z-score SHALL 在行业内 demean（减去行业均值），缺失行业归入 `未分类` 单独成组。

#### Scenario: Industry demeaning removes within-industry mean
- **GIVEN** 同一行业内某因子 z-score 的均值为 m
- **WHEN** 行业中性化执行
- **THEN** 该行业内 demean 后的均值约为 0
- **AND** 跨行业的总体分布形状不变

#### Scenario: Missing industry falls back to a dedicated bucket
- **GIVEN** 某些候选股票的 `industry` 字段为空
- **WHEN** 行业中性化执行
- **THEN** 这些股票被归入 `未分类` 桶并在该桶内单独 demean
- **AND** 它们不会与已分类股票合并计算行业均值

#### Scenario: Industry neutralization can be disabled
- **GIVEN** 配置 `factor_processing.neutralize_industry=false`
- **WHEN** 策略读取配置
- **THEN** 中性化步骤被跳过
- **AND** 标准化后的 z-score 直接进入加权汇总

### Requirement: Coverage-aware weight renormalization

每只候选股票 SHALL 按其实际有效因子的权重在该股票内归一，缺失因子不再以 0 加分。

#### Scenario: Stock with partial coverage uses available weights
- **GIVEN** 一只股票在该截面只有 4/6 个因子有效，且这 4 个因子的配置权重之和为 0.7
- **WHEN** 综合分计算
- **THEN** 该股的综合分 = Σ(valid_factor_zscore × signed_weight) / 0.7
- **AND** 其分数与因子全有效的股票在同一可比量级

#### Scenario: Insufficient coverage drops the stock
- **GIVEN** 一只股票的有效因子权重之和 / 全部因子总权重 < `factor_processing.min_factor_coverage`（默认 0.6）
- **WHEN** 综合分计算
- **THEN** 该股票被剔除出候选
- **AND** `data_warnings` 字段记录 `insufficient_factor_coverage`

### Requirement: Direction sign applied after standardization

`factors[*].direction` SHALL 作用在标准化后的 z-score 上，而不是作用在原始排名上。

#### Scenario: Direction low flips standardized score
- **GIVEN** PE 因子 `direction=low`
- **WHEN** 综合分计算
- **THEN** PE 的标准化值乘以 −1 后再加权
- **AND** PE 越低的股票综合分贡献越大

### Requirement: Reproducible factor snapshot per run

每次 `run-weekly` SHALL 把当周完整因子计算过程写入 `data/factor_runs/<run_id>.csv`，覆盖原值、winsorized、zscore、neutralized、weight、contribution。

#### Scenario: Factor snapshot exists after a weekly run
- **WHEN** `run-weekly` 成功完成
- **THEN** `data/factor_runs/<run_id>.csv` 存在
- **AND** 文件包含每只候选每个因子的一行记录，列至少包含 `account_id, code, factor, raw, winsorized, zscore, neutralized, weight, contribution`

#### Scenario: Snapshot allows reproducing the composite score
- **GIVEN** 某次跑的 `factor_runs/<run_id>.csv`
- **WHEN** 把 `neutralized × weight` 按 `(account_id, code)` 求和
- **THEN** 结果等于 `latest_signals.csv` 中同一行的 `score`（容差 < 1e-6）
