# 行情与资讯数据源补充策略

更新日期：2026-07-24

## 结论

可以接入同花顺、东方财富 Choice、富途等数据，但应使用官方或已授权的
API，不直接抓取 APP 页面、私有接口或逆向客户端协议。

当前最合理的顺序是：

1. 保留 Tushare + Baostock 作为国内证券日线和基础数据主链路。
2. 先探测现有 Tushare 账号的资讯、公告、同花顺/东财热榜和 ETF 专题权限。
3. 权限或覆盖不足时，在 Choice 量化接口与同花顺 iFinD 数据接口中二选一
   试用，不同时采购两套高度重叠的数据。
4. 富途 OpenAPI 只作为海外底层资产、跨夜行情和海外资讯增强源，不作为
   境内上市跨境 ETF 的成交价主源。

## 当前基线

- A 股：Tushare Pro 主源，Baostock 降级，本地按日期缓存。
- 跨境 ETF：Tushare `fund_basic`、`fund_daily`、`fund_nav`、`fund_adj`。
- 资讯：已有统一 SQLite 事件库、原始文档归档、时点字段、来源健康和因子诊断；
  中国政府网、发改委已接入，基金聚合公告只承担发现，未授权公司公告和商业新闻
  明确标记不可用。
- Dashboard：只读取缓存，不在用户打开页面时临时访问外部供应商。

这个基线应继续保留。新数据源通过离线采集任务写入统一库，不直接侵入策略和
Dashboard 请求路径。

### 现有凭据实测（2026-07-24）

已在 ECS 上用当前 Tushare 凭据完成只读权限探测，未打印 token，也未写入交易
数据：

| 接口 | 结果 | 说明 |
|---|---|---|
| `fund_daily` | 可用 | 返回 2,061 行，现有 ETF 日线主链路可继续使用 |
| `major_news` | 无权限 | 当前凭据不能直接补充长篇新闻 |
| `anns_d` 上市公司公告 | 可用 | 已验证按日返回标题、证券代码、公司名和巨潮资讯原文链接 |
| `ths_hot` | 无权限 | 当前凭据不能获取同花顺 App 热榜 |
| `dc_hot` | 无权限 | 当前凭据不能获取东方财富 App 热榜 |

因此，行情与上市公司公告现在都可进入统一事实库和 Dashboard；长篇新闻、APP
热榜和更广泛的舆情仍不能依赖当前 Tushare 权限。后续若试用 Choice / iFinD，
应在同一观察池、同一时间窗下比较新闻覆盖率、时效、误报率和增量价值。

## 数据源比较

| 数据源 | 可获取内容 | 部署形态 | 适合本项目的角色 | 主要约束 |
|---|---|---|---|---|
| Tushare Pro | A 股/基金行情、ETF 专题、公司公告、长篇新闻、同花顺和东财热榜 | Python API，现有 ECS 已接入 | 第一优先级，低改造成本 | 部分接口需单独权限；必须记录数据可见时间 |
| 东方财富 Choice | 行情、基本面、宏观、公司/行业资讯、公告、资讯订阅、舆情标签 | Python/Linux/Mac SDK | 资讯与公告的优先商业备选 | 需要 Choice 账号、授权和流量额度 |
| 同花顺 iFinD | 历史/实时行情、财务、问财、公告、基金实时估值 | Python/Linux SDK 或 HTTP API | ETF 实时估值、公告和问财增强 | 需要数据接口账号；权限与数据量按账号控制 |
| 富途 OpenAPI | 海外 K 线、实时行情、资金流、板块、新闻/公告/评级搜索 | 常驻 OpenD + SDK | 跨境 ETF 底层海外市场的参考数据 | 登录、协议确认、行情权限、限频、历史 K 线额度；不等于 APP 权限 |
| 交易所/基金公司 | 正式公告、基金文件、指数说明 | 官方网页或授权数据服务 | 事实核验与公告权威源 | 实时行情分发通常涉及展示或非展示许可 |

官方能力依据：

- [同花顺数据接口](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/)
  支持历史/实时行情、基金、公告、问财和 Linux Python 接口。
- [同花顺公告查询](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html)
  提供 `THS_ReportQuery`，返回公告时间、标题、证券代码和链接。
