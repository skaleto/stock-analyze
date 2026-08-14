# 双策略模拟竞赛运行手册

## 1. 目标与边界

系统维护两套长期风格不同的纸面策略，在同一资金、交易成本、信号日和
执行规则下比较净收益与风险。系统不连接真实券商，不产生真实委托，也不构成
投资建议。

2026-07-11 起由 Codex 同时维护两个策略。内部 ID 为历史兼容字段，页面和通知
统一使用产品名称：

| 内部 ID | 产品名称 | 核心假设 | 展示色 |
| --- | --- | --- | --- |
| `claude` | 稳健防守 | 价值质量、低波、低换手 | 琥珀色 |
| `codex` | 趋势进攻 | 动量成长、主动换仓 | 青色 |

当前赛季定义见 `configs/strategy_competition.json`。赛季从生效日前最后一个可用
净值点归一为 1，保留历史账本但不把旧策略收益混入新赛季比较。

2026-07-13 起在当前 S1 赛季内切换为每日收盘决策。赛季 ID、净值锚点、资金、
持仓和历史收益均不重置；规则变更仅取消固定周度调仓日。

## 2. 市场与账户

活跃市场只有：

- `a_share`：A 股账户。
- `cn_qdii_etf`：境内上市、可由大陆证券账户交易的跨境 ETF/QDII 账户。

美股和港股个股的直接模拟已经归档在 `archive/direct-overseas/`，不会参与调度。
每个活跃市场各有“稳健防守”和“趋势进攻”两个独立账户，共四条流水线。

关键路径：

```text
configs/competition_<market>.yaml       公平与交易基线
configs/agents/<agent>_<market>.yaml    活动策略
configs/strategy_competition.json       赛季与产品元数据
configs/strategy_versions/<release>/    不可变候选版本与发布 manifest
data/<market>/<agent>/                  状态、净值、成交、订单、运行账本
reports/app/                            React 生产构建
```

## 3. 两类策略

### 稳健防守

A 股侧强调低估值、ROE、低负债、低波动和分红，限制行业集中度并延长持有周期。
QDII 侧强调低波动、折溢价约束和流动性，降低短期追涨权重。

### 趋势进攻

A 股侧强调 20/60 日动量、盈利增长和质量，允许更主动的换仓。QDII 侧提高
20/60 日动量权重，使用流动性约束控制可交易性。

两套配置的因子向量距离必须高于注册表中的门槛。该门槛防止“表面两个版本，
实际同一策略”。

```bash
python3 -m stock_analyze --market a_share validate-strategy-pair
python3 -m stock_analyze --market cn_qdii_etf validate-strategy-pair
```

## 4. 策略发布

策略更新必须通过版本 manifest 原子发布，不应直接覆盖线上活动 YAML：

```bash
python3 -m stock_analyze apply-strategy-release \
  --manifest configs/strategy_versions/2026-07-takeover/manifest.json
```

发布会依次执行：

1. 校验四份候选 overlay 的 schema 与基线锁。
2. 对两套 A 股候选策略执行完整预检；全部通过前不写任何活动配置。
3. 校验每个市场的双策略差异度。
4. 把旧策略遗留待单归档到 `pending_order_archive/<release_id>.json` 后清空活动队列。
5. 写入活动配置、历史备份、演化日志、diff 和审计 CSV。
6. 门禁失败则不切换任何活动策略；中断后重跑可恢复待单归档步骤。

同一 manifest 再次执行应返回 unchanged，便于安全重试。

## 5. 本地运行

首次创建某个账户：

```bash
python3 -m stock_analyze --market <market> --agent <agent> init
```

每日收盘决策、执行到期订单、更新净值并生成下一交易日纸面订单：

```bash
python3 -m stock_analyze --market <market> --agent <agent> run-daily
```

周度复盘、诊断、报告和 briefing：

```bash
python3 -m stock_analyze --market <market> --agent <agent> run-weekly
```

`run-weekly` 不生成订单。`run-daily` 每个交易日先处理已到期订单并估值，再用
当日收盘数据替换下一交易日目标。没有证券越过策略的持仓缓冲和风控门槛时，
每日决策可以产生零笔订单。订单只有在达到执行日、通过停牌/涨跌停/流动性等
规则后才进入 `trades.csv` 和持仓。

