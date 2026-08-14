# 证据优先的策略恢复计划

> 日期：2026-08-14
> Campaign ID：`strategy-recovery-20260814-v1`
> 适用范围：A 股 `hs300` / `zz500`，跨境 ETF `hk_exposure` / `us_exposure`
> 目标：用一次有固定边界的完整研究战役，回答“哪些策略具备真实、可执行、净成本后优势”，而不是继续追逐“必须训练出一个机器学习模型”。

## 1. 先冻结现状

以下事实作为本计划的起点，不再通过改门槛或换叙述规避：

1. 当前四个 Ridge 残差候选均未给透明基线增加净收益，不能晋升。
2. A 股两个透明动量基线在本轮开发回放中均为负超额。
3. 港股暴露 QDII 的透明趋势基线在三个开发折中均为正超额，汇总净超额为正；但当前通用的换手率与资金利用率门槛不适合直接判定趋势/择时策略，因此它只能被视为“值得重新按正确语义验收”，不能据此直接上线。
4. 美股暴露 QDII 的透明趋势基线与机器学习残差均明显为负。
5. 近年的历史窗口已经被多轮研究查看过，不再具备真正未触碰 holdout 的资格。历史结果只能用于诊断和决定是否进入 Shadow，不能直接证明未来可盈利。
6. 当前正式纸面交易策略、持仓、NAV、订单和成交保持不变；本计划不以修改正式策略来制造“模型已可用”的表象。

| 范围 | 透明基线净超额 | Ridge 残差净超额 | ML 增量 | 当前结论 |
| --- | ---: | ---: | ---: | --- |
| A 股 HS300 | -5.72% | -5.98% | -0.26 个百分点 | 基线和 ML 均不成立 |
| A 股 ZZ500 | -12.23% | -12.87% | -0.65 个百分点 | 基线和 ML 均不成立 |
| QDII 港股暴露 | +15.69% | +15.63% | -0.06 个百分点 | 透明基线待按正确执行语义复验 |
| QDII 美股暴露 | -20.32% | -23.90% | -3.58 个百分点 | 基线和 ML 均不成立 |

## 2. 本次不再承诺什么

- 不承诺一定找到盈利策略。
- 不承诺机器学习一定优于规则策略。
- 不用 RankIC、训练分数或某一个漂亮区间代替可执行净收益。
- 不在看到结果后继续移动窗口、调参数、换门槛。
- 不把 Dashboard 完工、测试通过、任务部署成功描述成投资效果改善。

本次唯一承诺是：在固定数据、固定候选、固定试验预算和固定验收标准下，把四个账户范围全部跑完，并给每个范围一个不可含糊的终态。

## 3. 最终只允许四种结论

| 状态 | 含义 | 后续动作 |
| --- | --- | --- |
| `shadow_ready` | 历史诊断、稳健性、成本压力测试全部通过 | 进入独立 Shadow，不影响正式订单 |
| `baseline_only` | 透明规则策略可用，机器学习没有增量 | Shadow 使用规则版本，停止为“必须 ML”而优化 |
| `falsified` | 固定预算内所有预声明假设均失败 | 停止该范围研究，不追加参数试验 |
| `insufficient_data` | 点时数据、流动性或样本长度不足 | 明确列出缺口，只补数据，不调模型 |

不再保留“快通过”“接近可用”“再优化一点”的中间宣传状态。

## 4. 核心设计纠偏

### 4.1 策略可用性和 ML 增量分两道门

第一道门回答透明策略本身是否值得交易；只有第一道门通过，才允许训练机器学习残差。机器学习不是产品目标，而是可选的增量层。

```text
点时数据 -> 透明策略 -> 可执行净收益与稳健性
                         |
                         +-- 不通过：停止
                         |
                         +-- 通过：最多测试 2 个 ML 残差
                                      |
                                      +-- 无增量：保留透明策略
                                      +-- 有增量：ML 版本进入 Shadow
```

### 4.2 把“主动现金”与“执行失败”拆开

删除把 `capital_utilization >= 85%` 作为所有策略通用硬门槛的做法，改为同时报告：

- `strategic_risky_exposure`：策略主动要求投入多少风险资产；低于 100% 可以是择时设计。
- `target_fill_ratio`：实际成交风险资产 / 策略目标风险资产；用于发现整手、停牌、涨跌停、流动性或执行问题。
- `cash_drag`：主动现金相对基准带来的收益影响。

