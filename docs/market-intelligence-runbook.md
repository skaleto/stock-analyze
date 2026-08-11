# 市场情报数据链路

## 目标与边界

该链路为模拟研究补充公告、政策和事件数据，不连接券商，也不产生真实订单。
所有数据按 `published_at` 和 `effective_at` 做时点约束；缺失源保持缺失，不填成中性值。

## 当前数据源

| 来源 | 状态 | 用途 | 历史范围 |
| --- | --- | --- | --- |
| 国务院政策库 | 启用 | 国家政策与宏观事件 | 首页可稳定发现的近期文件 |
| 国家发改委官方检索 API | 启用 | 产业政策、支持与约束事件 | 可回补三年 |
| 证监会旧列表 | 禁用 | 监管政策 | 详情重定向会污染发布日期，待替换为可靠入口 |
| Tushare 公告 `anns_d` | 启用 | A 股公司公告元数据、证券映射和官方原文链接 | 默认回看 7 个自然日；支持按日期显式回补 |
| iFinD 量化接口 | 启用补源 | 校验 A 股/跨境 ETF 日线，补充 Tushare 缺失公告 | 每日决策标的；周末全市场公告审计 |
| 东方财富基金公告聚合页 | 启用发现层 | 发现基金申赎、停复牌和清盘线索 | 只能标记 `discovered`，不能直接形成正式硬阻断 |
| 上交所/深交所基金公告 | 契约已定义 | 官方交叉核验 ETF 交易状态 | 适配器和自动化使用规则待确认 |
| 基金管理人/托管人公告 | 契约已定义 | 官方确认申购、赎回、清盘与恢复范围 | 发行人注册表和签名校验待实现 |
| 央行/财政部/统计局/海关 | 契约已定义 | 宏观发布与修订历史 | 各站适配器和发布日期历待实现 |
| 商业新闻与舆情 | 待授权 | 新闻、行业与公司舆情增强 | 未获授权前返回明确不可用，不抓私有 APP |

禁止把来源授权失败、页面重定向或解析失败静默当成“没有事件”。这些状态会写入
`ingestion_runs`、质量报告、Dashboard 和每日汇总。基金事件采用申购、赎回、
交易和基金存续范围状态机；只有 `confirmed` 的权威事件可形成硬阻断，恢复公告
只清除同一范围的限制，未见恢复的停牌/清盘不会被固定 30 天自动解除。

## 自动任务

- `stock-analyze-intelligence.timer` 工作日 09:30、12:30、16:30、21:45、
  23:30 增量抓取并做确定性事件抽取，只刷新轻量状态。
- 每日行情任务读取截至当时已通过校验的最新情报快照，但不依赖情报服务成功；
  公告下载、解析或模型迭代失败不会阻断四个正式纸面账户。
- `stock-analyze-intelligence-artifact-backfill.timer` 在正式链路窗口外约每 20 分钟
  有界续跑 PDF 下载、文本优先解析和必要 OCR；资源不足或正式任务活跃时自动延期。
- `stock-analyze-intelligence-quality.timer` 周日 03:15 执行全库质量扫描，不把重查询
  塞进每次增量采集或 Dashboard 请求。
- 增量、对账、历史回填、语义和质量任务共享后台资源锁；模拟盘关键窗口优先，后台
  任务等待或延期，不与其他情报重任务并发。
- 每日行情任务用 iFinD 复核本次 A 股/跨境 ETF 候选行情，并检查持仓和
  待执行订单涉及的公告；只补 Tushare 缺失项，不覆盖主源数据。
- `stock-analyze-ifind-source-audit.timer` 每周六 08:30 对最近交易日公告做一次
  全市场交叉审计和缺口补齐，失败会进入统一流水线告警。
- 每日研究任务在标签生成后运行 `intelligence-evaluate`，输出覆盖率、Rank IC 和 ICIR。
- 飞书每日消息只追加一行情报总览，不逐条推送文档。

## 手动运维

```bash
python3 -m stock_analyze.cli intelligence-ingest \
  --repo-root . --sources tushare_announcement \
  --since 2026-07-23T00:00:00+08:00 --until 2026-07-25T00:00:00+08:00
python3 -m stock_analyze.cli intelligence-extract --repo-root . --limit 5000
python3 -m stock_analyze.cli intelligence-status --repo-root .
python3 -m stock_analyze.cli intelligence-evaluate --repo-root . --market a_share
python3 -m stock_analyze.cli intelligence-source-audit \
  --repo-root . --datasets market announcement --supplement
```

核心文件：

- 数据库：`data/shared/intelligence/intelligence.sqlite3`
- 原始文档：`data/shared/intelligence/raw/<source>/<yyyy>/<mm>/`
- 质量报告：`reports/intelligence/quality_latest.json`
- 双源审计：`reports/intelligence/source_audit_latest.json`
- 因子诊断：`reports/intelligence/factor_validation_<market>_<date>.json`
- 因子生命周期：`configs/intelligence_factors.json`

Tushare 公告按自然日、`limit/offset` 分页，单日达到分页保护上限时整批
fail closed 且不推进水位线。重复运行会利用公告 ID 和内容哈希去重；同一天会
重复拉取以捕获盘中晚到公告。公告归属日和因子截止时间使用
`Asia/Shanghai`，持久化时间统一为 UTC；历史回补不会倒退增量水位线。
回补数据的 `first_seen_at` 使用实际采集时间，不会伪装成历史当日已知数据。
当前落库范围是公告标题、证券代码、公司名、公告日期和巨潮资讯官方原文链接，
不批量镜像 PDF。`anns_d` 返回中混入的 `200xxx.SZ`、`900xxx.SH` B 股公告
在采集边界直接过滤，不进入本系统文档库或事件库。

iFinD 通过独立 Python 运行时和 stdin/stdout JSON 网关接入，不加载到主应用
进程。凭据只从 root 可读文件读取；带临时 token 的公告 PDF 地址不入库。
行情统一为不复权、原币口径：iFinD 成交量从股、成交额从元进入比较层；
Tushare 缓存按市场原始单位转换后再校验。匹配数据只记审计证据，主源缺失且
iFinD 有值时才以 `ifind_hq_fallback` 补入，并保留来源标记。

## 因子上线纪律

八个事件因子初始均为 `observing`，不会进入训练。诊断会同时计算覆盖率、采集
延迟、多个预测周期的 Rank IC、IC 符号与子区间稳定性、事件后异常收益、衰减、
误报率和移除该因子后的多空收益差。只有配置声明的证据全部存在且报告哈希可核验，
生命周期才会建议进入 `model_iteration`；旧的单一标量或缺失报告会 fail closed。
模型迭代还要经过时点/幸存者偏差审计、12 个独立影子周期、DSR/PBO 与角色门禁，
才能标记 `active`。正式稳健防守与趋势进攻只使用已激活版本，不读取正在迭代的候选版本。
