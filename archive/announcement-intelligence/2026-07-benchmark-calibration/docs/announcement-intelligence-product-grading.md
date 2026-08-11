# 公告智能抽取产物分级与来源说明

更新日期：2026-07-27

适用对象：接手公告语义抽取的执行 Agent。

前置材料：

- `docs/announcement-intelligence-claude-correction-handoff.md`（纠偏与继续执行交接）
- `docs/announcement-intelligence-agent-handoff.md`
- `docs/announcement-intelligence-runbook.md`

本文落实纠偏文档 P0.1 / P0.2 / P0.5：给现有产物重新分级、说明
`manifest.event_family` 的弱标签来源、并为法律意见书等派生文档补充标注政策。
不删除任何旧产物，分级通过本文与 benchmark 目录内的 `PRODUCT_GRADING.md`
元数据共同表达，保持原始 hash 与 lineage 可追溯。

## 1. 正确定位

全量生产抽取是主线；冻结样本、Anchor Gold 和 benchmark 是发布前的质量闸门。
两者不是二选一：benchmark 服务于生产，而不是替代生产。

当前所有产物都处于研发期，没有任何一项具备直接生产资格。

## 2. 产物分级

| 产物 | 新定位 | 路径 | 可用范围 |
| --- | --- | --- | --- |
| Candidate A/B 的 480 份输出 | Experimental Candidate | `benchmarks/announcement-v1/candidate_outputs/` | prompt 研究、错误分析 |
| 当前 `gold.jsonl` 240 行 | Silver v0 | `benchmarks/announcement-v1/gold.jsonl` | 弱监督、困难样本、研发参考 |
| 45 份带类别提示的重抽结果 | Disputed weak-label set | `benchmarks/announcement-v1/disputed/` | 盲审清单，不参与评分 |
| A/B benchmark 报告 | Self-consistency diagnostics | `reports/intelligence/` 与 `semantic_registry.json` | 比较两种 prompt 行为 |
| 未来独立标注样本 | Anchor Gold | 待建 | 发布质量评估 |
| 未来可重复运行的通过版本 | Champion | `semantic_registry.json` | 全量生产抽取 |

`semantic_registry.json` 当前 `champion: null`、`promotion_history: []`。两条
candidate 记录都是 `passed: false`，仅作自一致性诊断留存。

## 3. `manifest.event_family` 的来源与限制（P0.2）

`manifest.jsonl` 里的 `event_family` 字段是**标题关键词规则生成的弱标签**，
不是人工真值：

- 选样时由公告标题经关键词规则映射到 15 类事件族之一，或 `no_event`。
- 它只用于分层抽样（15 类各 12 份 + 60 份无事件）和 benchmark 报告的分族聚合展示。
- 冻结后随 manifest 一起进入 hash，但它的语义是“标题归类”，不是“事件真实存在”。

**禁止**：

- 作为 Gold 输入；
- 作为重抽时的目标事件族提示；
- 作为对外宣称准确率的分母锚；
- 把它和 Gold 一起做循环验证（“用同一标签引导模型，再用同一标签证明结果正确”）。

**允许**：

- 分层抽样选样、困难样本归类、benchmark 报告分族聚合展示。

**禁止泄漏检查（已完成）**：生产 bundle（`pipeline.py build_bundle` 构造的
`payload`）只含 `document / taxonomy_candidates / entity_whitelist / chunks /
tables / revision_context`，**不含 `event_family`**。`taxonomy_candidates` 来自
router 基于正文的路由决策，不是 manifest 弱标签。因此 manifest 标签只在选样、
分层和 stratum 校验时出现，从不进入抽取 prompt。唯一的泄漏发生在我对 45 篇
的离线重抽脚本里（故归为 Disputed），生产代码无泄漏。

## 4. 当前 `gold.jsonl` = Silver v0（P0.1）

- 来源：Claude 自身 candidate-a / candidate-b 的 A/B 共识 + 45 篇争议仲裁。
- annotator 分布（机器记录，非人工）：45 `adjudicated/claude-reextract`、
  41 `candidate-a+candidate-b/consensus`、138 `candidate-a`（启发式）、
  16 `candidate-b`。
- gold_hash：`504358509c90f42d58b27023158a47ba187144589f93138ba349a85b195b1c74`。
- stratum gate：0 失败（即 Silver v0 与 manifest 弱标签的事件族一致，但这只
  说明“按标题归类一致”，不说明“事件事实正确”）。

**不可用于**：

- 计算对外宣称的模型准确率（Gold 与 Candidate 同源，只能测自一致性）；
- 晋升 Champion；
- 批量写入 canonical 事件；
- 开启正式公告因子或改变两套正式策略。

**可用于**：

- 发现 Schema、prompt、taxonomy 的缺口；
- 建立错误类型库与困难样本库；
- 作为 prompt 开发集或弱监督数据；
- 作为人工 / 独立 Agent 建 Anchor Gold 时的候选参考（**标注者不可预先看到它**）。

文件与 hash 保持不变；来源与分级通过本文与 `PRODUCT_GRADING.md` 表达。

## 5. 45 篇争议弱标签集 = Disputed（P0.1 / §2.5）

- 来源：manifest 弱标签提示下，对 A/B 都判 `no_event` 但 manifest 有事件族的
  45 篇做了“给定目标事件族”的第三遍重抽。这构成循环验证，结果既不能算 Gold，
  也不能算 Candidate 成绩。
