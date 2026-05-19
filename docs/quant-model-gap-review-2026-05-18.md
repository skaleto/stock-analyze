# 量化模型差距 Review 与优化路线

日期：2026-05-18  
范围：`stock-analyze` 当前 A 股前向模拟系统、主流量化方法参照、工程化与 dashboard 路线  
结论属性：研究与工程评审记录，不构成投资建议，不代表真实交易指令。

## 结论摘要

当前系统已经从单文件筛选器升级为一个可运行的 A 股前向模拟服务：有沪深300和中证500两个模拟账户、周度信号、下一交易日模拟成交、成本估算、净值追踪、systemd 定时任务和静态 dashboard。

但它距离“主流量化系统”还有明显差距。现在更准确的定位是“价值 + 质量 + 动量规则选股器 + 纸面组合跟踪”，还不是完整的因子研究、历史回测、风险模型、组合优化、交易执行和生产监控系统。

最优先要补的是正确性地基：point-in-time 财务数据、真实交易日历、停牌和涨跌停不可成交、T+1 可卖约束、历史指数成分、NAV 去重、未成交订单状态保留。否则后续即使加更多因子和图表，结果也可能被未来函数、幸存者偏差或模拟成交偏差污染。

## OpenSpec 修正记录

本轮按 OpenSpec change `harden-forward-simulation-correctness` 落地了 P0 中最直接影响前向模拟可信度的部分：

- 周度调仓优先使用 A 股交易日历确定 `execute_after`，接口不可用时才降级到工作日近似。
- 模拟成交只读取运行日之前可见的行情，不再为了成交偷看未来 K 线。
- 正常执行不再用订单参考价兜底；缺少可见行情时订单保留 pending。
- 未成交或部分成交订单会记录 `status`、`attempts`、`last_attempt_at`、`unfilled_reason`，不会静默消失。
- 模拟成交会阻塞停牌、涨停买入、跌停卖出。
- 持仓增加 `available_shares` 和 `last_buy_date`，用作 T+1 可卖股数近似。
- `daily_nav.csv` 改为按 `date + account_id` upsert，避免同日重复运行污染净值曲线。
- `trading.max_single_weight` 已参与目标股数计算。
- Dashboard pending orders 增加状态、尝试次数和未成交原因。

仍未在本轮完成的关键项包括：point-in-time 财务公告日、历史指数成分、公司行动/复权审计、多年回测、因子 IC/RankIC、行业/市值中性和数据库 run ledger。

## 当前模型画像

配置入口：[configs/strategy_v1.yaml](../configs/strategy_v1.yaml)

当前策略是双账户、周度调仓的 A 股多因子模拟策略：

- 账户：`hs300` 对标沪深300，`zz500` 对标中证500。
- 初始资金：每个账户 50 万，总模拟资金 100 万。
- 调仓：每周生成信号，下一交易日模拟执行。
- 持仓：每账户取 `top_n=10`，近似等权。
- 因子：PE、PB、ROE、毛利率、资产负债率、市值、20 日动量、60 日动量。
- 成本：佣金、最低佣金、卖出印花税、固定滑点、100 股整数倍。
- 产物：信号、待执行订单、交易流水、持仓、NAV、周报、dashboard。

核心代码路径：

- [stock_analyze/strategy.py](../stock_analyze/strategy.py)：股票池、过滤、因子打分、信号生成。
- [stock_analyze/simulator.py](../stock_analyze/simulator.py)：订单生成、模拟成交、持仓和 NAV 更新。
- [stock_analyze/data_provider.py](../stock_analyze/data_provider.py)：行情、指数成分、估值、财务指标和数据源降级。
- [stock_analyze/reporting.py](../stock_analyze/reporting.py)：周报和 dashboard。
- [stock_analyze/store.py](../stock_analyze/store.py)：本地 JSON/CSV 状态读写。

## 探查记录 1：代码与模型实现

探查目标：检查当前模型在策略、数据、模拟交易、报告链路里的实现缺陷和偏差风险。

主要发现：

1. 历史模拟存在未来函数风险。`financial_metrics()`、`valuation_metrics()` 没有严格按 `as_of` 和公告日截断，`baostock_financial_metrics()` 还可能按当前年份取财务数据。历史回测时可能拿到当时不可见的信息。

