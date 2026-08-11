# 公告情报链路 Agent 交接手册

更新日期：2026-07-26

## 1. 交接目标

本项目已把 Tushare A 股公告目录、PDF/版面解析、严格事件 Schema、证据定位、
确定性校验、观察因子、Dashboard 和 ECS 调度拆成独立链路。历史目录与 PDF
物理回填会在 ECS 后台从账本续跑，不需要下一个 Agent 手工重启或从头执行。
下一位 Agent 只需要接入可用的大模型并完成 240 份冻结基准、Gold、质量门禁和
Champion；不得重做数据基础设施，也不得让语义故障阻断两套纸面交易策略。

当前没有生产 Champion。默认模型已按用户要求固定为 DeepSeek V4：

- `candidate-a`: `deepseek-v4-pro`
- `candidate-b`: `deepseek-v4-flash`
- API: `https://api.deepseek.com/chat/completions`
- 当前阻塞：现有 DeepSeek key 真实请求返回 HTTP 402
  `Insufficient Balance`

豆包不参与 Candidate、Gold、Champion 或正式语义抽取。

## 2. 重要路径

| 内容 | 路径 |
| --- | --- |
| 工作区 | `/opt/stock-analyze/app` |
| Python | `/opt/stock-analyze/venv/bin/python` |
| 主数据库 | `data/shared/intelligence/intelligence.sqlite3` |
| 冻结基准 | `data/shared/intelligence/benchmarks/announcement-v1/` |
| 私有 OSS | `oss://stock-analyze/announcements/` |
| 语义配置 | `configs/intelligence_semantic.yaml` |
| taxonomy | `configs/intelligence_event_taxonomy_v1.json` |
| prompt | `stock_analyze/intelligence/semantic/prompts/announcement_event_v2.md` |
| Provider 接口 | `stock_analyze/intelligence/semantic/provider.py` |
| Candidate/Gold/门禁 | `stock_analyze/intelligence/semantic/benchmark.py` |
| 生产流水线 | `stock_analyze/intelligence/semantic/pipeline.py` |
| 运维手册 | `docs/announcement-intelligence-runbook.md` |

生产秘密只放在 `/etc/stock-analyze/secrets.env` 及其引用的 root-only 文件中。
禁止把 key 写进 Git、日志、候选输出或交接文档。

## 3. 已完成的非 LLM 链路

- Tushare `anns_d` 目录使用独立回补账本和分区/逐证券闭包，不共享或回退实时游标。
- `200*.SZ`、`900*.SH` 在入口、链接、事件、因子和 Dashboard 边界重复排除。
- PDF 按内容哈希写私有 OSS；ECS 仅保留 lineage、页级文本、表格、坐标和状态。
- 解析失败、OCR 失败、模型失败和 `no_event` 是四种不同状态。
- 15 类公告事件使用严格 JSON Schema；数字由程序重新计算。
- quote 只有在指定 chunk 内唯一逐字命中时才自动重算偏移；其余进入隔离或仲裁。
- 21 个公告因子保持 `observing`，不会在证据期前进入正式下单。
- Dashboard 情报资源独立懒加载，支持 canonical/no-event/quarantine/failed 下钻。
- 30 分钟增量、20:30 对账和每 30 分钟的历史 PDF/解析续跑均由 systemd 管理。
  历史批次下载 100 份，50 份解析拆成 5 个各 10 份的进程；日常对账采用相同
  的解析分段，最多等锁 45 分钟。历史批次遇到锁占用以退出码 75 正常跳过，
  不触发假失败通知。生产解析固定单 worker，未经扩容不得提高。
- 账号欠费、权限拒绝或 provider 重试耗尽会在第一篇后熔断本批次，不会污染数百行。

最终线上实测数字以
`reports/intelligence/production_acceptance_<timestamp>.json` 为准。
该报告会明确区分“非 LLM 基础设施验收完成”和“历史物理回填尚在后台运行”。

## 4. 接入方式 A：OpenAI-compatible API

这是推荐路径，也是唯一能直接成为生产 Champion 的路径。

1. 把新 key 写入独立 root-only 文件，例如
   `/etc/stock-analyze/deepseek_apikey`，权限设为 `0600`。
2. 只更新 `/etc/stock-analyze/secrets.env`：

```text
INTELLIGENCE_LLM_API_KEY_FILE=/etc/stock-analyze/deepseek_apikey
INTELLIGENCE_LLM_MODEL_CANDIDATE_A=deepseek-v4-pro
INTELLIGENCE_LLM_MODEL_CANDIDATE_B=deepseek-v4-flash
```