### QDII 容量研究

P2 研究使用共享缓存离线比较 `top_n=4 5 6 8 10`：

```bash
python3 -m stock_analyze qdii-capacity-study \
  --start 2023-07-12 --end 2026-07-10 \
  --top-n 4 5 6 8 10
```

命令按周回放信号、下一交易日开盘成交、100 股手数、佣金、滑点、现金保留和
单标的权重上限，输出净收益、超额、回撤、换手、成本、指数集中度与有效相关簇。
它只写 `data/cn_qdii_etf/research/` 和 `reports/competition/research/`，不自动修改
活动策略、`top_n`、账户资金、待单或竞赛基线。

当前研究使用现存基金目录回放历史，明确存在幸存者偏差；在补齐历史目录和公告
事件链路前，报告只能作为容量证据，不能单独触发新赛季发布。

### QDII P2 研究工作流

全球权益、商品与债券范围始终写入 `data/cn_qdii_etf/research/`，不会创建活动
账户或竞赛订单。手工刷新命令：

```bash
python3 -m stock_analyze refresh-qdii-events
python3 -m stock_analyze qdii-shadow-research --refresh-data
```

公告记录保留发布时间、首次观测时间、解析版本、内容哈希和来源链接。暂停申赎、
终止与清盘等活动硬事件会阻断新订单；恢复公告只解除临时阻断。指数级主题观点
必须带来源，并只进入影子研究：

```bash
python3 -m stock_analyze record-theme-sentiment \
  --agent codex --week-end YYYY-MM-DD --index-key nikkei_225 \
  --score 0.2 --confidence 0.7 --drivers "日元与企业盈利" \
  --sources "https://source.example/item" --llm-model gpt-5.6
```

Dashboard 的“跨境 ETF 研究工作台”包含候选、全球影子、风险事件和主题观点四个
动态页签。任何范围晋级前仍需满足三年数据、95% 风险字段覆盖、无前视、回测门槛
和连续四周影子运行。

### 概率预测研究

成熟预测链分成四个可独立重跑的阶段：

```bash
python3 -m stock_analyze --market <market> --agent codex prepare-research-data --offline
python3 -m stock_analyze --market <market> --agent codex run-prediction-research --offline
python3 -m stock_analyze --market <market> --agent codex train-prediction-models --offline
python3 -m stock_analyze --market <market> --agent <agent> predict --offline
```

系统从点时快照生成 3/5/10/20 日标签、技术与量价事件、市场/行业五维状态、校准概率和
独立可信度。A 股财务指标以公告日做 as-of 合并，覆盖质量、成长、现金转化、高附加值和
边际成本代理；跨境 ETF 同步使用净值折溢价、跟踪差、份额、全球指数与汇率。PMI、货币、
通胀、Shibor 和美债曲线按保守发布日期进入宏观/全球状态，不把最新值倒灌到历史。
上市公司公告与官方政策已经进入结构化事实库；商业新闻、同花顺和东财热榜在当前
权限下仍显示 `source_unavailable`，不会用零值或中性值伪造信号。研究预测默认
不参与排序；只有模型注册表为 `active`、可信度
不低于 70%、未触发失效条件的记录才能影响纸面策略，且仍受原交易成本、行业、
单标的权重、手数与下一交易日执行规则约束。

模型按 `research -> shadow -> active` 单向晋级，对应页面上的“研究候选 -> 模拟验证 ->
正式使用”。每个市场和预测周期只固定一个 Challenger；本机月度研究产生的新版本不会在
验证途中替换它。ECS 月度任务只刷新标签并导出不可变训练包，不在小规格主机上拟合；
本机训练锁定训练包声明的精确快照和源指纹，也只注册 challenger；回传时 ECS
必须用原输入包复核同一指纹；
没有校验输入包时只允许生成研究报告，禁止拟合或写 Registry；失败和未晋级结果
也通过不含模型状态的轻量结果包回传，避免 Dashboard 停留在旧轮次；
覆盖率、IC/ICIR、概率校准、命中率提升、净超额、回撤、换手、消融稳定性和至少
12 个同时具备预测和真实前瞻净值证据的有效周全部通过后才允许替换 Champion。
同一周重复运行只计一个周期，尚未结算的预测周不进入页面倒计时。
训练完成后自动执行 `research -> shadow` 门禁；进入 shadow 后由每日预测保存独立
候选产物并按自然周累计，第 12 个有真实前瞻净值证据的有效影子周后自动执行
`shadow -> active` 门禁；仅证据不足时可延长到第 16 周，仍不足则淘汰。
任一证据未通过时保持原状态和原 Champion，并把原因写入 registry 与 Dashboard。
Challenger 晋级为 Champion 后，系统关闭该验证组合并自动选择下一候选版本；两套正式
策略始终只读取 Champion 的规范预测文件，不会使用正在迭代的新版本。