- 归档路径：`benchmarks/announcement-v1/disputed/`（45 份严格 Schema + 引用落地的
  JSON，附 manifest 指定族标注，供后续盲审）。
- 重新处理时**不得**提供目标事件族；应让模型仅依据正文判断。

### 5.1 法律意见书 / 独立财务顾问意见等派生文档标注政策（P0.5）

派生文档（法律意见书、独立财务顾问报告、核查意见等）不采用“全部 no_event”
或“强制有事件”两个极端，按以下判断：

- 文件包含**新的金额、日期、对象、状态变化或否决信息**：可抽取新事实或修订
  已有关系（`revises` / `cancels` / `completes` / `supersedes`）。
- 文件只**重复已有事项并确认合规**：作为 corroborating evidence 或
  `duplicate` / `revision` 关系，不重复创造一个新的基础事件。
- 文件只**讨论程序和法律意见，无法确认实际事件已经发生**：标 `no_event` 或
  隔离，不因为标题弱标签而强制制造事件。

该政策同时写入 P1 Anchor Gold 标注指南；标注者盲标，不得看 Candidate、Silver
或 manifest 类别。

## 6. benchmark 报告 = 自一致性诊断（P0.1）

两个 candidate 都失败 6/7 项门槛（仅 `schema_validity=1.0` 通过）：

| 指标 | candidate-a | candidate-b | 门槛 |
| --- | ---: | ---: | ---: |
| schema_validity | 1.00 | 1.00 | =1.0 |
| event_precision | 0.860 | 0.816 | ≥0.90 |
| event_recall | 0.814 | 0.509 | ≥0.85 |
| evidence_grounding | 0.863 | 0.384 | ≥0.98 |
| entity_accuracy | 0.731 | 0.355 | ≥0.995 |
| numeric_exact_match | 0.791 | 0.289 | ≥0.98 |
| no_event_false_negative_rate | 0.183 | 0.344 | ≤0.10 |

因为 Gold 与 Candidate 同源（都是 Claude），这些数字只能读作“两种 prompt
策略的自一致性与困难样本暴露”，不是真实准确率。门槛是研发期保守初值，不得
为了让现有 Candidate 通过而反向调低；在没有独立 Anchor Gold 前，也不应把
这些数字解释成可靠的生产质量结论。

## 7. `claude-subagent-file` = offline experiment（P0.4）

`FileBackedClaudeProvider` 是一次性离线产物：读取预算好的 candidate JSON，
不调用任何 LLM API，没有生产调度、重试、预算、幂等和 lineage 契约。

- 生产配置 `configs/intelligence_semantic.yaml` 的 `semantic.provider.kind`
  已从 `claude-subagent-file` 恢复为 `openai-compatible`（DeepSeek）。
- `semantic_runs=0`、`event_candidates=0`：生产语义分支从未用假 provider 身份
  写入任何行。这是用户主动推迟大模型分析后的正确状态。
- 要成为 Champion，必须实现正式 Provider factory（ECS 可定时调用、可重试、
  可追溯、原始输出写 OSS），见纠偏文档 P2。

## 8. `benchmark.py` int/str 修复与回归测试（P0.6）

`finalize_benchmark_gold` 的 consensus / queue / decision 三个查找字典必须按
**int** 键 document_id，因为 `_materialization_manifest` 返回 int 键、
`ordered_ids` 是 int。str 键版本对真实数字 id 会在 `queue_by_id[document_id]`
抛 `KeyError`。

- 修复已正式写入 worktree 源码（非 ECS 行号 hack），与 ECS 部署一致。
- 回归测试 `FinalizeBenchmarkGoldDocumentKeyTest
  .test_finalize_matches_consensus_by_int_document_id`：int 版本通过、str 版本
  失败（`KeyError`）。
- 临时 `.bak.prefix` 已删除；不保留 `.bak` / 行号式临时修复。

## 9. P0 完成状态

- ✅ 生产配置不再调用 `claude-subagent-file` runtime（`kind` 已回退）。
- ✅ `semantic_registry.json` 中 `champion: null`，无 Silver 写入 canonical
  （`semantic_runs=0`、`event_candidates=0`）。
- ✅ 产物来源与分级可从本文与 `PRODUCT_GRADING.md` 读出。
- ✅ 45 篇不再计为外部 Gold（Disputed，归档至 `disputed/`）。
- ✅ `manifest.event_family` 弱标签来源已记录，禁作 Gold 输入。
- ✅ `benchmark.py` int/str 修复有回归测试，无 `.bak`。

## 10. 后续（P1 / P2，不在 P0 内）

- **P1**：独立 Anchor Gold（首批 80 篇，盲标，覆盖 15 类、no_event、长文、表格、
  法律意见书、A/B 分歧样本）；指标拆开（quote 在原文 / quote 语义支持事实 /
  事件识别 / 主体日期金额单位币种 / no_event 漏报）；基于 Anchor Gold 错误成本
  重认发布门槛。
- **P2**：可持续生产 Provider（优先稳定 OpenAI-compatible API，或把 Claude
  包装成真正批处理 Provider）；1 / 20 / Anchor Gold 三阶段 canary；通过且可重复
  才晋 Champion；21 个公告因子观察 ≥20 交易日。