3. 若换成其他 OpenAI-compatible 服务，修改
   `configs/intelligence_semantic.yaml` 的 `semantic.provider.base_url`，
   同时保留 `response_format=json_object` 和本地 Schema 校验。
4. 模型、URL、温度、输出上限、prompt、Schema、taxonomy、parser 或证据对齐版本
   变化都会改变 Candidate 身份哈希。旧输出必须归档，不能混跑续接。
5. 先执行 `--limit 1`，确认没有 401/402/403、JSON 或 Schema 错误，再跑 240 份。

不得关闭本地 Schema 校验，不得启用未经实测的 provider 侧 JSON Schema，也不得把
模型自由文本摘要直接转成事件或交易信号。

## 5. 接入方式 B：自定义 Provider

`materialize_candidate_outputs` 接受注入式 Provider。适配器必须提供：

```python
identity: SemanticProviderIdentity

def extract(
    bundle: SemanticInputBundle,
    *,
    response_schema: Mapping[str, object],
) -> SemanticProviderResponse:
    ...
```

参考实现和契约：

- `OpenAICompatibleSemanticProvider`
- `SemanticProviderTest.test_from_config_reads_model_and_key_file_from_environment`
- `CandidateMaterializationTest.test_materialization_is_bounded_resumable_and_identity_pinned`

适配器必须满足：

- `identity.provider`、`identity.model` 与 Candidate 配置完全一致；
- 只把 `bundle` 中的白名单字段发给模型；
- 返回完整 `SemanticProviderResponse`，保留 input/output hash 和用量；
- 网络、限流、额度和权限错误使用稳定 `SemanticProviderError`；
- 仍由项目代码执行 Schema、证据、实体、数字、日期和 taxonomy 校验。

生产 Champion 必须对应一个可在 ECS 定时调用的稳定 Provider。一次性的 Coding Plan
不是生产 Provider。

## 6. 使用 Coding Plan 辅助分析

Coding Plan 可以：

- 阅读冻结 manifest 对应的 parsed chunks；
- 按 prompt v2 和 Schema 生成候选事件；
- 复核 `adjudication_queue.jsonl`；
- 为 A/B 都错误的文档编写 `choice=adjudicated` 的严格结构化修订；
- 检查证据 quote 是否逐字存在于指定 chunk。

Coding Plan 不可以：

- 直接编辑 Gold、registry 或 benchmark 报告绕过命令校验；
- 手工伪造 Candidate 身份、用量、hash 或通过指标；
- 把一次性分析结果晋升成无法由 ECS 重复调用的 Champion；
- 读取或修改两套正式策略的私有运行数据。

建议由 Coding Plan 实现一个符合第 5 节协议的适配器，再由
`materialize_candidate_outputs` 统一写候选输出；不要直接拼 JSONL。

## 7. Candidate、Gold 与 Champion 命令

在 `/opt/stock-analyze/app` 执行：

```bash
/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  intelligence-semantic-materialize --repo-root . \
  --benchmark announcement-v1 --provider-config candidate-a --limit 240

/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  intelligence-semantic-materialize --repo-root . \
  --benchmark announcement-v1 --provider-config candidate-b --limit 240

/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  intelligence-semantic-draft-gold --repo-root . \
  --benchmark announcement-v1
```

复核 `adjudication_queue.jsonl` 后写 `decisions.jsonl`，再执行：

```bash
/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  intelligence-semantic-finalize-gold --repo-root . \
  --benchmark announcement-v1 \
  --decisions data/shared/intelligence/benchmarks/announcement-v1/decisions.jsonl

/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  intelligence-semantic-benchmark --repo-root . \
  --benchmark announcement-v1 --provider-config candidate-a

/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  intelligence-semantic-benchmark --repo-root . \
  --benchmark announcement-v1 --provider-config candidate-b
```

只可晋升全部通过以下门槛的 run：

| 指标 | 门槛 |
| --- | ---: |
| Schema validity | 100% |
| Event precision | >= 90% |
| Event recall | >= 85% |
| Evidence grounding | >= 98% |
| Entity accuracy | >= 99.5% |
| Numeric exact match | >= 98% |
| No-event false-negative | <= 10% |

晋升命令：

```bash
/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  intelligence-semantic-promote --repo-root . \
  --benchmark-run-id <passing-run-id>
```

若两个 Candidate 都未通过，保留失败报告并改进 prompt/适配器；禁止降低门槛。

## 8. 上线后的验证

```bash
systemctl start stock-analyze-intelligence-reconcile.service
systemctl status --no-pager stock-analyze-intelligence-reconcile.service
systemctl list-timers 'stock-analyze-intelligence*' --all

/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  intelligence-enrich --repo-root /opt/stock-analyze/app \
  --limit 100 --stages semantic validate
```