### 模型迭代

`run-model-iteration` 把固定 Challenger 的点时预测用于一个完全隔离的纸面组合，专门
回答“这个候选版本能否把预测转化为可成交收益”。它不改变稳健防守或趋势进攻的持仓，
也不计入双策略排行榜；每个版本的状态、订单、成交和净值独立写入
`data/model_iterations/<market>/<horizon>/<version>/`。旧命令 `run-model-shadow`
只作为兼容别名保留。A 股使用 20 日预测、最多 10 只；跨境 ETF 使用
10 日预测、最多 5 只并保留 2% 现金。候选必须未失效、可信度至少 55%、上涨概率
高于下跌概率且预期超额收益为正，否则账户可以保持全现金。重复运行同一预测版本
不会重复下单或重复写入当日净值。

手动排障时可运行：

```bash
python3 -m stock_analyze --market <market> --as-of YYYY-MM-DD run-model-iteration --offline
```

日常无需手动触发：`stock-analyze-research.service` 在两市场预测完成后自动运行该账户。
Dashboard 左侧“模型迭代”展示 Champion、Challenger、验证进度、版本独立净值、持仓、
订单和预测依据；每日飞书总览
只追加一段紧凑状态，不单独增加通知。

## 6. ECS 调度

A 股继续使用共享行情缓存和触发器。工作日 daily worker 负责成交、估值和下一
交易日决策；周六任务只做复盘产物：

- `stock-analyze-market-data.timer`
- `stock-analyze-weekly-trigger.timer`
- `stock-analyze-monthly-review.timer`
- `stock-analyze-{claude,codex}-{daily,weekly}.service`
- `stock-analyze-research.service`：共享行情成功后离线生成特征、事件、状态和预测，再启动 A 股与跨境 ETF 的四个 daily worker。
- `stock-analyze-model-iteration.service`：仅在四个正式 daily 账本全部成功后启动，避免候选模型与正式账户争抢内存；自身失败独立告警。
- `stock-analyze-model-training.timer`：每月 1 日 02:30 按最新特征快照重建 `next-open-v2` 标签，并为两个市场分别导出带 SHA-256 和源指纹的不可变本机训练包；不在 ECS 执行模型拟合，每市场只保留最近 8 份。随后由本机 runner 锁定包内快照做透明基线与最多 10% 机器学习残差的同窗比较，只有净增量和可部署一致性门禁都通过才回传 Shadow。

QDII 两套策略都有独立定时器：

- `stock-analyze-{claude,codex}-cn-qdii-etf-daily.service`：由研究链成功后启动，执行成交、估值和每日决策；固定 18:50 timer 的单元文件保留，但部署时禁用，避免研究未完成就抢跑。
- `stock-analyze-{claude,codex}-cn-qdii-etf-weekly.timer`：周六 10:15，只生成复盘和报告。
- `stock-analyze-qdii-research.timer`：周六 10:30，刷新公告与多资产影子研究。

飞书只在完整性门禁通过后发送整体消息：

- `stock-analyze-daily-finalize.service`：任一 daily worker 完成后触发，等待并核验当日四条流水线账本；只有 4/4 成功才发送日报。
- `stock-analyze-daily-summary.timer`：周一至周五 21:30 兜底检查，不完整时不发送、不占用去重键。
- `stock-analyze-weekly-summary.timer`：周六 10:45，周任务状态和 Codex 复盘提醒。
- `stock-analyze-monthly-summary.timer`：每月 1 日 09:30，月报状态和策略演化提醒。

