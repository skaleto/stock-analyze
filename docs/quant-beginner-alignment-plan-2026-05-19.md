# 新手友好型多因子方案对齐：Review 与落地计划

日期：2026-05-19  
范围：在 P0 正确性修正完成的基础上，对齐“主流多因子量化初阶最佳实践”，并形成可执行 OpenSpec change。  
结论属性：研究与工程评审记录，不构成投资建议，不代表真实交易指令。  
对应 OpenSpec change：[`openspec/changes/align-beginner-friendly-multi-factor-foundations`](../openspec/changes/align-beginner-friendly-multi-factor-foundations/proposal.md)

## 一句话结论

P0 正确性已经稳住（订单不再吃未来行情、未成交不再静默丢、NAV 去重、模拟成交阻塞、T+1、`max_single_weight`）。下一步的核心瓶颈不是“因子不够多”，而是“因子处理太粗糙、组合控制太弱、绩效解释太薄、跑出来的结果回放不动”——这些恰好是主流多因子量化（Fama-French / S&P 多因子 / MSCI Barra / Qlib / Alphalens）默认就具备的基线。本计划聚焦把这条基线补齐。

## 现状画像（after P0）

策略入口：[`configs/strategy_v1.yaml`](../configs/strategy_v1.yaml)。当前结构：

- 因子：PE、PB、ROE、毛利率、资产负债率、市值、20 日动量、60 日动量。
- 处理：每个因子做 `rank(pct=True)`，按方向反转后乘权重再相加。
- 缺失值：`fillna(0)`，等价于补到该因子最低分。
- 选股：综合分 Top10 等权，每周完全重排。
- 成本：佣金/印花税/滑点 + 100 股整数倍。
- 绩效：累计收益、最大回撤、净值点数。
- 数据源：AkShare 多源 + Baostock 降级 + 本地缓存；运行健康写 `data/data_health.json`。

P0 修正给出的正确性保障在 [`docs/forward-simulation-runbook.md`](forward-simulation-runbook.md) 和 [`docs/quant-model-gap-review-2026-05-18.md`](quant-model-gap-review-2026-05-18.md) 中已记录。

## 主要缺陷分类

### 一、因子方法学的缺陷

| 编号 | 缺陷 | 影响 | 主流做法 |
| --- | --- | --- | --- |
| F-1 | 只做百分位排名，没有 winsorize | 极端 PE/PB/动量值压扁中段分布 | 截面 1%/99% winsorize 后再标准化 |
| F-2 | 没有 z-score 标准化 | 各因子权重在排名世界里偷换了概念 | 截面 z-score 让权重含义统一 |
| F-3 | 没有行业中性化 | 行业风格会主导排名（如银行集体便宜=拿一堆银行股） | 行业内 demean 或回归剥离 |
| F-4 | `fillna(0)` 把缺失当最低分 | 数据缺失股 = 该因子最差，混淆“没数据”和“差” | 按有效因子权重重新归一；覆盖不足直接剔除 |
| F-5 | 市值是 alpha 且 direction=high | 隐藏的大盘风格押注，挤压价值/质量的可解释性 | 把市值从 alpha 移开，仅作流动性下限/规模上限/加权方式 |
| F-6 | 没有 IC/RankIC/分组收益 | 不知道因子在当前池子里是否真有效 | Alphalens 风格诊断：覆盖率、IC、前向收益 |

### 二、组合构建的缺陷

| 编号 | 缺陷 | 影响 | 主流做法 |
| --- | --- | --- | --- |
| P-1 | 没有单行业上限 | TopN 可能集中在 1-2 个行业，赌行业 beta | S&P/MSCI 多因子指数单行业上限 25–30% |
| P-2 | 每周完全重排，没有缓冲 | 换手率高、成本吃光超额 | Buffer zone：保留排名在 top_n×(1+buffer) 内的持仓 |
| P-3 | 没有最长持有期约束 | 缓冲叠加可能让烂股长期占位 | `max_holding_days` 强制重评估 |
| P-4 | 缺少防御因子（低波、股息） | 新手最常用的“慢钱”因子缺位 | 低波、股息率作为可选/preset |
| P-5 | 只有一份默认配置 | 新手无法横向对比不同风格 | 提供 1+ preset 演示价值/质量/低波切换 |