只有 `target_fill_ratio` 不足才属于执行质量失败；策略按规则持有现金不再被误判为系统故障。

### 4.3 换手率不再单独一票否决

`annual_turnover <= 8x` 不再作为跨市场统一硬门槛。换手改为三层证据：

1. 回放必须使用真实佣金、印花税、滑点和冲击模型后的净收益。
2. 在基准成本的 `2x` 压力情景下，策略净超额仍不得转负。
3. 流动性缺失、冲击封顶名义金额和目标成交率必须通过执行质量门。

换手仍展示并进入归因，但不会在已经扣除成本后再用一个缺乏市场区分度的固定数字重复否决。

### 4.4 历史研究与未来证明分开

- 历史 nested walk-forward：只决定是否有资格进入 Shadow。
- 未来 Shadow：才是新的未见数据，决定是否进入纸面正式策略。
- 已查看过的 2025-2026 窗口不会被重新命名为“样本外”。

## 5. 固定候选与试验预算

### 5.1 透明策略候选

每个账户范围最多六个透明候选，共 `4 x 6 = 24` 个。A 股两个范围使用同一套声明；QDII 两个范围也使用同一套声明，禁止按单个范围事后定制参数。

#### A 股：六个候选

| ID | 家族 | 排名/仓位规则 | 调仓 |
| --- | --- | --- | --- |
| `A_MOM_01` | 中期动量 | `0.6 * momentum_60 + 0.4 * momentum_120` | 每 20 个交易日 |
| `A_MOM_02` | 多周期动量 | `0.2 * momentum_20 + 0.4 * momentum_60 + 0.4 * momentum_120` | 每 20 个交易日 |
| `A_QMLV_01` | 质量-动量-低波 | 动量 45%、盈利质量 35%、低波 20% | 每 20 个交易日 |
| `A_QMLV_02` | 质量-价值-动量 | 动量 35%、盈利质量 30%、价值 20%、低波 15% | 每 20 个交易日 |
| `A_REGIME_01` | 动量+市场状态 | `A_MOM_01`；基准低于 SMA200 时风险仓位降至 50% | 每 20 个交易日 |
| `A_REGIME_02` | 质量动量+市场状态 | `A_QMLV_01`；基准低于 SMA200 时风险仓位降至 50% | 每 20 个交易日 |

盈利质量仅使用已有点时字段：`roe`、`roic`、`gross_profit_to_assets`、`free_cashflow_to_assets`、`accrual_ratio`。缺字段时按横截面可用因子重新归一，不得向未来填充。

所有 A 股分数均在“同一交易日、同一账户候选池”内转为 0-1 percentile：盈利质量取 ROE、ROIC、毛利/资产、自由现金流/资产和负应计比率的可用均值；价值取正 PE TTM 与正 PB 的反向 percentile 均值；低波使用现有 `account_low_volatility_percentile`。单个合成项至少需要一半子字段可用，否则该股票当日不使用该合成项。

#### 跨境 ETF：六个候选

| ID | 家族 | 排名/仓位规则 | 调仓 |
| --- | --- | --- | --- |
| `Q_TREND_01` | 绝对趋势 | 60/120/200 日趋势多数投票 | 每周 |
| `Q_TREND_02` | 慢趋势 | 120/200 日趋势多数投票 | 每周 |
| `Q_DUAL_01` | 双动量 | 60/120 日相对动量排名，SMA200 绝对趋势过滤 | 每周 |
| `Q_DUAL_02` | 双动量低波 | 双动量 80%、低波 20%，SMA200 过滤 | 每周 |
| `Q_TRACK_01` | 趋势+产品质量 | 趋势 70%、折溢价/跟踪误差 20%、流动性 10% | 每周 |
| `Q_TRACK_02` | 慢趋势+产品质量 | 慢趋势 70%、折溢价/跟踪误差 20%、流动性 10% | 每周 |

`Q_TREND_01` 的三个趋势信号中，至少两个为正时目标风险仓位 100%，一个为正时 50%，全部为负时 0%；`Q_TREND_02` 对两个信号使用相同的 100%/50%/0% 规则。相对排名使用各 lookback 的标准化均值。折溢价、跟踪误差和低流动性统一按“越低越好”转换，不直接比较原始量纲。

### 5.2 机器学习候选

只有通过透明策略门的每个范围，才允许对该范围的最佳家族测试：

1. `Ridge residual`：固定 5% 残差倾斜。
2. `HistGradientBoosting residual`：固定浅树、强正则、固定 5% 残差倾斜。