- [Choice Python 量化接口](https://quantapi.eastmoney.com/Upload/EMQuantAPI_Python.html)
  的 `cfn`/`cnq` 支持公司资讯、行业资讯、公告、重大事项及资讯订阅。
- [富途行情接口总览](https://openapi.futunn.com/futu-api-doc/quote/overview.html)
  包含历史 K 线、板块、资金流和资讯搜索。
- [富途权限与额度](https://openapi.futunn.com/futu-api-doc/intro/authority.html)
  明确 API 行情权限与 APP 权限不同，并受限频、订阅及历史 K 线额度约束。
- [上证行情许可说明](https://www.sseinfo.com/services/assortment/market/)
  显示 Level-1、Level-2、历史数据和非展示使用均有正式许可体系。

## 推荐的数据模型

新增统一资讯表时，先存元数据和可审计来源，不默认永久保存受版权保护的全文：

```text
news_items
  provider              tushare / choice / ifind / futu / exchange
  provider_item_id      供应商内唯一编号
  published_at          原始发布时间
  fetched_at            系统首次获取时间
  title                 标题
  source_name           原始媒体或发布机构
  source_url            可追溯链接
  category              news / announcement / rating / fund_notice
  instrument_codes      关联的境内证券代码数组
  underlying_codes      关联的海外指数或资产代码数组
  sentiment_label       可选，供应商原始标签
  content_excerpt       授权范围内的短摘要
  content_hash          去重哈希
  license_scope         metadata / excerpt / fulltext
```

所有时间必须同时保留 `published_at` 与 `fetched_at`。回测只能读取当时已经发布且
已经进入系统的数据，避免未来信息泄漏。

## 接入架构

供应商适配层只负责转换成统一结构：

```text
Provider API
  -> raw/provider/date/ 原始响应（按授权范围）
  -> normalize + code mapping + dedupe
  -> news_items / announcements / market_signals
  -> 每周情绪与事件因子
  -> Dashboard 资讯与个股研究页
```

必须具备：

- provider 级开关和凭据环境变量，凭据不进 Git、日志和 API 响应；
- 单源故障不阻断每日估值或订单执行；
- 去重、重试、限频、游标和水位线；
- 原始来源、授权范围、发布时间和采集时间审计；
- 同一字段多源冲突时的确定性优先级；
- Dashboard 只读本地库，不把第三方延迟传给页面。

## 分阶段实施

### P0：零采购验证（已完成）

在 ECS 上使用现有 Tushare 凭据做只读权限探测，不输出 token：

- `major_news`；
- 上市公司公告；
- 同花顺 App 热榜、东方财富 App 热榜；
- ETF 基本信息、跟踪指数、实时参考与指数公司公告。

结果见“现有凭据实测”：基金日线与上市公司公告 `anns_d` 可用；商业新闻、
同花顺热榜和东财热榜仍为 `permission_denied`。

### P1：统一资讯库（基础设施已完成）

已实现来源适配协议、SQLite/原始文档落盘、去重、水位线、时点约束、事件抽取、
质量报告和每 30 分钟定时采集。上市公司公告已经进入独立的 PDF、语义和证据链；
商业新闻因权限不足不会伪装成已接入。新增情报因子保持 `observing`，仍需至少
20 个交易日的覆盖率、延迟、误报、IC 稳定性和消融证据，才能进入模型迭代。

### P1.5：Tushare 全量公告与文档级语义解析（工程完成，生产验收中）

用户已确认继续补齐 Tushare `anns_d` 全量历史、公告 PDF、每日收盘对账和大模型
结构化解析。实施不采用“让大模型自由总结后直接判定利好/利空”的方案，而采用
金融数据厂商和文档级事件抽取研究中已经验证的分层路径：

详细的文件级实施步骤、测试、ECS 调度、真实数据验收和模型门禁见
[`2026-07-24-tushare-full-announcement-llm-extraction.md`](superpowers/plans/2026-07-24-tushare-full-announcement-llm-extraction.md)。

```text
公告目录与精确发布时间
  -> PDF / 页面 / 表格的可审计文本
  -> 文档级多事件抽取
  -> 证据定位与确定性校验
  -> 事件相关性、状态、规模、新颖度和置信度
  -> 时点正确的日频特征
  -> 候选模型、影子周期、正式版本门禁
```

参考基线：

- RavenPack / LSEG 的机器可读资讯方案：按实体和事件输出分类、相关性、情绪、
  新颖度与频次，再聚合为公司级因子；不直接把自然语言摘要当作交易信号。
- DCFEE、Doc2EDAG：中文金融事件必须按文档级抽取，允许一个公告包含多个事件，
  同一事件的主体、金额、日期和状态散落在不同句子或页面。
- FinanceBench、FinQA：金融文档的大模型结果必须附带证据，复杂数字不信任模型
  自算；保留原始操作数，由确定性程序复算金额、比例和同比。
- Feast point-in-time join：训练和回测只能取得当时已发布、已进入系统且仍在有效期
  内的特征。
- LangExtract 式 grounding：每个非空事实必须绑定可在原文重新定位的页码、文本
  坐标和证据片段。

公开资料：

- [RavenPack Company News Factors](https://www.ravenpack.com/products/edge/factors/company-news)
- [LSEG Machine Readable News Analytics](https://www.lseg.com/en/data-analytics/financial-data/financial-news-coverage/political-news-feeds-analysis/news-analytics)
- [DCFEE: Document-level Chinese Financial Event Extraction](https://aclanthology.org/P18-4009/)
- [Doc2EDAG: Chinese Financial Event Extraction](https://aclanthology.org/D19-1032/)
- [FinanceBench](https://arxiv.org/abs/2311.11944)
- [FinQA](https://aclanthology.org/2021.emnlp-main.300/)
- [Feast Point-in-time Joins](https://docs.feast.dev/v0.51-branch/getting-started/concepts/point-in-time-joins)
- [Google LangExtract](https://github.com/google/langextract)

#### 实施阶段

- [x] **A. 全历史目录回补**：显式请求
  `ann_date,ts_code,name,title,url,rec_time`，按自然日和 offset 断点续跑；B 股在
  入口排除。实时库继续记录真实 `first_seen_at`，历史研究库把可靠 `rec_time`
  写入 `research_available_at` 并标记 `reconstructed_rec_time`，缺失精确时间的
  公告保守到下一交易日可用。
- [x] **B. PDF 与版面存储**：PDF 原件进入独立对象存储，ECS 只保存内容哈希、
  页级文本、表格、版面坐标和解析状态；下载、OCR、文本和表格解析分别重试，
  不把解析失败解释成“没有事件”。
- [x] **C. 文档级事件抽取**：标题规则承担快速路由；正文采用固定事件 taxonomy
  和严格 JSON Schema，一次输出零到多个事件记录。所有公告经过轻量路由，高价值
  或不确定公告进入深度模型，同时抽样复核未命中公告以估计召回率。
- [x] **D. 证据与确定性校验**：逐字段验证证券代码、页码、原文片段、金额、币种、
  股数、比例、日期和公告状态。程序复算物质性比例；无证据、冲突或越界结果进入
  quarantine，不进入 canonical events。
- [x] **E. 事件评分与因子化**：相关性由主体角色和文档位置计算，新颖度由历史事件
  相似度计算，方向由事件 taxonomy、生命周期和规模规则计算，置信度由来源、证据
  覆盖和校验结果计算；大模型自报的 confidence 和 sentiment 不直接作为最终分数。
- [x] **F. 模型消费与治理**：只向模型提供版本化数值特征，不提供自由文本结论。
  先做按事件类型的事件研究、Rank IC、误报率、稳定性和消融，再进入
  `model_iteration`、独立影子周期和 `active` 门禁。
- [x] **G. 调度与对账**：保留每 30 分钟公告目录增量任务；新增每日 20:30 的
  T+0/T-1/T-2 对账、PDF/正文解析、结构化抽取、质量报告和一条飞书摘要。全历史
  回补使用独立的限速、可恢复任务，不占用实时任务水位线。

#### 验收边界

- 历史目录无日期断层、无 B 股、源 ID 唯一，分页保护触发时水位线不推进。
- 每个 canonical event 的非空事实均可重新定位到原 PDF 的页码和文本坐标。
- quote 只允许在指定 chunk 中做唯一逐字定位后重算偏移；跨 chunk、改写或多重命中
  不自动猜测，进入仲裁或隔离。
- 数字字段由程序复算，抽取证据缺失率、字段准确率、事件 precision/recall 和
  no-event 抽样漏报率均进入版本化评测报告。
- 训练集使用 point-in-time join；历史重建时间和真实首次看见时间不可混用。
- LLM、prompt、schema、解析器和事件 taxonomy 均有独立版本，重跑不会静默覆盖
  已参与过模型训练的结果。
- 未通过因子门禁或模型门禁时，稳健防守与趋势进攻继续按现有规则运行。

### P2：商业源与官方确认层（接口契约已完成，外部授权待办）

系统已为巨潮、上交所/深交所、基金管理人、央行/财政部/统计局/海关和授权新闻
定义 fail-closed 接口契约。实际启用仍取决于 API 使用规则、发行人注册表或商业授权。
若现有官方源仍缺少公司/行业资讯、舆情标签或 ETF 实时估值：

- 资讯优先试 Choice；
- ETF 实时估值、公告和问财优先试 iFinD；
- 用相同一周、相同代码集合评估覆盖率、延迟、重复率和成本后只选一套。

### P3：富途增强

仅在确实需要海外指数盘中行情、资金流或海外新闻时部署 OpenD。数据只用于解释
境内 ETF 的底层市场，不改变“境内 ETF 价格以境内交易所行情为准”的规则。

## 不采用的方案

- 不抓同花顺、东方财富或富途 APP 页面。
- 不依赖未公开的 `push2`、Cookie 或移动端私有接口作为生产主链路。
- 不把供应商正文无差别复制进数据库。
- 不把富途海外报价当作境内 ETF 可成交价格。
- 不让资讯源故障阻断模拟交易主流程。

公告全量回填、PDF/OCR、统一语义抽取契约、隔离和容量命令见
[公告情报运维手册](announcement-intelligence-runbook.md)。
