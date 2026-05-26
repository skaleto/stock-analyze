## Context

P0 修正完成后，前向模拟系统已经具备：A 股交易日历、保守模拟成交、未成交订单留痕、T+1 近似、`max_single_weight` 约束、NAV upsert、模拟成交阻塞（停牌/涨停买/跌停卖）。

但策略本身仍停留在“横截面百分位 + 等权 TopN”的粗糙打分上。这与主流文献和工业实现（Fama-French 三/五因子、Carhart 动量、S&P Quality/Value/Momentum、MSCI/Barra 风险模型、Qlib/Alphalens 研究流程）之间的差距，已经超出“多加几个因子”的范畴，是缺少**横截面处理 / 风险控制 / 绩效诊断 / 可复现实验**这四件事情。

本 change 不试图一步到位变成 Qlib。它的目标是把当前项目从“能跑”推进到“跑得明白”：

1. 让因子打分按主流流水线生成，而不是裸排名。
2. 把市值这种风格因子从 alpha 移开，避免隐含押注。
3. 把绩效指标补到一个新手能据此判断“策略 vs 市场 vs 成本”的程度。
4. 输出能让人事后回放和归因的诊断与账本。

这是 P1 的方法学基线，是后续 P2 历史回测、P3 因子研究、P4 组合优化的入口。

## Goals / Non-Goals

**Goals:**

- 把当前策略对齐到“主流多因子初阶最佳实践”的程度，足以让新手据此判断收益来源。
- 让每一次跑出来的信号、订单、净值都能被反向追溯到当时的配置与代码版本。
- 在不引入回测引擎的前提下，先把因子诊断和绩效归因的“产出口径”定下来，给后续回测铺基础。
- 保持现在的 CSV/JSON 文件态架构，避免一次到位引入数据库迁移成本。
- 默认配置升级后跑出来的结果应在量级上与当前 v1 相近，避免突变让历史 NAV 不可比；明显改动通过 preset 显式开启。

**Non-Goals:**

- 不实现历史回测引擎（多年滚动样本外、3-5 年回测、参数敏感性）。
- 不实现 point-in-time 财务公告日（避免动财务接口口径）。
- 不引入历史指数成分数据库。
- 不引入组合优化器（CVXPY/PyPortfolioOpt），等约束条件先固定再考虑。
- 不引入告警/通知/SLA；监控仍依赖人眼看 dashboard。
- 不接券商。
- 不改变现有 CLI 命令名、参数名、产物文件名（向后兼容）。

## Decisions

### 1. 因子标准化采用 winsorize → z-score → 行业中性化的顺序

主流文献和 Alphalens 默认做法都是先抑制极端值再标准化。我们选择：

- Winsorize 在配置的两端百分位（默认 1% / 99%）夹边，而不是用 z-score ± k σ 截断。原因是 A 股个别股票的 PE 可能极端到 1000+，σ 截断在小样本下表现不稳定。
- Z-score 在截面内对每个因子单独做，公式 `(x - mean) / std`。
- 行业中性化在 z-score 之后，做行业内 demean：每个因子值减去其所在行业的均值。行业字段优先用 `basic_info().industry`；缺失则归入 `未分类` 桶。这是一阶近似，更严格的做法是回归剥离行业 dummy + 市值，但对“入门版本”足够。

不选“OLS 残差”是因为：(a) 实现复杂度更高；(b) 残差解释成本对新手不友好；(c) 后续 P2 引入回测时再升级。

### 2. 缺失因子按可用因子归一权重

当前实现 `fillna(0)` 把缺失值当 0 加分，等价于该因子的最低分。这对部分小盘股或新上市股票不公平，也让 `score` 的含义混乱。

新做法：

- 对每只股票，统计该股票实际有效因子的权重之和 `w_valid`。
- 综合分 = Σ(valid_factor_zscore × signed_weight) / max(w_valid, eps)。
- 当 `w_valid < min_factor_coverage * total_weight`（默认 0.6）时，剔除该股票并写 `data_warnings += "insufficient_factor_coverage"`。

不选“多重插补”是因为：(a) 财务/估值数据缺失通常不是随机缺失（MNAR），均值/中位数插补会有偏；(b) 跳过低覆盖股票更直接也更安全。

### 3. 市值从 alpha 降级为风险/流动性过滤

当前 `market_cap_yi` 在 `factors` 中且 `direction=high`，相当于偷偷下了一个“偏大盘”的风格押注。在 A 股，价值/质量因子和大盘风格本身高度相关，这会让组合的超额收益解释力变模糊。

主流做法（S&P Quality/Value/Momentum 多因子指数方法论）通常把市值用作：

- **流动性筛选**（最小市值阈值，避免微小盘流动性问题）。
- **规模上限**（防止集中在大盘）。
- **加权时的因子**（cap-weighted 内部 vs equal-weight）。