最多 `4 x 2 = 8` 个 ML 试验。若透明策略没有通过，该范围 ML 预算为零。不得新增算法、自动超参搜索或事后调残差权重。

参数同时冻结：A 股 Ridge `alpha=25`，QDII Ridge `alpha=35`；HGBR 使用 `learning_rate=0.03`、`max_iter=100`、`max_leaf_nodes=7`、`min_samples_leaf=100`、`l2_regularization=5`、`random_state=20260814`。这些值只允许因实现不支持而整批撤销，不能在看到收益后调整。

### 5.3 数据范围

首个 Campaign 只使用已经具备点时审计的行情、成交量、复权、指数成分、财务质量、ETF NAV、折溢价、跟踪误差、流动性和基准数据。公告语义、新闻舆情、LLM 事件、深度学习和仍有覆盖争议的资金流不进入本次候选，也不阻塞本次结案；它们以后只能作为“已通过基线之上的单独增量消融”，不能和基线发现混在一起。

### 5.4 全局预算与幂等性

- 最大透明试验：24。
- 最大 ML 试验：8。
- 最大总试验：32。
- 相同 `spec_hash + data_fingerprint + simulator_version` 的重跑只读缓存，不增加试验次数。
- 只有程序异常、文件损坏或确定性校验失败可以重跑；研究结果不好不能成为重跑理由。
- 超出预算直接返回 `campaign_budget_exhausted`，不接受 `--force` 绕过。
- 旧 Trial Registry 中在同一账户范围、同一已消费历史窗口上运行过的可比试验也进入 DSR 的多重试验计数，不能通过更换 Campaign ID 抹掉过去的试错。

## 6. 冻结的验收门槛

### Gate 0：数据与回放可信

全部满足才可计算收益：

- 特征、财务、指数成分、ETF 成分/NAV/折溢价全部 point-in-time。
- 标签版本为 `next-open-v2`，信号在 T 日收盘后产生，最早 T+1 开盘成交。
- 标的编码按字符串读取，无前导零丢失。
- 三个 purged walk-forward 折均有交易，训练与测试间隔不少于最大预测 horizon。
- 基准、交易日历、复权、手续费、滑点和整手约束与纸面交易一致。
- 收益归因误差不高于 `1e-10`。

失败时终态为 `insufficient_data`，不得用降级数据继续比较模型。

### Gate 1：透明策略具有经济价值

必须全部满足：

- 汇总净收益 `> 0`。
- 汇总净超额收益 `> 0`。
- 至少 2/3 walk-forward 折的净超额 `> 0`。
- 最大回撤 `<= 25%`，且不得比对应基准恶化超过 2 个百分点。
- `2x` 交易成本压力下净超额仍 `>= 0`。
- `target_fill_ratio >= 95%`。
- 流动性缺失名义金额占比 `<= 10%`，冲击封顶名义金额占比 `<= 10%`。
- 同一家族两个预声明变体的汇总净超额都必须为正，避免只挑中一个孤立参数点。

### Gate 2：统计稳健性

必须全部满足：

- Deflated Sharpe Ratio 概率 `>= 0.95`；按账户范围计入本 Campaign 六个候选以及旧 Registry 中同窗口的所有可比试验，而不是只计算赢家。
- Probability of Backtest Overfitting `<= 0.50`；只在同一账户范围、同一日期对齐的收益矩阵内计算，不能混合不同市场/范围。
- 日级主动收益使用 10,000 次 stationary block bootstrap，随机种子固定为 `20260814`，平均 block 长度固定为对应预测 horizon 个交易日；要求 `P(net_excess > 0) >= 0.95`。
- 不允许任何一个单一年度或单只标的贡献超过总净超额的 50%。
- 牛市定义为基准高于 SMA200 且 60 日收益大于 5%；下行定义为基准低于 SMA200 且 60 日收益小于 -5%；其余为震荡。三种状态均单独报告，允许某一状态为负，但不得触发 25% 回撤底线。

Gate 1 或 Gate 2 失败即 `falsified`。不得为了通过而降低门槛。

### Gate 3：机器学习必须提供增量

ML 与其透明基线使用相同日期、相同成本、相同组合约束，并必须全部满足：

- 汇总净超额增量 `> 0`。
- 至少 2/3 折增量 `> 0`。
- 配对 block bootstrap 中 `P(ML - baseline > 0) >= 0.95`。
- 最大回撤相对基线恶化不超过 2 个百分点。
- 年换手相对基线增加不超过 25%。
- `2x` 成本压力下仍优于透明基线。
- 主要特征方向/重要性在至少 2/3 折一致。

