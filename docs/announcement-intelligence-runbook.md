# 公告情报生产运维手册

更新日期：2026-08-06

本系统只保留一条生产主线：

```text
Tushare/iFinD/官方政策
  -> 元数据目录
  -> PDF 私有 OSS
  -> 本地解析与 OCR
  -> 通用语义抽取任务
  -> 确定性校验与隔离
  -> 结构化事件
  -> event-lite-v1 因子
  -> 增量效果评估
  -> 达标后才允许进入模型迭代
```

原始语料和大模型文字不能直接触发买卖。所有结果先经过 Schema、实体白名单、
逐字证据、时间和数值校验；`quarantined` 只用于排错，不能进入事件、因子或模型。

## 每日任务

| 时间（上海） | ECS 任务 | 结果 |
| --- | --- | --- |
| 工作日 09:30/12:30/16:30/21:45/23:30 | `stock-analyze-intelligence.timer` | Tushare 公告增量、官方政策和规则事件；只刷新轻量语义状态 |
| 每天 00:30 | `stock-analyze-intelligence-reconcile.timer` | 近两日 PDF 下载、文本优先解析、必要 OCR 和失败重试 |
| 约每 20 分钟，17:45-21:30 正式链路窗口暂停 | `stock-analyze-intelligence-artifact-backfill.timer` | 历史 PDF 和解析队列有界续跑；资源不足或正式任务运行时自动延期 |
| 工作日 22:10 | `stock-analyze-intelligence-semantic.timer` | DeepSeek 生产批次，默认最多 3 篇，校验、入库并刷新事件因子 |
| 周日 03:15 | `stock-analyze-intelligence-quality.timer` | 全库质量、覆盖和一致性报告；与下载解析共享互斥锁 |
| 周六 08:30 | `stock-analyze-ifind-source-audit.timer` | iFinD 全市场交叉核验和有额度保护的缺口补充 |
| 日常研究流水线 | `stock-analyze-research.service` | 两个市场的 `intelligence-evaluate`、预测与模型影响报告 |

历史公告元数据的 `intelligence-backfill` 仍是人工可续跑任务，不创建 timer。

## 数据源

- Tushare `anns_d` 是公告目录主源，工作日做增量抓取。
- iFinD 是周度交叉核验和缺口补充源，不承担全量主抓取，受每月公告额度保护。
- 国务院和国家发改委官方站点提供政策文件。
- CNINFO 链接是 PDF 法定披露原文地址。
- 生产原文只写入杭州同地域内网 Bucket `stock-analyze-hz` 的
  `announcements/` 前缀；旧 Bucket 不再作为读取回退源。
- 尚未实现的交易所、基金公司、扩展宏观和商业新闻接口保留配置声明，但默认关闭，
  不再每天制造“不可用”运行记录。

线上与 Dashboard 默认使用 `observed` 口径，信息只有在 `first_seen_at` 后可见。
`research` 可在
`historical_cutoff=2026-07-17T23:59:59+08:00` 之前使用保守重建时间；该时间之后
的迟到文件不能倒灌历史 OOS。

## 生产语义执行器

生产配置固定安装到：

```text
/etc/stock-analyze/intelligence-semantic-executor.yaml
```

该文件不含 API Key，只声明 OpenAI-compatible 接口、模型和预算。密钥路径由
`/etc/stock-analyze/secrets.env` 的 `INTELLIGENCE_LLM_API_KEY_FILE` 提供。
部署版本默认使用 DeepSeek；缺少或为空时语义服务明确失败并触发流水线告警，不再
静默变成“只排队”。

秘密预检只打印 `configured` 或 `missing`：

```bash
sudo bash -c '
set -a; . /etc/stock-analyze/secrets.env; set +a
for name in TUSHARE_TOKEN INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE \
  INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE INTELLIGENCE_LLM_API_KEY_FILE; do
  value=${!name:-}; state=missing
  if [[ "$name" == *_FILE ]]; then
    [[ -n "$value" && -r "$value" && -s "$value" ]] && state=configured
  else
    [[ -n "$value" ]] && state=configured
  fi
  printf "%s=%s\n" "$name" "$state"
done
'
```

生产命令：

```bash
python -m stock_analyze.cli intelligence-semantic-daily \
  --repo-root /opt/stock-analyze/app \
  --profile a-share-announcement-mentions-v1 \
  --limit 3 \
  --max-input-characters 40000 \
  --executor-config /etc/stock-analyze/intelligence-semantic-executor.yaml
```

Prompt、Schema、taxonomy、输入和输出契约与执行器解耦。需要临时改用 Codex、
Claude 或其他 Coding Plan 时，只生成相同任务并导入相同输出，不修改下游：

```bash
python -m stock_analyze.cli intelligence-semantic-prepare \
  --repo-root . --profile a-share-announcement-mentions-v1 --limit 20
python -m stock_analyze.cli intelligence-semantic-import \
  --repo-root . --job <job_id>
```

生产主 CLI 只保留以下五个统一契约命令：

- `intelligence-semantic-prepare`
- `intelligence-semantic-run`
- `intelligence-semantic-import`
- `intelligence-semantic-job-status`
- `intelligence-semantic-daily`

旧的多模型投票和独立晋升状态机已经退出代码主线。执行器质量通过同一任务契约上的
小规模分层抽检和漂移复查验证。

当前生产 profile 是 `a-share-announcement-mentions-v1`：大模型按
`semantic-mentions-v1` 只抽取带逐字证据的主体、事实、日期和状态 mention；本地
`mention-compiler-v1` 再依据 taxonomy v4 确定性编译成 canonical event。模型不能
自行补齐缺失事实或直接构造交易信号。逐字引用、实体、类型和必要事实任一校验失败就
隔离；高风险标题被回答为 `no_event` 时也进入复查，不静默接受。

