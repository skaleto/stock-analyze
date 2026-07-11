# 行情与资讯数据源补充策略

更新日期：2026-07-11

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
- 资讯：尚无统一资讯库；市场情绪主要由每周人工 LLM 检索后录入。
- Dashboard：只读取缓存，不在用户打开页面时临时访问外部供应商。

这个基线应继续保留。新数据源通过离线采集任务写入统一库，不直接侵入策略和
Dashboard 请求路径。

### 现有凭据实测（2026-07-11）

已在 ECS 上用当前 Tushare 凭据完成只读权限探测，未打印 token，也未写入交易
数据：

| 接口 | 结果 | 说明 |
|---|---|---|
| `fund_daily` | 可用 | 返回 2,061 行，现有 ETF 日线主链路可继续使用 |
| `major_news` | 无权限 | 当前凭据不能直接补充长篇新闻 |
| 上市公司公告 | 无权限 | 当前凭据不能直接补充公告库 |
| `ths_hot` | 无权限 | 当前凭据不能获取同花顺 App 热榜 |
| `dc_hot` | 无权限 | 当前凭据不能获取东方财富 App 热榜 |

因此，行情数据现在可直接用于模拟和 Dashboard；资讯库不能仅靠当前 Tushare
账号落地。短期需要在“开通 Tushare 对应权限”和“试用 Choice / iFinD”之间做
同一观察池、同一时间窗的覆盖率与成本对比。

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

结果见“现有凭据实测”：基金日线可用，新闻、公告、同花顺热榜和东财热榜均为
`permission_denied`。

### P1：统一资讯库

实现 `NewsProvider` 协议、SQLite/Parquet 落盘、去重和每日定时采集。先接
Tushare，验证至少四周的数据完整性、延迟和稳定性。

### P2：商业源试用

若 P1 缺少公司/行业资讯、舆情标签或 ETF 实时估值：

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