ML 失败不会否决已通过的透明策略，终态为 `baseline_only`。

### Gate 4：Shadow 与自动决策

历史 Gate 1-3 通过后才创建版本化 Shadow：

- Shadow 与正式账户同日、同价、同费用规则运行，但不产生正式 pending orders。
- 第 12 个可用周自动评估；证据不足只允许延长至第 16 周。
- 至少需要：周度策略 8 次计划调仓；月度策略 3 次计划调仓。
- 未来净超额为正、无回放差异、无风险底线违规，才可进入 `active_capped`，模型对正式纸面策略的最大影响先固定为 10%。
- 第 16 周仍未通过则自动拒绝，不继续无限等待。

## 7. 实施任务

### Task 1：冻结 Campaign 与清理活动主线

**文件**

- 修改：`stock_analyze/research/classical_specs.py`
- 修改：`stock_analyze/research/trial_ledger.py`
- 修改：`stock_analyze/cli.py`
- 新增：`scripts/run-local-strategy-campaign.sh`
- 新增：`stock_analyze/research/strategy_campaign.py`
- 测试：`tests/test_research_classical_specs.py`
- 测试：`tests/test_research_trial_ledger.py`
- 测试：`tests/test_cli_research.py`
- 新增测试：`tests/test_research_strategy_campaign.py`

**步骤**

1. 先写失败测试，证明 Campaign 只接受上面的 24 个透明 spec 和最多 8 个 ML spec。
2. 将旧 Ridge/ElasticNet/HGBR/Blend tournament 标为 `legacy_read_only`，保留历史读取，不再进入默认运行。
3. 为 manifest 固定 `campaign_id`、数据指纹、代码 commit、模拟器版本、候选哈希、门槛和试验计数。
4. 拒绝结果产生后的 manifest 修改，拒绝用 `--force` 增加预算。

### Task 2：修正执行语义与成本压力测试

**文件**

- 修改：`stock_analyze/research/portfolio_replay.py`
- 修改：`stock_analyze/research/activation.py`
- 新增：`stock_analyze/research/strategy_viability.py`
- 测试：`tests/test_research_portfolio_replay.py`
- 测试：`tests/test_research_activation.py`
- 新增测试：`tests/test_research_strategy_viability.py`

**步骤**

1. 先写测试区分 `strategic_risky_exposure`、`target_fill_ratio` 和 `cash_drag`。
2. 移除通用 `capital_utilization` 一票否决；保留旧字段只用于兼容展示。
3. 将绝对年换手硬门槛替换为真实成本和 `2x` 成本压力回放。
4. 添加执行质量门与逐项失败原因。
5. 证明旧策略回放结果在新字段之外不发生无意漂移。

### Task 3：实现六类透明策略

**文件**

- 修改：`stock_analyze/research/classical_specs.py`
- 修改：`stock_analyze/research/models.py`
- 修改：`stock_analyze/research/account_features.py`
- 修改：`stock_analyze/research/portfolio_replay.py`
- 测试：`tests/test_research_classical_specs.py`
- 测试：`tests/test_research_models.py`
- 测试：`tests/test_research_account_features.py`

**步骤**

1. 先写每个 spec 的精确权重、lookback、调仓周期和状态过滤测试。
2. 补充点时 `momentum_120`、SMA200 与市场状态，不引入外部新数据依赖。
3. 复用现有盈利质量、低波、ETF NAV/折溢价/跟踪误差字段。
4. 对缺失字段做横截面可用因子归一，禁止未来填充和全局均值泄漏。
5. 为每个信号生成可解释的日级因子贡献。

### Task 4：实现统计稳健性与归因

**文件**

- 修改：`stock_analyze/research/governance.py`
- 修改：`stock_analyze/research/classical_tournament.py`
- 修改：`stock_analyze/research/attribution.py`
- 新增：`stock_analyze/research/robustness.py`
- 测试：`tests/test_research_governance.py`
- 测试：`tests/test_research_classical_tournament.py`
- 测试：`tests/test_research_attribution.py`
- 新增测试：`tests/test_research_robustness.py`

**步骤**