我们的选择：

- 从默认 `factors` 中移除 `market_cap_yi`。
- 新增 `filters.min_market_cap_yi`（默认 30 亿）和 `filters.max_market_cap_yi`（默认不限）。
- 加载器对老配置兼容：如果 v1 配置仍在 `factors` 中含 `market_cap_yi`，自动折叠到 `filters.min_market_cap_yi=30` 并打 warning。

### 4. 持仓缓冲（hold buffer）减少换手

行业研究和指数方法论（S&P、MSCI、Russell）普遍采用“buffer zone”来抑制成分股频繁进出。我们的实现：

- 配置 `portfolio_controls.hold_buffer_pct`（默认 0.5）。
- 选股逻辑：先按 composite score 排序，前 `top_n` 直接入选；当前已持有但排名落到 `[top_n, top_n × (1 + hold_buffer_pct)]` 区间内的，保留不卖。
- 已持有但排名跌出 `top_n × (1 + hold_buffer_pct)` 才卖出。
- 副作用：实际持仓数可能在 `top_n` ~ `top_n × (1 + hold_buffer_pct)` 之间浮动。后续可用 `max_single_weight` 与现金缓冲控制。

不选“固定阈值变化才换”（如 score 变化 > 0.1 才动），因为分数本身没有稳定语义；排名 buffer 更可解释。

### 5. 单行业权重上限

当前 TopN 等权可能在一个行业里塞 4-5 只。例如 hs300 选 10 只里 5 只是银行，那本质就是变相做行业 beta。

做法：

- 配置 `portfolio_controls.max_industry_weight`（默认 0.30）。
- 选股流程：按 composite score 降序遍历候选；如果加入该股票会使所属行业权重超过上限，则跳过该股票，从下一名补位；如果遍历完仍不够 `top_n`，则放宽（防止补不满）并记录 warning `industry_cap_relaxed`。
- 行业字段缺失（未分类）的股票视为独立分组，不和已分类股票共享上限（避免一锅端把所有“未分类”当一个行业）。

### 6. 低波 / 股息率作为可选防御因子

主流 A 股新手友好的因子集合通常包含：价值、质量、动量、低波、股息。我们补齐后两个：

- **`low_volatility_60`**：过去 60 个交易日收益率标准差，`direction=low`。基于 `price_history` 现成数据即可计算。
- **`dividend_yield`**：TTM 股息率，`direction=high`。来源：AkShare `stock_individual_info_em` / `stock_a_indicator_lg`；缺失则从 Baostock 派生（每股股息 / 当前价）。

默认 `strategy_v1.yaml` 不开启（权重 0），由 `configs/preset_quality_low_vol.yaml` 在 preset 中演示开启方式。

### 7. 绩效指标口径选择

为避免新手在 Sharpe、信息比率定义上踩坑，统一口径：

- **年化口径**：A 股 252 个交易日。
- **波动率**：日收益率的样本标准差 × √252。
- **Sharpe**：(年化收益 − 年化无风险) / 年化波动。`risk_free_rate` 默认 0.02。
- **Sortino**：分母换成下行半标准差（仅 daily_return < 0 的样本）。
- **跟踪误差**：每日 (account_return − benchmark_return) 的样本标准差 × √252。
- **信息比率**：年化超额收益 / 跟踪误差。
- **换手率**：单周 buy 与 sell 名义额之和 / 期初组合市值（双边口径）。
- **成本占比 (bps)**：累计佣金+印花税+滑点 / 累计成交金额 × 10000。
- **Win rate**：完整 round-trip（买入并最终全部卖出）中收益为正的比例；持仓未平仓不计入。

口径在 `docs/forward-simulation-runbook.md` 与 dashboard tooltip 中显式声明，避免误解。

### 8. 因子诊断的 forward IC 冷启动策略

`forward_ic.csv` 计算需要至少 1 个完整周期（5 个交易日）后的实际收益。冷启动期间：

- 当 NAV 历史不足或未达 5 个交易日时，写一行 `signal_date, factor, ic=NaN, ic_status="insufficient_history"`。
- 一旦满足历史长度，自动回填该 signal_date 的 IC，避免历史记录长期空白。
- IC 计算用 Spearman rank correlation，避免极端值主导。

### 9. 运行账本：CSV 而非 SQLite

主流做法是数据库，但本项目当前还在 CSV/JSON 阶段，引入 SQLite 会带来迁移、schema 版本、备份的复杂度。本次选择：