确认：

- canonical 非空事实全部有证据；
- quarantine 不进入事件、因子或模型矩阵；
- Candidate/Champion 版本与当前配置哈希一致；
- provider 故障只让语义分支 `partial`/`unavailable`，纸面交易继续；
- Dashboard 能查看模型版本、处理状态、隔离原因和证据原文；
- 飞书仍只有每日汇总中的一行情报状态。

## 9. 观察与正式消费

Champion 发布只代表抽取质量通过，不代表公告因子立即参与下单。继续运行：

```bash
for market in a_share cn_qdii_etf; do
  /opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
    intelligence-evaluate --repo-root /opt/stock-analyze/app \
    --market "$market"
done
```

至少累计 20 个交易日，并检查覆盖率、延迟、误报率、Rank IC、ICIR、衰减和消融。
门禁通过前保持 `observing`；任何正式消费都走既有模型迭代和影子验证流程。

## 10. 禁止事项

- 不删除 `intelligence.sqlite3`、回补账本或坏结果来“重置”进度。
- 不回退或共享 live cursor。
- 不抓取同花顺、东方财富或富途私有接口作为生产主源。
- 不把 B 股、自由文本摘要或模型自报 confidence/sentiment 送入正式特征。
- 不直接修改 Gold/Champion registry，不绕过冻结 benchmark。
- 不因语义链路未完成而修改两套正式策略或竞赛公平基线。

## 11. 当前生产快照

截至 2026-07-26 晚间：

- 部署版本：
  `bacda91c104aa56988b51e3aab628f2cdd40dd15-worktree.9517b470f0a1`
- 本地 Python `1363` 项、ECS Python `723` 项、前端 `50` 项测试通过。
- 公告目录 `476,962` 条、规则事件 `1,790` 条；B 股链接与负 ingestion delay
  均为 0。
- PDF 与 parsed 产物持续写入 `oss://stock-analyze/announcements/`；已用真实
  数据库 URI 验证对象存在。当前 PDF `2,891` 份、parsed `1,062` 份，ECS
  不保存 PDF 副本。
- 元数据年份已覆盖 2000–2010、2023–2026；2011–2022 尚在后台闭包回填。
  `stock-analyze-intelligence-backfill.service` 为手工创建的一次性长服务，
  当前已经启动，完成前不得再启动第二个实例。
- 21 个因子全部保持 `observing`。A 股最新评估有 21 个非零覆盖因子；跨境
  ETF 有 21 个因子记录，其中 15 个为非零覆盖。
- `semantic_runs=0`、`event_candidates=0`、Champion 为空。这是用户主动推迟
  大模型分析后的正确状态，不是故障。
- ECS 数据目录约 1.1 GiB，系统盘剩余约 21 GiB；当前无需扩容。
- Dashboard 情报资源是独立懒加载接口。大回填并发写入时冷请求可能需要约
  21 秒，15 秒缓存命中后的热请求约 63 毫秒；预测接口冷/热约
  1.6 秒/10 毫秒。
- 周末模型迭代回归已在线验证：A20-V005 自动使用周五 `2026-07-24`
  快照，Q5-V004 使用当前可用快照；模型服务与整套 timer 健康检查均通过。

计数会随后台任务变化，机器可读验收以
`reports/intelligence/production_acceptance_latest.json` 为准。本次不可变快照为
`reports/intelligence/production_acceptance_20260726T121917Z.json`。

## 12. 接手检查单

1. 先读本手册和 `docs/announcement-intelligence-runbook.md`，不要重建 SQLite、
   OSS、taxonomy、prompt 或 systemd。
2. 运行 `systemctl list-timers 'stock-analyze-intelligence*' --all` 和
   `systemctl status stock-analyze-intelligence-backfill.service`，确认后台任务
   仍在续跑；不要因年份尚未全部出现而清库。
3. 选择 OpenAI-compatible API 或实现第 5 节 Provider 接口。先用 1 份文档验证
   身份、Schema、证据和错误分类，再跑 240 份。
4. 依次完成 Candidate A/B、draft Gold、分歧仲裁、finalize Gold、冻结 benchmark
   与质量门禁；所有命令见第 7 节。
5. 只有门禁全过才运行 promote。晋级后先观察 20 个交易日，不能直接修改正式
   策略权重或竞争基线。
6. 最终向用户报告 Provider、模型、Candidate hash、benchmark run id、各项门槛、
   Champion 版本、失败样本与回滚方式；绝不输出密钥。