1. 复用现有 DSR/PBO，按同一账户范围纳入 Campaign 和旧 Registry 中同窗口的全部可比试验。
2. 添加日级主动收益的 stationary block bootstrap 和配对增量 bootstrap。
3. 固定牛/震荡/下行状态的定义，不允许按策略结果重新切分。
4. 输出选股、择时、Beta、主动现金、费用、未成交六类归因并严格对账。
5. 输出年度、标的和状态集中度，阻止单一行情阶段伪装成普遍优势。

### Task 5：一次性运行全部透明候选

**命令**

```bash
python3 -m unittest \
  tests.test_research_classical_specs \
  tests.test_research_trial_ledger \
  tests.test_research_strategy_campaign \
  tests.test_research_portfolio_replay \
  tests.test_research_strategy_viability \
  tests.test_research_governance \
  tests.test_research_robustness \
  tests.test_research_attribution

python3 -m stock_analyze --as-of 2026-08-14 \
  run-strategy-campaign \
  --campaign strategy-recovery-20260814-v1 \
  --input-bundle .artifacts/local-model-training/a_share-20260814-080903-62501/input/manifest.json \
  --input-bundle .artifacts/local-model-training/cn_qdii_etf-20260814-083057-66211/input/manifest.json \
  --stage transparent \
  --offline
```

**输出**

- `data/research/campaigns/strategy-recovery-20260814-v1/manifest.json`
- `data/research/campaigns/strategy-recovery-20260814-v1/trials.jsonl`
- `data/research/campaigns/strategy-recovery-20260814-v1/transparent-results.parquet`
- `reports/research/strategy-recovery-20260814-v1-transparent.json`
- `reports/research/strategy-recovery-20260814-v1-transparent.md`

所有四个范围完成后才读取排名；中途不根据结果调整候选。

计算在本机执行；ECS 只负责提供带 SHA-256 的不可变输入包。开始运行前先将两个 manifest 及其全部文件复制到 Campaign 的只读 input 目录并复核哈希，运行期间不再从 ECS 增量读取数据。

### Task 6：只为幸存基线运行 ML 增量

**文件**

- 修改：`stock_analyze/research/cross_sectional_candidate.py`
- 修改：`stock_analyze/research/pipeline.py`
- 测试：`tests/test_research_cross_sectional_candidate.py`
- 测试：`tests/test_research_pipeline.py`

**步骤**

1. 先写测试，证明负收益基线不能启动 ML，ML 失败不会否决正收益基线。
2. 固定 Ridge/HGBR、5% 残差倾斜和特征集；删除默认路径中的自动超参选择。
3. 在同一 Campaign manifest 下运行最多八个 ML spec。
4. 用配对增量门决定 `shadow_ready` 或 `baseline_only`。

**命令**

```bash
python3 -m stock_analyze --as-of 2026-08-14 \
  run-strategy-campaign \
  --campaign strategy-recovery-20260814-v1 \
  --stage incremental-ml \
  --offline
```

### Task 7：生成一次性最终决策包

**文件**

- 新增：`stock_analyze/research/campaign_report.py`
- 新增测试：`tests/test_research_campaign_report.py`

**最终产物**

- `reports/research/strategy-recovery-20260814-v1-final.json`
- `reports/research/strategy-recovery-20260814-v1-final.md`
- 每个范围一张 decision card，包含：
  - 最终状态；
  - 赚钱/亏钱来源；
  - 基准收益、净收益、净超额、Sharpe、最大回撤；
  - 选股/择时/Beta/现金/费用/未成交归因；
  - 三个时间折和三个市场状态；
  - DSR、PBO、bootstrap 概率；
  - 失败门与停止原因；
  - 透明基线与 ML 的增量对比。

报告必须解释失败，不得只罗列指标。四个范围必须全部有终态，才允许 Campaign 标记 `complete`。

### Task 8：Shadow、Dashboard 与发布

**文件**

- 修改：`stock_analyze/research/activation.py`
- 修改：`stock_analyze/research/tabular_forward.py`
- 修改：`stock_analyze/dashboard_workspace_api.py`
- 修改：`frontend/dashboard/src/ModelResearchPage.tsx`
- 修改：`frontend/dashboard/src/types.ts`
- 测试：相应 registry、forward、API 和 frontend 测试

**步骤**