2. 成交价格路径原先有未来行情风险。`execution_price()` 曾通过完整历史数据找 `>= execute_after` 的第一根 K 线。OpenSpec 修正后，模拟成交通过运行日可见的 `execution_quote()` 取价，运行日之后的行情不会被用于成交。

3. 未成交订单原先会被静默丢弃。OpenSpec 修正后，行情缺失、停牌、涨跌停不可成交、T+1 可卖股数不足或现金不足时，订单保留 pending，并记录 `unfilled_reason`。

4. 交易日历原先只是自然日近似。OpenSpec 修正后，周度调仓会优先使用 AkShare 交易日历；接口和缓存都不可用时才降级到 `next_business_day()`。

5. 股票池预筛选会引入样本偏差。`max_fetch_candidates=180` 先按 PE/PB/市值预筛，再拉取财务和历史数据，后续因子排名不再是完整指数池排名。

6. 缺失值处理不稳定。严格过滤为空时会切换到 `fallback_require_fields`，不同数据源状态下策略风格可能突变；缺失因子填 0 也会混淆“中性缺失”和“明确负面”。

7. 因子归一化过于简单。当前只做横截面百分位排名，没有 winsorize、z-score、行业中性、市值中性、有效样本数记录和因子相关性诊断。

8. 市值因子方向可能变成风格押注。配置里 `market_cap_yi` 是“越大越好”，这会让组合偏大盘，未必是在赚价值/质量/动量的钱。

9. `max_single_weight` 配置原先未被真正实现。OpenSpec 修正后，目标订单会用 `account_total_value * max_single_weight` 对单票目标市值做上限。

10. 买入资金规划与实际成交脱节。订单按信号价估算目标股数，执行时按开盘价和滑点逐单缩水，买入顺序会影响最终权重。

11. 基准没有进入完整绩效计算。系统记录了 `benchmark_close`，但缺少基准收益、超额收益、超额回撤、信息比率等指标。

12. NAV 原先可能重复追加。OpenSpec 修正后，`daily_nav.csv` 按 `date + account_id` upsert，同一天重复运行会保留最新一行。

建议优先修复：

- 给所有数据接口增加明确 `as_of` 语义和公告日约束。
- `execution_price()` 只能看运行日可见数据，不能从完整未来行情里找成交。（已在 OpenSpec P0 修正中处理）
- 未成交订单保留 pending，并记录 `unfilled_reason`。（已在 OpenSpec P0 修正中处理）
- 引入真实 A 股交易日历。（已在 OpenSpec P0 修正中处理，仍保留工作日降级）
- `daily_nav.csv` 按 `date + account_id` upsert 或去重。（已在 OpenSpec P0 修正中处理）
- 补基准收益、超额收益、最大超额回撤、信息比率。
- 实现 `max_single_weight`、现金缓冲、成交后权重偏离记录。

## 探查记录 2：主流量化方法对比

探查目标：用主流量化系统的结构对照当前项目，判断差距和升级路径。

主流股票量化系统通常包含这些模块：

| 模块 | 主流做法 | 当前差距 |
| --- | --- | --- |
| 数据层 | 行情、复权、指数成分、财务、公告、行业、市值、停牌、涨跌停、交易日历、公司行动，并保证 point-in-time 可见性。 | 有多源数据和缓存，但缺少 PIT 财务公告日、历史指数成分、公司行动统一口径、数据质量审计。 |
| 因子层 | 价值、质量/盈利能力、动量、规模、低波、红利、成长、投资、流动性、反转等，通常做 winsorize、标准化、行业/市值中性化、IC/分组收益检验。 | 当前是固定权重百分位打分，没有 IC/RankIC、分组收益、因子衰减、因子相关性和中性化。 |
| 风险模型 | 拆解行业、市值、风格、市场、个股特异风险，用协方差矩阵做 ex-ante 风险、暴露和归因。 | 当前只有账户收益和最大回撤雏形，没有风险暴露、跟踪误差、行业/风格归因。 |
| 组合优化 | 带约束优化：单票上限、行业上限、跟踪误差、换手率、流动性容量、风格暴露、交易成本。 | 当前是前 10 等权，没有优化器、行业约束、换手约束、流动性容量约束。 |
| 交易执行 | 处理成交价、排队、涨跌停不可成交、停牌、T+1、最小交易单位、费用、滑点、冲击成本、撤单/部分成交。 | 当前用下一交易日价格或参考价模拟，缺少涨跌停、停牌冻结、T+1 可卖股数、部分成交和成交量占比。 |
| 回测验证 | 多年历史回测、滚动样本外、交易成本、基准比较、风险指标、参数敏感性、幸存者偏差检查。 | 当前偏前向模拟，历史回测和样本外检验还没有建立。 |
| 生产监控 | 数据源健康、信号漂移、仓位暴露、净值回撤、成交失败、换手率、规则变化、ST/退市风险。 | 有 dashboard 和数据源健康记录，但没有告警、暴露漂移、成交失败原因聚合。 |