- 用 `data/runs.csv` 作为账本；`run_id = f"{command}-{strftime('%Y%m%dT%H%M%S')}-{rand4}"`。
- 命令入口立刻 append 一行 `status=running`；try/finally 中根据成败更新到 `success` 或 `failed`。
- 由于 CSV 不便随机更新，我们采用 append + 末尾 status 行覆盖前一行的策略：每次写一行新状态，dashboard 读时按 `run_id` group + 取最新一行。后续 P2 升级到 SQLite 时一次性 migrate。
- 配置快照：每次启动计算 `config_hash = sha256(json.dumps(config, sort_keys=True))[:12]`；若 `data/configs/<hash>.json` 不存在则写入。

### 10. Dashboard 三段式扩展，不重新设计

保持当前单 HTML 文件结构，按顺序追加：

- **绩效解释面板**：在“净值曲线”下加一个 4×3 网格，列出本变更新增的指标，每个指标带 tooltip 说明计算口径。
- **因子诊断面板**：在“因子贡献均值”旁加“因子覆盖率”和“最近 12 周前向 IC”图。
- **运行账本面板**：在最底部加一个最近 10 次运行的表格，列 run_id、命令、状态、耗时、config_hash。

不重新设计成多页应用，避免 dashboard 大改阻塞主线。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 行业字段在公开数据源缺失率较高 | 行业中性化和行业上限可能退化 | 中性化对“未分类”桶单独 demean；行业上限把“未分类”视为独立桶，至少不放大错误；在 dashboard 标记行业覆盖率 |
| Buffer zone + 行业上限可能让组合长期偏离 TopN 设想 | 模拟收益与“TopN 等权基准”不可比 | 在绩效摘要中输出实际持仓数与行业分布；在文档中明确这是“TopN + 缓冲”而非纯 TopN |
| 因子标准化口径变化导致历史 NAV 不可比 | 升级后第一周可能换手率突增 | 把 `factor_processing.enabled` 设为可选开关；默认 v1 配置升级后首次运行写 warning，提示这是基线切换；保留前一份 config 快照便于回放对照 |
| `dividend_yield` 在 AkShare/Baostock 不一定可靠 | 新因子可能噪音大 | 默认不启用；在 preset 与 docs 标记数据源依赖 |
| `run_id` 写入失败影响主流程 | CLI 命令体验回归 | 账本写失败仅 record health，不中断主命令；账本是观察工具不是临界路径 |
| 行业上限导致候选不足 | TopN 凑不齐 | 放宽规则并写 warning `industry_cap_relaxed`；不静默偏离上限 |
| 持仓缓冲让某只股票长期占位 | 换手过低反而成为缺陷 | 强制最长持有周期上限（默认 60 个交易日）；过期强制重新评估 |

## Migration Plan

1. **配置兼容层**：在 `config.load_config()` 之后增加一步 `migrate_strategy_config()`：
   - 若 `factors.market_cap_yi` 存在 → 折叠到 `filters.min_market_cap_yi=30`（亿）并 warning。
   - 若 `factor_processing` 不存在 → 注入默认值 `{enabled: true, winsorize_lower: 0.01, winsorize_upper: 0.99, neutralize_industry: true, min_factor_coverage: 0.6}`。
   - 若 `portfolio_controls` 不存在 → 注入默认值 `{max_industry_weight: 0.30, hold_buffer_pct: 0.5, max_holding_days: 60}`。
2. **数据兼容层**：旧 `data/state.json`、`pending_orders.json`、`daily_nav.csv` 不需要迁移；新增列附加。`positions.csv` 增加 `industry`、`hold_since` 列，旧读者忽略未识别列。
3. **首次运行**：升级后第一次 `run-weekly` 写一行 `runs.csv` 注明 `migration=v1_to_v2`，并把当时的 v2 配置 hash 写入 `data/configs/`。
4. **回滚**：若实际跑出来的行为有问题，可以把 `factor_processing.enabled` 与 `portfolio_controls.*` 关闭，行为退回原来的横截面排名 + 等权 TopN；旧文件态保持兼容。
5. **文档同步**：完成实现后更新 `README.md`、`docs/forward-simulation-runbook.md`、`docs/quant-model-gap-review-2026-05-18.md` 的 P1 状态。

## Open Questions

- 默认配置升级后，是否需要把 `run-weekly` 在第一次跑时输出一份“前后对照”的因子贡献样例，让新手理解新流水线的影响？设计倾向是“是”，但需要确认是否在本变更范围。暂列入 tasks 的可选项。
- 行业字段是否值得在 `AkshareProvider` 里维护一张本地 `industry_map.csv` 缓存，避免每只股票都打一次 `basic_info()`？倾向是“是”，但属于性能优化，可放后续。
- `dividend_yield` 在 A 股的有效因子周期通常较长（年度），周频策略中作用可能有限。是否值得作为内置因子？保留为 preset 演示，本次不进默认。
- 历史回测引擎放下一个 change `add-historical-backtest-baseline`；本变更只把口径打齐。是否需要在本变更预留 `backtest_run_id` 字段在 `runs.csv`？暂不预留，避免过早设计。