DeepSeek 生产批次当前按每日 3 篇、25 万输入 token 封顶。该保守阈值来自真实小批
验收：在输出质量稳定前先控制成本与隔离队列规模；通过率和分层抽检达到门槛后再逐步
扩大，不以积压量倒逼无约束放量。

API 直连结果会在写入 `output.jsonl` 前做完整本地校验。证据不存在或不唯一时只允许
一次有界纠正；仍失败则只在 `semantic_runs` 保留 `failed_terminal` 错误码，不保存
无效模型输出，也不会在同一 Prompt/Schema/taxonomy/解析版本下每天重复消耗 token。
契约或解析产物升级后可形成新任务重新处理。外部执行器写回的文件仍在导入边界严格
校验，失败进入隔离区。

## 存储与模型供给

| 层 | 存储 | 含义 |
| --- | --- | --- |
| 目录 | SQLite `documents` | 公告、来源、时间和证券映射 |
| 原文 | 私有 OSS `announcements/pdf/` | PDF 原文；ECS 不保留重复 PDF |
| 解析 | SQLite chunks/tables + OSS 解析产物 | 带页码的正文、表格和 OCR |
| 语义 | `semantic_runs`、`event_candidates`、`events` | 执行血缘、隔离候选和可信结构化事件 |
| 因子 | 研究特征快照中的 `event-lite-v1` 八因子 | 5/20 日事件强度、相关性、确定性、修订风险、覆盖率等 |
| 模型影响 | `reports/research/model_incremental_effect_*.json` | 同切分、同随机种子的 Base 与 Base+Event 对比 |

当前公告因子状态是 `observing`，因此**未自动入模**。语义抽取成功只代表事实可用，
不代表有预测收益。至少具备 20 个活跃交易日和 100 行活跃样本后，系统才计算
覆盖率、IC、稳定性和增量效果；门槛未通过时保持观察。状态提升到
`model_iteration` 或 `active` 后，训练矩阵才会读取这些因子。

这条门禁回答三个不同问题：

1. 语料是否被正确抽取。
2. 事件是否能生成非空、无前视的因子。
3. Base+Event 是否相对 Base 带来可重复的模型影响。

只有第三项通过并经历影子/OOS 验证，才可能讨论真实回报贡献。

## 状态与排错

```bash
cd /opt/stock-analyze/app
python -m stock_analyze.cli intelligence-status --repo-root .
python -m stock_analyze.cli intelligence-semantic-status --repo-root .
python -m stock_analyze.cli intelligence-evaluate \
  --repo-root . --market a_share
python -m stock_analyze.cli intelligence-model-effect \
  --repo-root . --market a_share
```

人工补一小批 PDF/解析：

```bash
python -m stock_analyze.cli intelligence-enrich \
  --repo-root . --limit 20 --stages download parse
python -m stock_analyze.cli intelligence-reconcile \
  --repo-root . --lookback-days 2 --limit 10 --stages parse
```

`intelligence-enrich` 和 `intelligence-reconcile` 只处理目录、下载和解析，不接受
`route/semantic/validate` 阶段。生产语义统一走 `intelligence-semantic-daily`。

历史元数据续跑：

```bash
sudo systemctl reset-failed stock-analyze-intelligence-backfill.service
sudo systemctl start --no-block stock-analyze-intelligence-backfill.service
# 服务内部使用 --resume；CLI 返回 3 表示本批正常但仍有后续分区
```

PDF/解析优先读取 PDF 文本层，只有文本缺失或质量不足的页面才进入 OCR；不是所有
PDF 都走 OCR。1.6 GiB ECS 上固定单解析 worker、4 个相互隔离的 OSS 下载 client。
Phase A 每轮最多下载 180 篇、解析 75 篇；Phase B 最多下载 240 篇、解析 100 篇。
可用内存低于 512 MiB、磁盘低于 5 GiB、负载过高或单批达到 18 分钟会主动延期。
不要用提高解析并发换取短期速度；当前瓶颈是解析内存而不是 OSS 下载。
增量采集、近两日对账、历史回填、语义抽取和全库质量扫描共享同一后台资源锁；任务
可以等待或返回 `75` 延期，但不能在小内存 ECS 上并行争抢内存。

## 完整性与容量

```bash
cd /opt/stock-analyze/app
sqlite3 data/shared/intelligence/intelligence.sqlite3 "
PRAGMA integrity_check;
PRAGMA foreign_key_check;
"
du -sh data/shared/intelligence
df -h /opt/stock-analyze
ossutil du oss://stock-analyze-hz/announcements/
```

`PRAGMA integrity_check` 必须返回 `ok`，外键检查应为空。Dashboard 在磁盘使用率
达到 80% 时提示预警，达到 88% 时提示严重；历史回填仍以可用空间低于 5 GiB 为
硬暂停门槛。不得删除 SQLite 账本、语义血缘或失败记录来制造“已完成”。

## Dashboard 与通知

Dashboard 的“情报与模型影响”页面按四层展示：语料覆盖、语义事件、因子供给、
模型采用与增量效果。页面中的“最近执行器”只描述最近生产批次，不代表模型已采用。
采集与语义任务会刷新 `semantic_status_latest.json`；PDF 回填直接维护
`artifact_backfill_state.json`，Dashboard 合并这两份轻量状态与有界最近明细，
不在用户请求或每个回填批次中扫描完整公告库。全量质量扫描只在周日低峰执行。

飞书只发送每日整体摘要和需要行动的失败，不发送逐公告流水。巡检脚本必须同时检查
semantic timer 和 service；“timer active”不能替代最近一次服务结果、数据库产物和
模型影响报告。

质量抽检和漂移复查不属于每日生产依赖，也不能阻塞这条主线。