1. 只为 `shadow_ready` / `baseline_only` 生成不可变版本；失败范围不写 Registry。
2. Shadow 每日自动前推，12/16 周自动评估，不要求用户手动提醒。
3. Dashboard 只显示四层事实：透明策略、ML 增量、Shadow 进度、正式采用状态。
4. 失败显示精确失败门；不再显示模糊的“训练中/待晋升”。
5. 前端工作只在最终决策包生成后开始，避免再次用大量时间美化尚未成立的研究。
6. 发布前验证正式纸面交易文件哈希不变。
7. ECS 发布仅发生一次：导入最终报告和通过者的 Shadow bundle，然后运行 API、定时任务与纸面交易隔离验证。

## 8. 验证矩阵

### 8.1 代码验证

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q stock_analyze
bash -n scripts/run-local-strategy-campaign.sh
cd frontend/dashboard && npm test -- --run && npm run build
```

### 8.2 研究验证

- manifest 在首个结果产生后不可变。
- 24 个透明候选全部完成或给出数据不足原因。
- ML 数量不得超过实际幸存范围数乘以 2。
- 每个收益数字能由日级 NAV 与归因重算。
- base cost 与 `2x` cost 报告同时存在。
- DSR 按范围计入全部可比历史试验，PBO 使用同范围、同日期对齐的完整候选矩阵。
- 最终报告四个范围无空白状态。

### 8.3 安全验证

- `formal_strategy_activated` 在 Shadow 通过前保持 `false`。
- 正式 `daily_nav.csv`、`positions.csv`、`pending_orders.json`、`trades.csv` 的部署前后哈希一致。
- 无真实券商接口、无真实订单。
- ECS 仅接收代码、报告和通过的 Shadow bundle，不接收本地临时缓存。

### 8.4 Dashboard 验证

- API 契约测试通过。
- 五个核心接口返回 200 且字段非空。
- Desktop 与 390px mobile 截图无白屏、无横向溢出、无陈旧状态。
- 失败状态、规则基线、ML 增量和 Shadow 不能混为同一概念。

## 9. 用户沟通契约

执行本计划时只产生两次面向用户的结论性更新：

1. 启动确认：给出冻结的 Campaign manifest 哈希、数据指纹和总试验预算。
2. 最终交付：给出四个范围的终态、证据、部署结果与需要等待的 Shadow 日期。

只有真正阻塞（数据文件损坏、权限缺失、硬件故障）才中途打断。不会再用“指标略有提升”“下一轮可能通过”要求用户继续授权或继续投入。

## 10. 完成定义

本计划只有同时满足以下条件才算完成：

1. 现有错误门槛语义已经修正并有回归测试。
2. 24 个透明候选在固定预算内全部结案。
3. 只有幸存范围运行 ML，且总 ML 试验不超过 8。
4. 四个范围分别得到 `shadow_ready`、`baseline_only`、`falsified` 或 `insufficient_data`。
5. 最终报告能解释收益来自哪里、亏损发生在哪里、ML 是否真正增加收益。
6. 只有通过者进入 Shadow，正式策略不被提前修改。
7. Dashboard、API、定时任务、纸面交易隔离均完成生产验证。

即使最终四个范围全部 `falsified`，只要上述证据闭环完成，本 Campaign 仍然是完整结果：它说明现有数据和预声明策略没有可证明优势，并阻止系统继续无限消耗在同一批历史数据上。

## 11. 回滚

- 代码回滚到 `6975d5c611b8c7c4ba8e881363cd8c3acba8ffc0`。
- Campaign 报告保留为审计证据，不删除失败记录或篡改试验计数。
- Shadow bundle 可停用，但不删除历史版本。
- 正式账户状态、持仓、NAV、订单与成交不做重置。

## 12. 方法依据

- 动量与趋势只作为有限、预声明的经济假设，不被当作必然盈利规律。
- 多重试验会显著抬高回测表现，因此使用固定试验预算、PBO 和 DSR。
- 时间序列动量既有支持证据，也有质疑其可预测性的研究；因此采用跨折、成本压力和未来 Shadow，而不是引用论文直接宣告策略成立。
- 盈利质量与价值组合只在 A 股点时财务覆盖足够时使用，任何因子都必须通过组合净收益验证。

主要一手依据：

- Bailey et al., [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253)
- Bailey and Lopez de Prado, [The Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- Jegadeesh and Titman, [Returns to Buying Winners and Selling Losers](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1993.tb04702.x)
- Moskowitz, Ooi and Pedersen, [Time Series Momentum](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf)
- Huang et al., [Time series momentum: Is it there?](https://doi.org/10.1016/j.jfineco.2019.08.004)
- Novy-Marx, [The other side of value: The gross profitability premium](https://doi.org/10.1016/j.jfineco.2013.01.003)