### 三、绩效与归因的缺陷

| 编号 | 缺陷 | 影响 | 主流做法 |
| --- | --- | --- | --- |
| M-1 | 只有累计收益、最大回撤 | 看不出策略 vs 市场 vs 成本 | 年化、Sharpe、Sortino、最大回撤天数 |
| M-2 | 没算超额、跟踪误差、IR | 无法说“是不是跑赢了基准” | 累计/年化超额、跟踪误差、信息比率 |
| M-3 | 没有换手率、成本占比 | 看不到成本对超额的吃掉比例 | 换手（双边）、累计成本 bps |
| M-4 | 没有 round-trip 胜率 | 新手判断不出“赢的次数多还是赔的次数多” | FIFO 配对，统计 win rate / 平均持有 / pnl 分布 |

### 四、可复现性与运维的缺陷

| 编号 | 缺陷 | 影响 | 主流做法 |
| --- | --- | --- | --- |
| R-1 | `latest_signals.csv` 直接覆盖 | 历史信号无法回放 | 按 `run_id` 持久化因子明细 |
| R-2 | 没有 run ledger | 不知道某份 dashboard 对应当时哪份配置/代码 | `runs.csv` 记录 run_id/状态/config_hash/code_version |
| R-3 | 配置版本不冻结 | 任意修改 config 之后历史结果不可回放 | 按 `config_hash` 把完整配置快照存档 |
| R-4 | 没有“失败 run”可见性 | 跑挂了只能翻日志 | 账本里把 `failed` 显式留痕 |

## 落地范围（本计划）

本计划仅推进上面 4 类缺陷中的“可在不引入回测引擎的前提下完成”的部分，对应 OpenSpec change `align-beginner-friendly-multi-factor-foundations`，拆 5 个能力域：

| 能力域 | 解决 | 不解决 |
| --- | --- | --- |
| `factor-processing-pipeline` | F-1、F-2、F-3、F-4、F-6 的口径 | F-6 的样本外检验（待回测引擎） |
| `portfolio-construction-controls` | F-5、P-1、P-2、P-3、P-4、P-5 | 组合优化器（CVXPY/PyPortfolioOpt） |
| `strategy-performance-metrics` | M-1、M-2、M-3、M-4 | 行业/风格归因（待回测引擎） |
| `factor-diagnostics-output` | F-6 的产出口径与冷启动 | 长样本 IC、衰减分析（待回测引擎） |
| `run-ledger-and-config-snapshot` | R-1、R-2、R-3、R-4 | SQLite/DuckDB run ledger（待数据库迁移 change） |

明确暂不在本计划范围（保留给后续独立 change）：

- 历史回测引擎（建议下一 change：`add-historical-backtest-baseline`）。
- Point-in-time 财务公告日（建议 change：`introduce-point-in-time-fundamentals`）。
- 历史指数成分库。
- 组合优化器、行业/风格归因模型。
- 告警/SLA、券商接口。

## 关键设计选择速览

详细论证见 OpenSpec change [`design.md`](../openspec/changes/align-beginner-friendly-multi-factor-foundations/design.md)。这里只列结论与原因：

1. **顺序：winsorize → z-score → 行业中性化**。Alphalens 默认做法。winsorize 抵抗 A 股极端 PE/PB。
2. **缺失因子重归一**而不是补 0。避免“没数据=最差”混淆。
3. **市值移出 alpha**。改为 `filters.min_market_cap_yi=30` 亿做流动性下限。
4. **持仓缓冲 hold_buffer_pct=0.5**。S&P/MSCI 指数方法论常见做法。
5. **`max_holding_days=60`**。防缓冲叠加导致长期占位。
6. **单行业上限 30%**。常见 beginner-friendly 阈值。
7. **新增低波/股息率**作为可选因子，默认不开；通过 `configs/preset_quality_low_vol.yaml` 演示。
8. **绩效指标按 252 交易日年化**，A 股口径。
9. **Forward IC 用 Spearman**，对极端值稳健。
10. **运行账本仍用 CSV**（不一步到位 SQLite），通过 `run_id` 写多行实现 status 演进。

## 实施路线与时间预估

按 OpenSpec change tasks.md 顺序：

