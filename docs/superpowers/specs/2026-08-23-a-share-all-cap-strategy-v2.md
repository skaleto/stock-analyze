# A 股全市场分层策略 v2 设计与预注册

## 决策

采用“全市场可投资池 + 大/中/小/微盘独立策略袖套 + 成本约束组合构建”的方案，
但不把当前沪深 300、中证 500 正式账户原地扩容。旧账户、历史流水和负面研究结果
保持不变；新方案使用独立 season、独立子账本和独立准入证据，按袖套逐个通过后才
允许进入隔离纸面运行。

这不是一次“多抓一些股票后重新训练”的活动。首个候选只回答一个冻结问题：在相同
透明因子、相同下一交易日开盘执行和相同成本模型下，把横截面排名改成在 PIT 规模袖套
内计算，并采用与信号衰减及流动性相匹配的固定调仓周期，能否稳定改善净超额收益？
已有事件数据、新闻特征和已经失败的模型交互不进入本活动。

机器可读合同为 `configs/research/a_share_all_cap_v2.yaml`。在数据合同完整、代码评审和
合同提交之前，不读取本活动的 2025 年以后收益，也不改变正式策略配置、模型 Registry
或账户状态。

## 市场成熟做法及其含义

成熟指数和量化流程并不把“指数成分”与“选股模型”混成一层。

- MSCI GIMI 和 FTSE GEIS 都先定义可投资证券，再做规模分层，并同时使用市值、自由
  流通、流动性、交易状态和缓冲区。FTSE 的规则还明确检查过去一年不交易天数，指数
  调整以降低不必要换手为目标。对本项目的含义是：必须先建立 PIT 可投资池，不能把
  当天仍上市的股票主表倒灌到历史，也不能每天按一个硬市值阈值让股票来回跨层。
- 中证 1000 从中证 800 之外选择规模偏小且流动性好的 1,000 只证券；中证 2000 继续
  向下选择 2,000 只，并设置新进/保留缓冲。沪深 300、中证 500、中证 1000、中证
  2000 因而适合做四个规模袖套的官方参照，但不适合继续作为唯一候选池。
- Fama/French 研究组合把规模与价值、盈利等特征分开排序，并按固定日历重构。它支持
  “先按规模分组，再在组内比较因子”的做法，而不是拿微盘股的估值、波动和大盘股直接
  做一个全局排名。
- 中证 A500 和 MSCI/FTSE 多因子方法进一步表明，行业约束的主要作用是控制意外风险，
  而不是凭空创造 alpha；成熟组合还会显式约束非目标风格、单股集中、换手和容量。
- MSCI Momentum 使用成分缓冲降低换手；Garleanu/Pedersen 的有成本动态交易结论是
  只向目标组合移动一部分；AQR 的真实交易成本研究表明，成本优化会显著改变可实现
  结果，短期反转尤其容易被成本吃掉。对本项目的含义是：信号、目标权重和实际交易量
  必须分开，微盘不能沿用大盘的换手和成交假设。
- Deflated Sharpe Ratio 和 Probability of Backtest Overfitting 用于惩罚多重试验和
  策略挑选偏差。所有频率、参数和失败版本都必须进入同一试验台账，不能只上报赢家。

主要依据：