单个 agent service 的 `OnSuccess` 触发共享 finalizer，不直接发送消息。失败仍通过
`stock-analyze-pipeline-failure@.service` 立即告警。摘要发送使用 cadence/target
幂等账本，重启服务不会重复推送同一条。每日摘要只增加新的可信度不低于 70% 的
实质性上行/下行变化；相同模型、标的、期限、方向和概率档位不会重复提醒。

检查定时器、最近失败和服务/账本一致性：

```bash
SA_ECS_REMOTE=root@<host>:/opt/stock-analyze/app \
SA_ECS_SSH_OPTS='-i <key>' \
./scripts/check-ecs-timers.sh
```

巡检必须同时覆盖四个 `(market, strategy)` 账户。只看到 parent trigger 成功不代表
子服务成功，应结合 `journalctl`、每个账户的 `runs.csv` 和错误日志判断。

周度和月度的 LLM 判断不会在 ECS 无人值守运行。飞书卡片会给出完整触发语：

```text
运行 YYYY-MM-DD 周度复盘
运行 YYYY-MM 月度策略演化
```

周度复盘只做归因与异常检查，不改策略；月度演化通过版本 manifest、门禁和两阶段
部署更新四份活动策略。旧 `weekly.sh` / `monthly.sh` 只保留为安全状态预检，不再
调用 Claude CLI、录入 sentiment 或直接覆盖 YAML。

## 7. 两阶段部署

第一阶段先部署代码、前端和不可变候选版本，不覆盖线上活动策略：

```bash
SA_SKIP_AGENT_CONFIG_SYNC=1 \
SA_ECS_REMOTE=root@<host>:/opt/stock-analyze/app \
./scripts/deploy-app-to-ecs.sh
```

然后在 ECS 执行 manifest、校验四份 overlay 和两组差异门禁。成功后正常再跑一次
部署脚本，使源码活动配置与线上已接受版本一致。部署脚本不会删除 `data/`。

## 8. 看板与比较维度

启动服务：

```bash
python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765
```

主要入口与接口：

```text
/app.html
/api/dashboard/summary.json
/api/dashboard/detail.json?market=a_share&agent=claude
/api/dashboard/instrument.json?market=a_share&agent=claude&code=<code>
```

策略竞技场按市场比较：赛季收益、基准收益、超额收益、年化波动、Sharpe、最大回撤、
现金、换手、成本及成本基点、持仓/订单/成交数量、因子结构、资产分布、持仓重叠和
日收益相关性。样本不足时展示“数据积累中”，不使用伪造的零值。

进入单策略工作台后可查看时间线、分组持仓、目标订单、策略因子、周报与标的 K 线；
K 线和净值图均支持鼠标十字线读取具体数值。概率工作台按 3/5/10/20 日切换，
分别展示上涨/震荡/下跌概率、可信度、预期超额、证据、失效条件、预警、市场状态、
模型校准和验证周期；标的抽屉仍以 K 线为第一视觉，预测和历史事件证据位于其后。

## 9. 故障定位

常用检查：

```bash
systemctl list-timers --all 'stock-analyze-*' --no-pager
journalctl -u <service> --since '7 days ago' --no-pager
tail -n 20 data/<market>/<agent>/runs.csv
tail -n 20 logs/<agent>-<market>-daily.err
curl -fsS http://127.0.0.1:8765/api/dashboard/summary.json
```

常见原则：

- `competition_baseline_locked:*`：候选 overlay 覆盖了公平基线字段，删除覆盖项。
- `factor_distance_below_floor`：两套策略过于相似，应重新设计因子权重，而非降低门槛。
- 定时器 active 但无账本：检查实际 child service、环境变量和数据源错误。
- 页面旧：确认 `DEPLOY_VERSION`、重建 `reports/app/`、重启 dashboard service。
- CSV 代码缺前导零：读取文本编码字段时缺少显式 `dtype=str`，修复读取链路。

## 10. 评估纪律

目标是比较不同投资假设在真实前向纸面数据中的净成本表现，不是保证收益提升。
至少累计一个完整执行周期后再评价交易差异；至少数周后再评价波动、回撤和相关性。
任何优化结论都应同时陈述收益、风险、成本和样本长度。

公告元数据、PDF/OCR、统一语义抽取、事件隔离和容量操作见
[公告情报运维手册](announcement-intelligence-runbook.md)。公告新因子保持
`observing`，未通过门禁前不进入正式竞赛决策。