| 阶段 | 内容 | 工作量参考 |
| --- | --- | --- |
| 1 | OpenSpec foundation & strict validate（本文档 + change 本身） | 0.5 天 |
| 2 | Factor processing pipeline + 单元测试 | 1.5 天 |
| 3 | Portfolio construction controls（行业上限、缓冲、preset） | 1.5 天 |
| 4 | Performance metrics（年化、超额、IR、换手、成本、win rate） | 1.5 天 |
| 5 | Factor diagnostics（snapshot、coverage、forward IC、面板） | 1 天 |
| 6 | Run ledger & config snapshot | 0.5 天 |
| 7 | 文档、单元测试组织、烟囱验证 | 0.5 天 |

总计 ~7 天，单人粒度。

## 风险与回退

主要风险：

- 升级流水线后第一次 `run-weekly` 可能换手率突增；通过保留旧 v1 行为开关与 config 快照可对照。
- 行业字段缺失率高时中性化退化；把 `未分类` 单独成桶并在 dashboard 标记。
- `dividend_yield` 数据源不一定可靠；默认关闭，preset 中显式打开并在 `data_health.json` 记录派生。

回退路径：

- 把 `factor_processing.enabled=false` 与 `portfolio_controls.*` 清空恢复旧行为。
- v1 配置不改一行也能在 v2 加载层下跑通，靠迁移函数自动折叠 `market_cap_yi`。
- 新增文件（`runs.csv`、`factor_runs/`、`factor_diagnostics/`、`configs/`）都是附加，不影响旧读者。

## 验收口径

OpenSpec change tasks.md 的 Quality Gate 必须全部通过：

- `python -m unittest discover -s tests` 全绿。
- `python -m py_compile stock_analyze/*.py tests/*.py` 通过。
- `openspec validate align-beginner-friendly-multi-factor-foundations --strict` 通过。
- 用旧 v1 配置 + 新代码跑一次 `run-weekly` 不出错，且产出新文件集合。
- dashboard 新增的绩效面板、因子诊断面板、运行账本面板能渲染且对缺失数据有占位。

## 不变的边界

- 仍然只是前向模拟，不接券商。
- 仍然不构成投资建议。
- 仍然依赖公开数据接口，无法保证不间断可用。
- 仍然不保证策略有正超额。

## 后续 change 建议

按优先级：

1. `add-historical-backtest-baseline`：把当前前向模拟的同一套规则跑 3-5 年历史，输出年化、超额、最大回撤、夏普、换手；提供样本外检验。需要历史指数成分。
2. `introduce-point-in-time-fundamentals`：按公告日生效财务因子，消除“未来财务函数”污染。
3. `add-research-factor-toolkit`：因子衰减、相关性、行业暴露归因、风格暴露归因。
4. `migrate-run-ledger-to-sqlite`：把 `runs.csv` + `data/configs/` 迁到 SQLite/DuckDB，加索引与原子写。
5. `add-alerting-and-sla`：任务失败、NAV 停更、pending 超期、回撤超阈值告警。
6. `introduce-portfolio-optimizer`：CVXPY/PyPortfolioOpt 在固定约束下做优化。

## 参考

- Fama-French 五因子模型：<https://academic.oup.com/rfs/article/29/1/69/1843682>
- Carhart 1997 动量：<https://ideas.repec.org/a/bla/jfinan/v52y1997i1p57-82.html>
- S&P Quality, Value & Momentum Multi-factor Indices：<https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-quality-value-momentum-multi-factor-indices.pdf>
- MSCI Barra Global Equity Model：<https://app2.msci.com/products/analytics/models/global_equity_model/>
- Alphalens 因子分析：<https://quantopian.github.io/alphalens/>
- Qlib 文档：<https://qlib.readthedocs.io/en/latest/>
- Qlib point-in-time 数据库：<https://qlib.readthedocs.io/en/latest/advanced/PIT.html>
- QuantConnect Algorithm Framework：<https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview>
- 上交所交易机制（涨跌停、100 股整数倍、T+1）：<https://english.sse.com.cn/start/trading/mechanism/>
- 印花税减半通知：<https://szs.mof.gov.cn/zhengcefabu/202308/t20230827_3904226.htm>