A 股场景必须建模为硬约束：

| 事项 | 模型影响 |
| --- | --- |
| 涨跌停 | 买入涨停、卖出跌停可能无法成交，不能只按开盘价或收盘价成交。主板通常 10%，ST 通常 5%，科创板/创业板常见 20%，不同板块和新股阶段规则不同。 |
| 停牌 | 停牌期间不能成交，复牌可能跳空，持仓应冻结，订单应保留或过期，而不是消失。 |
| T+1 | 当日买入股票通常不能当日卖出，影响短线、止损和再平衡模拟。 |
| 100 股整数倍 | 买入股票申报数量应为 100 股或其整数倍，卖出零股需要特殊处理。 |
| 交易时间和休市 | 需要交易所日历，不能用周末近似。 |
| 印花税和佣金 | 卖出印花税、佣金和最低佣金直接影响高换手策略。 |
| ST/退市整理 | 需要剔除或单独风险处理，且规则要按生效日区分。 |
| 财报公告日 | 财务因子必须按公告日生效，不能用报告期日期。 |
| 历史指数成分 | 用沪深300/中证500做股票池时，历史回测要用当时成分，不能用今天成分倒推历史。 |

适合当前项目的升级顺序：

1. 先补正确性地基：交易日历、涨跌停价、停牌状态、T+1 可卖股数、ST/退市状态、历史指数成分、财报公告日。
2. 建历史回测 MVP：同一配置跑 3 到 5 年，输出年化、超额、最大回撤、夏普、换手、月度收益、相对沪深300/中证500表现。
3. 建因子研究表：覆盖率、缺失率、IC/RankIC、分组收益、多空收益、行业/市值中性前后对比。
4. 升级因子处理：winsorize + z-score + 行业内排名或回归中性化；市值从加分因子改成风险暴露或约束。
5. 加组合约束：保留 TopN 可解释性，同时加入行业上限、单票上限、最大换手、最低成交额、涨跌停不可交易。
6. 完善前向模拟监控：每天记录信号、持仓、未成交原因、涨跌停触发、数据源失败、组合行业/市值暴露。
7. 最后再讨论真实下单接口。在历史回测和前向模拟稳定前，不建议接券商真实交易。

## 探查记录 3：工程化与 Dashboard

探查目标：检查系统是否能长期无人值守运行、是否可复现实验、dashboard 是否能支撑研究和监控。

当前做得好的地方：

- 运维入口清楚，README 和运行手册已经说明 `init`、`run-daily`、`run-weekly`、`dashboard`、`serve-dashboard`。
- 部署有雏形，daily、weekly、dashboard 的 systemd service/timer 分开，dashboard 默认只监听 `127.0.0.1`。
- 数据源韧性有意识，`data_health.json` 记录了数据源、状态、消息和行数。
- 本地状态结构简单，适合初期调试：`state.json`、`pending_orders.json`、`daily_nav.csv`、`trades.csv`、`positions.csv`、`latest_signals.csv`、`performance_summary.json`。
- 凭据边界正确，东方财富 Cookie 只通过 `EASTMONEY_COOKIE` 环境变量传入，不写入配置、仓库和日志。

主要工程短板：

1. 存储还不是可信账本。JSON/CSV 直接覆盖或 append，缺少原子写、锁、事务、唯一键、schema 版本和迁移机制。

2. 实验不可复现。`latest_signals.csv` 会覆盖，缺少 `run_id`、配置快照、代码版本、依赖版本、数据快照引用和数据源版本。

3. 调度不防重入。systemd timer 可能补跑，daily 和 weekly 理论上可能并发写同一批状态文件，需要文件锁或任务锁。