- [MSCI Global Investable Market Indexes Methodology](https://www.msci.com/indexes/index-resources/index-methodology)
- [FTSE Global Equity Index Series Ground Rules](https://www.lseg.com/content/dam/ftse-russell/en_us/documents/ground-rules/ftse-global-equity-index-series-ground-rules.pdf)
- [中证全指编制方案](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/20231208175438-000985_Index_Methodology_cn.pdf)
- [中证 1000 编制方案](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/20231208175402-000852_Index_Methodology_cn.pdf)
- [中证 2000 编制方案](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/20231208180041-932000_Index_Methodology_cn.pdf)
- [Fama/French Benchmark Portfolios](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/f-f_portfolios.html)
- [中证 A500 编制方案](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000510_Index_Methodology_cn.pdf)
- [MSCI Diversified Multi-Factor Indexes Methodology](https://www.msci.com/eqb/methodology/meth_docs/MSCI_Diversified_Multiple-Factor_Indexes_Methodology_May2022.pdf)
- [FTSE Global Factor Index Series Ground Rules](https://www.lseg.com/content/dam/ftse-russell/en_us/documents/ground-rules/ftse-global-factor-index-series-ground-rules.pdf)
- [Dynamic Trading with Predictable Returns and Transaction Costs](https://www.nber.org/papers/w15205)
- [Trading Costs of Asset Pricing Anomalies](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs-of-Asset-Pricing-Anomalies)
- [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)

## 方案比较

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| 继续新增 CSI1000/CSI2000 正式账户 | 改动最少，官方成分和基准清晰 | 仍把研究范围绑在指数上；CSI2000 历史成分仅从 2023 年发布后可得 | 只作为基线，不作为目标架构 |
| 单一全市场模型 | 配置简单，表面上覆盖全部股票 | 规模、流动性、因子分布和成本不可比；大样本会掩盖袖套失败 | 不采用 |
| 全市场 PIT 池 + 四个袖套 | 研究范围完整，风险和成本可分层，袖套可独立准入 | 数据合同和账户架构改动较大 | 采用 |

采用的方案仍保留官方指数作为“锚”和基准，不把指数成分当候选池。所有合格股票每季
按 PIT 总市值排名进入规模袖套，边界目标为 300、800、1,800、3,800 名；当前袖套在
边界上下 10% 缓冲内保持不变。排名 1–300 为大盘，301–800 为中盘，801–1,800 为
小盘，1,801–3,800 为微盘。其余股票仍进入全市场研究目录和候选漏斗，但标记为
`nano_watch`，首个正式候选不分配资金。固定人民币市值标签仅用于 Dashboard 浏览，
不能决定历史策略袖套。

## 当前系统与数据审计

审计日期为 2026-08-23，生产 ECS 的持久化数据是事实来源。

| 项目 | 现状 | 影响 |
| --- | ---: | --- |
| 全市场日行情/每日指标 | 2018-01-02 至 2026-08-14 共 2,091 个交易日；单日代码从 3,282 增至 5,540 | 已具备宽市场原始横截面，无需重新抓取历史行情 |
| 证券主表 | 5,882 条，含 5,543 条上市和 339 条退市记录 | 可建立无幸存者偏差的生命周期边界 |
| 复权因子 | 5,584 个代码已完成 | 接近全市场，需对生命周期范围做最终缺口审计 |
| 财务指标 | 5,855 个代码有完成标记 | 现有两个透明策略的核心财务字段大体可扩展 |
| 利润表/资产负债表/现金流量表 | 各约 2,036 个代码完成 | 丰富质量/成长特征不能直接全市场使用 |
| 历史交易/ST 状态 | Baostock 仅约 1,402 个代码；Tushare `stock_st` 当前账号无权限 | 全市场回测必须先补齐或逐行失败关闭 |
| 历史指数成分 | 缓存只有沪深 300 和中证 500 | 中证 1000/2000 基线尚未落盘 |
| 行业 | 当前快照约 4,063 个代码；旧路径还使用静态 `stock_basic.industry` | 行业中性化存在覆盖和 PIT 风险 |
| 合格特征快照 | 20260814 快照 1,672,726 行、204 列、1,402 个历史代码 | 当前研究特征仍是旧指数并集 |

当前 5,549 只活动 A 股目录与 20260814 合格特征代码的交集为 1,340 只：大盘覆盖
181/194，中盘 605/792，小盘 503/2,175，微盘仅 50/2,382。限制来自物化器固定读取
沪深 300/中证 500 历史并集，而不是原始行情缺失。另一个可直接修复的浪费是：原始
`daily_basic` 已含 `float_share`、`free_share` 和 `circ_mv`，当前物化 schema 却只保留
`total_mv`。

已对当前数据权限做只读探测：中证 1000 月度成分可回溯至 2018；中证 2000 成分从
2023-09 发布期开始可用，指数日线可回溯至 2018；中证全指日线可用；申万 2021 一级
行业的当前与历史调入/调出记录可按行业分批获取；`stk_limit` 可按日取得全市场涨跌停
价格；`stock_st` 无权限，因此历史 ST 使用现有 Baostock + 名称变更双源合同。

生产盘总空间 40GB，当前仅余约 3.1GB。新流程不得复制 2.8GB 的已有行情缓存，也不得
生成“5,500 只股票 × 每个交易日 × 208 列”的第二份宽表。特征只在冻结的决策日生成，
原始行情按引用读取，成员和标签按年分区；任何发布都必须在写前估算空间并保留至少
15% 文件系统余量。

## A 股小微盘的特殊风险

小微盘不能仅按“大盘策略换一个股票池”处理。历史研究显示，中国最小市值股票的收益
曾显著混入壳价值；但证监会 2024 年退市意见明确要求削减壳资源价值并加快出清，说明
旧时期的微盘溢价存在制度断点，不能线性外推。相关依据为
[Size and Value in China](https://www.nber.org/papers/w24458) 与
[证监会严格执行退市制度意见](https://www.csrc.gov.cn/csrc/c100028/c7473607/content.shtml)。

A 股买入后在交收前通常不能卖出，主板/创业板又分别存在 10%/20% 涨跌幅限制和特殊
无涨跌幅阶段；北交所和科创板规则还不同。历史模拟必须按交易日、板块、ST 和上市阶段
使用精确限制价格，封死涨跌停、停牌和无对手盘时成交量为零。不能用第二天开盘价假定
小微盘总能退出。规则依据见
[深交所交易规则（2026 年修订）](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)。

因此 3,800 名以后股票虽然保留在目录、数据质量和信号漏斗中，但首轮只作为
`nano_watch`。微盘正式准入必须额外展示异常上涨/换手、连续跌停、成交减半、五日清算
和盈亏平衡 AUM；短期动量结果若只来自极端涨停尾部，不能以正均值替代典型股票证据。

## PIT 可投资池合同

季度评审日只使用当日收盘后已经可见的数据，变更从下一交易日生效。ST、退市、整日
停牌和精确涨跌停等硬交易状态仍按日更新，不等待季度重构。每条成员记录保留
`review_date`、`effective_date`、`code`、`eligible`、`exclusion_reasons`、`size_rank`、
`raw_sleeve`、`stable_sleeve`、市值/流动性来源日期、状态来源和行业有效区间。

证券必须同时满足：

1. 在该历史日期已经上市且尚未退市；主板/创业板上市满一个季度，科创板满一年，
   北交所满两年。现有策略自己的更长上市期过滤仍可继续收紧。
2. 非 ST、非 *ST，评审日不是整日停牌；状态缺失不能默认正常。
3. 过去 252 个可用交易日中不交易日少于 60；历史不足时按上市后的可用日数同比例计算。
4. 新进入证券的过去一年日均成交额必须位于合格样本前 80%；已有证券位于前 90% 时
   可以继续持有，跌出前 90% 才退出。市值、成交额和复权因子为正且来源日期不晚于
   评审日。
5. 评分需要的核心特征达到本袖套覆盖门槛；缺失只会排除该证券或令该候选失败，不做
   跨期回填。

申万行业按 `in_date <= signal_date < out_date` 做 as-of join。没有历史行业时标记
`unclassified`，不得用 2026 年行业反填 2018 年。ST 以 Baostock 日状态为主、Tushare
名称变更区间为交叉校验；冲突行失败关闭。涨跌停使用 `stk_limit` 原始上下限，不再只
依赖代码板块和 ST 标志推算。

## 两层决策与候选策略

第一层每日处理硬状态、每季决定“哪些股票可研究、属于哪个规模袖套”；第二层只在本袖套内部做因子
截面处理、排名和组合构建。每个袖套独立记录候选数、覆盖率、目标、成交和收益，聚合
结果不能掩盖失败袖套。

首个透明候选不修改两种策略的因子权重：

- 稳健防守继续使用价值、质量、低波和股息，四个袖套均每 20 个交易日更新一次目标。
- 趋势进攻继续使用 20/60 日动量、成长、质量和低波；大/中盘每 5 日、小盘每 10 日、
  微盘每 20 日更新目标。

每次目标更新都先在袖套内 winsorize、行业中性化和排名，再经过现有行业上限、持仓
缓冲、最大持有期和风险调整权重。实际订单受单股权重、袖套换手、最小交易权重、下一
开盘可交易性和成交额参与率共同限制，只向目标权重移动允许的一部分。基础情形订单不
超过 2% ADV，5% ADV 为硬上限；流动性缺失按最高冲击成本处理并禁止新增仓位。
当完整目标相对当前持仓的双边换手为 `T`、本期允许换手为 `Tmax` 时，首个候选固定使用
`alpha=min(1,Tmax/T)`，实际目标为 `alpha*完整目标+(1-alpha)*当前持仓`。已批准的
本期交易再由成交约束执行，组合目标迁移和订单执行不得互相改写。

新 season 的固定战略资金权重为大盘 35%、中盘 30%、小盘 25%、微盘 10%，两个策略
完全相同。v2 首轮不做动态规模轮动，因为同时改变选股和袖套配置将无法归因。以后若要
做规模择时，必须作为另一项独立、登记全部试验次数的 Challenger。

## 统一测评基准

每个候选必须在完全相同的样本、执行日和成本上同时比较：

1. 对应官方价格指数：大盘 000300.SH、中盘 000905.SH、小盘 000852.SH、微盘
   932000.CSI；聚合参考 000985.CSI。
2. PIT 合格袖套的市值加权持有基线和等权持有基线。
3. 现有透明策略在旧 HS300/ZZ500 边界上的只读回放。
4. 相同全市场袖套、但不应用主动因子排序的 `router_only` 基线。

2018-01-02 至 2024-12-31 是四段 purged expanding walk-forward 开发窗口。标签终点必须
早于下一验证段起点；所有频率、成本和实现变体写入统一 trial registry。设计、数据和
代码冻结后，2025-01-01 至 2026-08-21 只打开一次作为最终历史留出集。

单个袖套进入隔离纸面运行必须全部满足：

- PIT、无幸存者偏差、simulator parity 和 checksum 检查通过；关键成员字段 100%，
  行情/每日指标覆盖至少 99%，复权覆盖至少 98%，核心因子逐日覆盖至少 80%。
- 至少 4 个 OOS folds、252 个 OOS 交易日、100 笔已完成交易；Rank IC > 0.02，
  ICIR >= 0.30，至少 3/4 folds 的净超额为正。
- 袖套净超额收益为正；聚合账户年化净超额至少 2%；2 倍交易成本压力下净超额不为负。
- 最大回撤不超过 20%，且不超过对应基准回撤的 1.2 倍；目标成交率至少 95%。
- DSR >= 0.95，PBO <= 0.50；至少 4 个完整日历年净超额为正，单一年份贡献不超过累计
  正超额的 50%。
- 基础情形 99% 的订单不超过 2% ADV，任何订单不超过 5% ADV；压力情形平仓天数不超过
  5 个交易日，涨跌停和停牌不得产生虚假成交。
- 容量报告必须给出 1、5、10、20 倍目标资金下的净收益、冲击成本、未成交率、正常/
  成交减半/连续跌停三种清算天数，以及净超额降至零时的盈亏平衡 AUM；更高资金情形
  用于披露，不改变 100 万纸面账户的历史准入阈值。

聚合账户只有在所有被分配资金的袖套各自通过时才通过；失败袖套保持现金，不允许靠
其他袖套收益抵消。历史留出集还需保持正净超额和上述风险/执行门槛。之后至少运行 60
个交易日且不少于 12 个完整 forward cycles 的隔离纸面账户，才可讨论新 season 的
Active 准入。

## 与现有系统的结合

数据链路增加四个清晰边界：

```text
共享原始行情/财务/状态
        -> 月度 PIT 可投资池与稳定规模袖套
        -> 冻结决策日的特征和多周期 next-open 标签
        -> 袖套内透明评分 + 现有成本/风险组合构建
        -> 隔离 season 子账本 + 聚合只读报告/Dashboard
```

现有 `research.models` 的 purged walk-forward、`research.governance` 的 DSR/PBO、
`research.activation` 的准入、A 股 simulator 的下一开盘/T+1/整手/停复牌逻辑，以及
`execution_policy` 的成交额冲击模型继续复用。需要替换的是硬编码两指数的 data view、
只按指数并集物化的 materializer、全局 850 只预筛和偏向较大市值的 35/35/30 预选。

新 season 使用 `data/a_share/<agent>/seasons/<season_id>/`，其中四个 sleeve 有独立
positions、trades、daily_nav、runs 和状态；旧根目录账本只读保留。Dashboard 增加 season
选择、总账户/袖套拆解、候选漏斗、数据覆盖、基准、成本、成交率和容量页面。Dashboard
请求处理器仍只读已落盘产物，不能直接调用任何数据源。

## 分阶段上线和停止规则

1. 先补齐参考指数、行业、涨跌停、状态和财报缺口，发布校验清单；不生成收益结果。
2. 建立按年分区的 PIT 季度成员表和每日硬状态表，并以官方成分做截面一致性审计。
3. 只在决策日生成宽特征，运行透明基线和 v2 候选，写入不可变 trial registry。
4. 只有开发门槛全部通过才打开一次 2025+ 留出集；失败结果原样提交并停止该候选。
5. 通过的袖套进入独立 paper season；未通过袖套保持现金。正式旧账户不会被迁移、清零
   或重写。

任一数据源权限、历史状态、行业有效区间、磁盘余量或 checksum 不满足，结果为
`insufficient_data`，不能用当前名称、固定行业、推算 ST 或扩大成本上限来修复结果。
