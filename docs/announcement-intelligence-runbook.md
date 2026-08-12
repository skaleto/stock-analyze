# 公告情报生产运维手册

更新日期：2026-08-11

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
  --profile a-share-announcement-mentions-v27 \
  --limit 3 \
  --max-input-characters 40000 \
  --executor-config /etc/stock-analyze/intelligence-semantic-executor.yaml
```

Prompt、Schema、taxonomy、输入和输出契约与执行器解耦。需要临时改用 Codex、
Claude 或其他 Coding Plan 时，生成相同合同的分片任务，先验收再导入，不修改下游：

```bash
python -m stock_analyze.cli intelligence-semantic-prepare \
  --repo-root . \
  --profile a-share-announcement-mentions-v27 \
  --limit 100 \
  --max-input-characters 24000 \
  --executor-mode coding_plan \
  --provider claude \
  --model claude-fable-5 \
  --client-version claude-code-2.1.215
python -m stock_analyze.cli intelligence-semantic-coding-plan-collect \
  --repo-root . --job <job_id>
python -m stock_analyze.cli intelligence-semantic-import \
  --repo-root . --job <job_id>
```

任务自动按 25 篇切分，Claude 提示词见
`docs/claude-historical-semantic-backfill-prompt.md`。`collect` 只生成校验报告、
规范化输出和隔离清单，不写生产数据库；只有 Codex 验收后才执行 `import`。

生产主 CLI 保留以下六个统一契约命令：

- `intelligence-semantic-prepare`
- `intelligence-semantic-run`
- `intelligence-semantic-coding-plan-collect`
- `intelligence-semantic-import`
- `intelligence-semantic-job-status`
- `intelligence-semantic-daily`

旧的多模型投票和独立晋升状态机已经退出代码主线。执行器质量通过同一任务契约上的
小规模分层抽检和漂移复查验证。

当前生产 profile 是 `a-share-announcement-mentions-v27`：Document IR v1 先完整
冻结正文、表格、页码和实体关系，检索器再投影成不超过 24,000 字符的证据包；大模型按
`semantic-mentions-v17` 只抽取带逐字证据的主体、事实、日期和状态 mention；本地
`mention-compiler-v3-ir` 再依据 taxonomy v12 确定性编译成 canonical event。
`long_document`、`table_heavy` 只是难度标签，不再单独触发 LLM。投资者关系、定期
报告、法律意见和治理文件若没有明确当前事件信号，会在路由层直接结束。

模型不能自行补齐缺失事实或构造交易信号。有效 mention 可独立通过，失败的同文
mention 保留在 source output/quarantine 中，不再抹掉已验证事件；所有 mention 都
失败时仍整篇隔离。高信号事件标题第一次得到 `no_event` 后只允许一次有界复核，复核
仍无法证明事件时保持无事件或隔离，绝不由规则凭空创建 canonical event。

v27 在冻结的 80 篇独立参考集上通过上线门槛：Schema 80/80，有效事件精确率
46/46（100%），召回率 46/51（90.20%），证据 grounding 295/295（100%），实体
53/53（100%），已抽取数值 47/47（100%），31 篇无事件误报 0。参考集中仍有 30 个
数值没有被抽出，数值覆盖率为 61.04%；这表示丰富度仍可提升，不是已抽取数值有误。
验收是独立 DeepSeek 全量执行，不是 Candidate A/B 自一致性分数，也没有导入生产库。

DeepSeek 生产批次当前按每次定时执行最多 3 篇、25 万输入 token 封顶。80 篇验收共
消耗 863,505 token，平均每篇约 10,794 total token；真实消耗会随表格和长文变化。
正常情况下 systemd 每个工作日只调用一次，额外人工重跑会产生新的批次预算，因此
只能用于明确的 canary 或修复，不能拿积压量倒逼无约束放量。

2026-08-11 的最终生产批次 `sj-d6bed9a34296a8ff4b8f845c` 真实处理 3 篇公告：
3/3 执行、3/3 导入，生成 2 个股权融资事件和 1 个担保事件，零隔离、零失败；共
消耗 33,669 token。导入后生产库新增 17 条逐字证据、11 条结构化事实和 3 组事件
评分。上线 canary 先后暴露并修复了程序性股东名册误路由、完整 IR 证据在导入端不可见
以及单篇完整 IR 超过普通交换行上限三个边界；对应回归已进入测试集。历史失败行保留
原审计血缘，不通过删除或重置伪装成功。

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
| 语义 | `semantic_runs`、`event_candidates`、`events`、`event_evidence/event_facts/event_scores` | 执行血缘、逐字证据、结构化事实、评分和可信事件 |
| 因子 | 研究特征快照中的 `event-lite-v1` 八因子 | 5/20 日事件强度、相关性、确定性、修订风险、覆盖率等 |
| 模型影响 | `reports/research/model_incremental_effect_*.json` | 同切分、同随机种子的 Base 与 Base+Event 对比 |

当前公告因子状态是 `observing`，因此**未自动入模**。2026-08-11 最新研究快照的
语义覆盖率是 37.35%，尚低于 55% 门槛；多数细分事件因子活跃样本不足。暂时相对
积极的 `policy_industry_exposure_20d` 5 日 Rank IC 为 0.064、多空差约 0.91%，但
只有 12 个有效交易日，仍不足以晋升。语义抽取成功只代表事实可用，不代表有预测
收益。状态提升到 `model_iteration` 或 `active` 后，训练矩阵才会读取这些因子。

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
模型采用与增量效果。生产版本来源统一由
`configs/intelligence_semantic.yaml.production_extraction_profile` 提供，当前必须显示
v27；页面中的“最近执行器”只描述最近生产批次，不代表模型已采用。
采集与语义任务会刷新 `semantic_status_latest.json`；PDF 回填直接维护
`artifact_backfill_state.json`，Dashboard 合并这两份轻量状态与有界最近明细，
不在用户请求或每个回填批次中扫描完整公告库。全量质量扫描只在周日低峰执行。

飞书只发送每日整体摘要和需要行动的失败，不发送逐公告流水。巡检脚本必须同时检查
semantic timer 和 service；“timer active”不能替代最近一次服务结果、数据库产物和
模型影响报告。

质量抽检和漂移复查不属于每日生产依赖，也不能阻塞这条主线。

路由器会在调用执行器前排除制度、定期报告、普通法律意见、调研记录和程序性披露。
例如“回购股份事项前十名股东持股情况”只披露股东名册，不代表新的回购生命周期，
必须标记为 `procedural_disclosure/context_only`，不能消耗 LLM token 或形成事件因子。