4. Dashboard 目前是结果页，还不是决策控制台。已展示净值、信号、因子均值、订单、持仓、交易和数据源状态，但缺少回撤归因、换手、暴露、告警、运行状态和实验对比。

5. 监控只到人眼看文件。`data_health.json` 没有历史化、失败率、任务耗时、SLA、告警状态和恢复记录。

6. 凭据管理还只是文档约束。systemd 模板没有 `EnvironmentFile=`、secret 权限建议、rotation 流程和日志脱敏测试。

推荐 dashboard 三层视图：

| 视图 | 重点问题 | 关键内容 |
| --- | --- | --- |
| 策略研究层 | 为什么选它，这套规则是否稳定？ | 策略版本、配置快照、因子权重、候选池漏斗、入选/剔除原因、因子分布、信号历史、参数实验对比。 |
| 运行监控层 | 系统今天有没有可靠跑完？ | 最近任务状态、运行耗时、数据源成功率、缓存命中、降级次数、pending orders 年龄、NAV 是否按交易日更新、日志错误摘要。 |
| 风控归因层 | 亏赚来自策略、市场、成本还是数据问题？ | 组合收益 vs 基准、超额收益、最大回撤、单票/行业/因子暴露、换手率、交易成本、滑点、未成交原因、数据缺失影响。 |

推荐数据 schema：

- `runs`: `run_id, run_type, as_of, started_at, finished_at, status, code_version, config_hash, data_snapshot_id, duration_ms, error_summary`
- `strategy_configs`: `config_hash, strategy_id, config_json, created_at`
- `data_source_events`: `run_id, time, source, status, rows, latency_ms, message_hash, fallback_level`
- `signals`: `run_id, account_id, signal_date, code, score, factor_json, warnings_json, rank, selected`
- `orders`: `run_id, account_id, signal_date, execute_after, code, side, target_shares, delta_shares, reference_price, status, unfilled_reason`
- `trades`: `trade_id, run_id, account_id, trade_date, code, side, shares, price, commission, tax, slippage, cash_after`
- `positions_daily`: `date, account_id, code, shares, avg_cost, last_price, market_value, unrealized_pnl`
- `nav_daily`: `date, account_id, cash, market_value, total_value, benchmark_code, benchmark_close, benchmark_return`
- `risk_daily`: `date, account_id, turnover, max_position_weight, drawdown, excess_return, cost_bps, missing_data_count`
- `alerts`: `alert_id, run_id, severity, category, title, detail_hash, status, created_at, resolved_at`

推荐调度和告警：

- 短期保留 systemd，但 daily/weekly 加 `flock`，避免并发写。
- systemd 增加 `EnvironmentFile=/etc/stock-analyze/stock-analyze.env`，并使用受限权限运行。
- 每次执行生成 `run_id`，任务开始写 `running`，结束写 `success` 或 `failed`。
- Dashboard 读取最近一次成功 run 和最近一次失败 run。
- 告警规则先覆盖：任务失败、任务超时、连续两次未更新 NAV、数据源全部失败、缓存过旧、pending orders 超期、组合回撤超阈值、dashboard 过久未刷新。
- 备份数据库和关键 CSV，至少保留 30 天，保证某天看到的结果可以回放。

## 主线程补查资料记录

为了校准“主流方式”的判断，本轮补查了以下公开资料：

- Qlib 文档将量化平台拆成数据层、模型训练、组合管理与回测、实验记录、分析报告和在线服务；其文档中也有 point-in-time 数据库、`backtest_daily`、`risk_analysis`、`score_ic` 等模块。
- QuantConnect/LEAN 的 Algorithm Framework 明确区分 Universe Selection、Alpha、Portfolio Construction、Risk Management、Execution，强调模块职责分离。
- QuantConnect 的写算法文档把现实建模列为关键部分，包括 fill、slippage、fees 等。
- Alphalens 是预测性 alpha 因子的分析库，核心 tear sheet 包括收益分析、IC 分析、换手分析和分组分析。
- Kenneth French Data Library 提供 Fama/French 3 因子、5 因子、规模、账面市值比、盈利能力、投资、动量、反转等研究组合和因子数据。
- 上交所交易机制说明包含 100 股申报单位、A 股 0.01 元最小变动单位、主板 10% 涨跌幅、风险警示股票 5% 涨跌幅、价格优先和时间优先。
- 上交所交易规则明确交易日、交易时间、T+1 相关约束、100 股整数倍、涨跌幅价格和有效申报。
- 财政部和税务总局 2023 年第 39 号公告确认自 2023-08-28 起证券交易印花税减半征收。

