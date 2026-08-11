# Tushare 与 iFinD 双源完整性方案

## 结论

Tushare 是生产主源，iFinD 是校验和缺口补源。两边不会无差别重复落库：

| 数据域 | Tushare | iFinD | 合并规则 |
| --- | --- | --- | --- |
| A 股日线 | 主行情缓存 | `THS_HQ` 复核 | 主源缺日才补，原有值不覆盖 |
| 跨境 ETF 日线 | `fund_daily` 主缓存 | `THS_HQ` 复核 | 主源缺日才补，原有值不覆盖 |
| A 股公告 | `anns_d` 主目录 | `THS_ReportQuery` 补漏 | 标题、证券、日期归一后去重 |
| PDF 正文 | 巨潮官方链接按需下载 | 不持久化临时下载 URL | 继续走现有 OSS/解析链 |

系统不采集 B 股。公告比较会先移除发行人前缀，避免“公司名：公告标题”和
“公告标题”被误判为两条。未来同一 Tushare 公告即使 URL 查询参数顺序改变，
也复用 `(source, source_id)` 对应文档，不再制造新版本。

## 数据口径

- 行情：日频、不复权、原币。
- iFinD：成交量为股，成交额为元。
- Tushare A 股缓存：成交量换算为股后比较，成交额已为元。
- Tushare 基金日线：成交量由手换算为股，成交额由千元换算为元。
- 公告时点：`published_at`、`first_seen_at`、`effective_at` 分开保存；
  补录的历史公告不会伪装成当时已经可知。

## 调度与额度

1. 工作日行情流水线：校验当日实际候选范围并补行情缺口。
2. 工作日行情流水线：校验持仓、待执行订单涉及的公告并补缺口。
3. 每周六 08:30：对最近交易日做全市场公告审计并补缺口。
4. iFinD 失败不阻断 Tushare 主链；每周审计失败触发统一飞书流水线告警。

试用额度优先用于决策相关标的。全市场公告只做每周审计，不按日重复消耗。

## 存储与审计

- 主库：`data/shared/intelligence/intelligence.sqlite3`
- 运行证据：`source_audit_runs`
- 逐项差异：`source_audit_items`
- 最近报告：`reports/intelligence/source_audit_latest.json`
- 统一质量报告：`reports/intelligence/quality_latest.json`

状态含义：

- `matched`：两源同一条数据且值一致。
- `mismatch`：两源都有但关键字段不一致，需要人工排查口径。
- `primary_only`：只有 Tushare；保留主源记录。
- `secondary_only`：只有 iFinD；未启用补源时仅记录差异。
- `supplemented`：iFinD 缺口已补入，且带明确来源。

历史 URL 变体造成的旧重复记录暂不在线合并。它们可能已被 PDF、解析结果和
事件表引用，应在回填停止后用专门迁移脚本合并，而不是直接删除。

## 2026-07-27 生产验收

- A 股行情：800 个实际候选全部匹配，差异 0，缺口 0。
- 跨境 ETF 行情：70 个目录标的全部匹配，差异 0，缺口 0。
- 2026-07-24 公告：1,136 条两源匹配，1 条仅 Tushare，43 条仅 iFinD
  且已补入。
- iFinD 补源库：43 个文档、43 条证券关联；B 股与非标准代码残留均为 0。
- SQLite：schema v13，`quick_check=ok`。
- iFinD 试用额度验收后：行情 57,058 / 7,500,000，公告
  3,974 / 10,000。重复验收产生的消耗不代表正常月度消耗。

验收时历史元数据回填和 PDF 解析回填同时运行，1.2GB SQLite 上的全量质量
报告耗时明显增大，Dashboard 情报下钻接口可能超过 15 秒。双源审计本身成功；
回填结束后应再次测量接口延迟，再决定是否增加只读快照或汇总表。

## 运维命令

```bash
# 决策标的行情与公告
python3 -m stock_analyze.cli intelligence-source-audit \
  --repo-root . --datasets market announcement --supplement

# 最近交易日全市场公告
python3 -m stock_analyze.cli intelligence-source-audit \
  --repo-root . --datasets announcement \
  --announcement-scope full-market --supplement

# 查看统一数据质量
python3 -m stock_analyze.cli intelligence-status --repo-root .
```