## 总体差距矩阵

| 维度 | 当前水平 | 目标水平 | 优先级 |
| --- | --- | --- | --- |
| 前向模拟 | 能跑，能生成信号、订单、交易、NAV 和 dashboard。 | 订单状态完整、数据可追溯、模拟成交符合 A 股交易约束。 | P0 |
| 数据 PIT | 财务和估值有缓存，但不严格按公告日。 | 财务、估值、指数成分、公司行动均支持 point-in-time。 | P0 |
| 交易日历 | 只做工作日近似。 | 接入 A 股真实交易日历和休市日。 | P0 |
| 订单状态 | pending 到成交流水，中间状态不足。 | pending、partially_filled、filled、canceled、expired、unfilled_reason。 | P0 |
| NAV | append 为主，可能重复。 | 按日期和账户 upsert，保证唯一净值点。 | P0 |
| 因子研究 | 固定权重百分位打分。 | 覆盖率、IC/RankIC、分组收益、因子衰减、相关性、中性化。 | P1 |
| 组合构建 | 前 10 等权。 | 单票/行业/风格/换手/流动性约束，后续可接优化器。 | P1 |
| 风控归因 | 最大回撤和净值曲线。 | 超额收益、跟踪误差、行业暴露、风格暴露、成本归因。 | P1 |
| 工程账本 | JSON/CSV 文件态。 | SQLite 或 DuckDB run ledger + CSV 导出。 | P1 |
| Dashboard | 静态结果页。 | 策略研究、运行监控、风控归因三层控制台。 | P2 |
| 告警 | 依赖人工看日志和 dashboard。 | 任务失败、数据失败、NAV 停更、订单超期、回撤超阈值告警。 | P2 |
| 实盘接口 | 不接券商。 | 只在回测和前向模拟稳定后再评估。 | P5 |

## 推荐实施路线

### P0：先修模拟正确性

目标：让“模拟结果不明显虚高、不静默丢单、不重复污染 NAV”。

- 引入真实交易日历，替换 `next_business_day()` 的周末近似。
- 修正 `execution_price()`，只使用运行日可见行情。
- 未成交订单保留 pending，并写入 `unfilled_reason`。
- 模拟涨跌停不可成交、停牌不可成交、T+1 可卖股数。
- `daily_nav.csv` 按 `date + account_id` 去重或 upsert。
- 明确 demo 强制执行模式，避免把“假装到了执行日”的演示和正式模拟混淆。
- 实现 `max_single_weight` 和现金缓冲。

### P1：建立可信数据和回测底座

目标：从“跑一次看一次”变成“可复现、可回放、可比较”。

- 新增 SQLite 或 DuckDB，建立 `runs`、`signals`、`orders`、`trades`、`positions_daily`、`nav_daily`、`data_source_events`。
- 每次运行保存 `run_id`、配置 hash、代码版本、依赖版本、数据源状态。
- 建 PIT 财务数据表，按公告日生效。
- 建历史指数成分表，避免用当前成分倒推历史。
- 建 3 到 5 年历史回测 MVP，输出账户收益、基准收益、超额收益、最大回撤、夏普、换手、成本。

### P2：建立因子研究能力

目标：证明因子在当前股票池里有没有稳定解释力。

- 输出每个因子的覆盖率、缺失率、分布、极值。
- 实现 winsorize、z-score、行业内排名、回归中性化。
- 计算 IC、RankIC、ICIR、分组收益、多空收益、因子衰减、换手。
- 把市值从“越大越好”的加分因子改成风险暴露或约束。
- 保存完整因子原值、标准化值、分位、贡献和入选/剔除理由。

### P3：组合与风险模型

目标：从 TopN 等权升级到可控风险的组合。

- 加行业上限、单票上限、现金下限、最大换手、最低成交额。
- 加组合实际权重、目标权重、偏离和现金使用率。
- 计算行业暴露、市值暴露、因子暴露、跟踪误差、信息比率。
- 后续再接 `cvxpy` 或 `PyPortfolioOpt` 做约束优化。

### P4：Dashboard 和监控升级

目标：让 dashboard 回答“为什么选、有没有跑对、风险在哪里”。

- 策略研究页：因子权重、候选漏斗、入选/剔除理由、因子分布、信号历史。
- 运行监控页：任务状态、运行耗时、数据源成功率、缓存命中、降级次数、日志摘要。
- 风控归因页：收益 vs 基准、超额收益、回撤、换手、成本、行业/风格暴露。
- 告警页：任务失败、数据失败、NAV 停更、pending orders 超期、回撤超阈值。

### P5：实盘前置条件

目标：避免过早接真实交易。

实盘接口应放在最后。至少满足以下条件后再考虑：

- 历史回测无明显未来函数和幸存者偏差。
- 前向模拟连续运行一段时间，订单状态和 NAV 稳定。
- dashboard 能清楚解释收益、风险、成交失败和数据异常。
- 真实交易前有手工确认、只读账户同步、权限隔离和风控熔断。

## P1 落地状态（2026-05-19）

OpenSpec change [`align-beginner-friendly-multi-factor-foundations`](../openspec/changes/align-beginner-friendly-multi-factor-foundations/proposal.md) 已实现并合入 main：

- 因子流水线 winsorize → z-score → 行业中性化 → 按可用因子归一权重。
- 单行业上限、持仓 buffer、`max_holding_days` 强制重评估。
- 市值降级为流动性/规模过滤；新增可选低波 60 与 TTM 股息率因子。
- 年化收益/波动、Sharpe、Sortino、最大回撤天数、累计/年化超额、跟踪误差、信息比率、双边换手率、成本占比 bps、FIFO Win Rate。
- 因子覆盖率累计 + 前向 5 日 Spearman RankIC（不依赖 scipy）。
- `runs.csv` 运行账本 + 按 `config_hash` 归档的 config snapshot；`code_version` 直接读 `.git/HEAD`。

后续相关 change 的落地节奏：

- [`introduce-dual-agent-competition`](../openspec/changes/introduce-dual-agent-competition/proposal.md)：双 agent 共享起跑线、月度对比、聚合 dashboard。
- [`enable-cli-based-agent-analysis`](../openspec/changes/enable-cli-based-agent-analysis/proposal.md)：本地 CLI 分析闭环 + 周/月 briefing + slash commands + sync 脚本，无 API key 依赖。
- [`expand-portfolio-capacity-and-strategy-visibility`](../openspec/changes/expand-portfolio-capacity-and-strategy-visibility/proposal.md)：组合容量提到 100 只、策略演进可视化、系统总览文档。
- 仍未启动：`enable-monthly-config-evolution`（提案自动应用）、`add-historical-backtest-baseline`、`introduce-point-in-time-fundamentals`、`add-research-factor-toolkit`。

## 参考资料

- Qlib 文档：https://qlib.readthedocs.io/en/latest/
- Qlib point-in-time 数据库：https://qlib.readthedocs.io/en/latest/advanced/PIT.html
- Qlib 组合管理与回测：https://qlib.readthedocs.io/en/latest/component/strategy.html
- Qlib 分析报告：https://qlib.readthedocs.io/en/latest/component/report.html
- QuantConnect Algorithm Framework：https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview
- QuantConnect Execution Model：https://www.quantconnect.com/docs/v1/algorithm-framework/execution
- QuantConnect 写算法总览：https://www.quantconnect.com/docs/v2/writing-algorithms
- Alphalens 文档：https://quantopian.github.io/alphalens/
- Kenneth French Data Library：https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
- 上交所英文交易机制：https://english.sse.com.cn/start/trading/mechanism/
- 上交所交易规则修改通知：https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20150912_3988782.shtml
- 财政部/税务总局证券交易印花税公告：https://szs.mof.gov.cn/zhengcefabu/202308/t20230827_3904226.htm
- S&P Quality, Value & Momentum Multi-factor Indices Methodology：https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-quality-value-momentum-multi-factor-indices.pdf
- MSCI Barra Global Equity Model：https://app2.msci.com/products/analytics/models/global_equity_model/
- FTSE Russell Focused Factor Indexes Methodology：https://www.lseg.com/content/dam/ftse-russell/en_us/documents/other/ftse-focused-factor-indexes-methodology.pdf
- Fama-French 五因子模型：https://academic.oup.com/rfs/article/29/1/69/1843682
- Carhart 1997：https://ideas.repec.org/a/bla/jfinan/v52y1997i1p57-82.html
