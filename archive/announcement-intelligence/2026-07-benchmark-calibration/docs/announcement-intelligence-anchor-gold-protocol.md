# Anchor Gold 盲标协议与评估规格

更新日期：2026-07-27

适用对象：公告智能抽取的独立标注者（不同模型族或人工）。

前置材料：

- `docs/announcement-intelligence-claude-correction-handoff.md`（P1 章节）
- `docs/announcement-intelligence-product-grading.md`（产物分级与 `event_family`
  弱标签来源）

本文落实纠偏文档 P1.2 / P1.3 / P1.4：定义 Anchor Gold 的盲标规则、Gold 记录
格式和拆分指标规格。实际标注由**独立于 Candidate 生成过程的标注者**完成；
Candidate 由 `claude-fable-5` 生成，因此 Claude 自身**不得**担任 Anchor Gold
标注者或仲裁者（自产自评，见纠偏 §1.2 / §3.2）。

## 1. 独立性要求（P1.2）

- 标注者**不得**查看 Candidate 输出、Silver v0 (`gold.jsonl`) 或
  `manifest.event_family`。只看公告正文（chunks / tables）和实体白名单。
- 至少**两名独立标注者**盲标同一份文档；分歧由**第三名仲裁者**裁决，仲裁者
  只看分歧项。
- 标注者可以是不同模型族（如 DeepSeek、GPT、Gemini）或人工组合，但不能是
  生成 Candidate 的同一个模型。
- 标注者身份（模型 / 标注者代号）记入 Gold 记录，不可匿名。

## 2. 标注者收到的盲材料

由 `scripts/export-anchor-workbench.py` 从 `anchor_sample.jsonl` 的 80 篇生成，
每篇一个独立目录，**只含**：

- `document.json`：标题、ts_code、name、published_at、source_url（无 event_family）。
- `chunks.json`：页码、chunk_id、section、bbox、正文文本。
- `tables.json`：表格结构与单元格（若存在）。
- `entity_whitelist.json`：允许的 issuer 实体（ts_code + name + role=issuer）。
- `revision_context.json`：修订链上下文（关联文档的标题/时间/关系，不含事件族）。
- `schema.json`：严格 JSON Schema（Draft 2020-12），与 Candidate 用的同一份。
- `taxonomy.json`：15 类事件 taxonomy（事件类型 + lifecycle + fact 定义）。
- `protocol.md`：本协议的标注指南摘要。

**不含**：Candidate A/B 的任何输出、Silver v0、`manifest.event_family`、
benchmark 报告。`taxonomy_candidates`（router 基于正文给的事件类型候选）**不**
提供给 Anchor Gold 标注者，避免引入与 Candidate 同源的路由信号。

## 3. Gold 记录格式（P1.3）

每篇文档产出一个 Gold 记录，JSON 格式，与 Silver v0 同 Schema 但增加标注溯源：

```json
{
  "document_id": 431611,
  "artifact_hash": "<sha256>",
  "annotator": "deepseek-v4-pro",
  "adjudicated_at": "2026-07-28T00:00:00+00:00",
  "events": [
    {
      "event_type": "buyback",
      "lifecycle": "completed",
      "subjects": [{"entity_id": "000001", "role": "issuer", "evidence_ids": ["e1"]}],
      "facts": [{"name": "amount", "numeric_value": 100000000, "unit": "CNY", "currency": "CNY", "period": null, "evidence_ids": ["e1"]}],
      "effective_dates": [],
      "conditions": [],
      "conflicts": []
    }
  ],
  "evidence_spans": [
    {"evidence_id": "e1", "page_number": 1, "chunk_id": "chunk-1", "start": 3, "end": 7, "quote": "回购金额"}
  ],
  "no_event_reason": null,
  "annotation_basis": "正文第1页明确记载回购金额1亿元，已完成",
  "adjudication_reason": null
}
```

字段约束：

- `annotator`：标注者身份（模型名或人工代号）。两名标注者用不同值。
- `adjudicated_at`：标注或仲裁的 ISO 时间。
- `annotation_basis`：**必填**，标注者对该事件判断的一句话依据（引用正文事实，
  非模型自报 confidence）。
- `adjudication_reason`：仅仲裁记录填写，说明分歧与裁决理由。
- `annotation_hash`：由 `canonical_json_hash` 对除该字段外的记录计算，程序校验。
- `evidence_spans`：每个 `evidence_id` 必须在指定 chunk 内有唯一逐字命中
  （`exact-quote-unique-v1`）；`start`/`end` 可由 `relocate_evidence_offsets`
  自动重算，跨 chunk / 改写 / 多重命中不修复，进入仲裁。
- 数字字段（amount 等）由程序重新计算，不采信模型自报的字符串。

## 4. 派生文档标注政策（P0.5）

法律意见书、独立财务顾问报告、核查意见等派生文档不采用“全部 no_event”或
“强制有事件”两个极端：

- 文件含**新的金额、日期、对象、状态变化或否决信息**：抽取新事实或修订关系
  （`revises` / `cancels` / `completes` / `supersedes`）。
- 文件只**重复已有事项并确认合规**：作为 corroborating evidence 或
  `duplicate` / `revision` 关系，不重复创造新基础事件。
- 文件只**讨论程序和法律意见，无法确认实际事件已发生**：标 `no_event` 或隔离，
  不因标题含“法律意见”而强制制造事件。

## 5. 拆分指标规格（P1.4）

评估时把当前 7 项门槛拆成 5 个可独立诊断的子问题，每个有明确分母与失败样本：

| 子问题 | 测什么 | 分母 | 失败样本记录 |
| --- | --- | --- | --- |
| quote_in_text | quote 是否逐字存在于指定 chunk | 每条 evidence | doc_id, evidence_id, quote, chunk_id |
| quote_supports_fact | quote 是否**语义支持**对应事实（非仅字符串存在） | 每条带事实的 evidence | doc_id, fact_name, quote, 判定理由 |
| event_identity | 事件是否识别正确（类型 + lifecycle） | 每个 gold 事件 | doc_id, gold_event, pred_event |
| entity_temporal_numeric | 主体、日期、金额、单位、币种是否正确 | 每个 gold 事实 | doc_id, fact, gold vs pred 各字段 |
| no_event_false_negative | no_event 文档是否被错误漏报（判出事件） | 每个 no_event gold | doc_id, 误报事件 |

`quote_in_text` 是机械检查（`text.find(quote)`），但 `quote_supports_fact` 必须
人工或独立模型判定：一个 quote 可能逐字存在却不支持对应事实（如引用了金额但
金额属于另一事项）。这是纠偏 §3.2 “`text.find(quote)` 只能证明字符串存在”
的落实。

## 6. 受约束的事件匹配（P1.5）

事件匹配**不能只看事件类型和 lifecycle**，必须同时核对：

1. 事件类型 (`event_type`) 一致；
2. lifecycle 一致或兼容（如 `completed` vs `announced` 需标记为部分匹配）；
3. **主体** (`subjects.entity_id`) 一致；
4. **关键事实** (`facts.name` + `numeric_value` + `unit` + `currency`) 在容差内一致；
5. **时间** (`effective_dates`) 一致或重叠。

只有 1-5 全部匹配才算 true positive；只匹配类型与 lifecycle 算 partial match，
单独统计，不计入 precision/recall 分子。避免“按事件族宽松对齐”导致虚高。

## 7. 报告与门槛重认（P1.6 / P1.7）

报告输出：

- 总体指标：5 个子问题的通过率 + 95% 置信区间（Wilson 区间）。
- 分事件族指标：15 族各自的 precision / recall / F1。
- 困难文档指标：法律意见书 / OCR / 修订链 / A/B 分歧子集的指标。
- 失败样本清单：每个失败子问题的 doc_id 与具体字段。

发布门槛基于 Anchor Gold 的真实错误成本重新确认，**不得**为了让现有 Candidate
通过而反向调低。若现有 7 项门槛（schema=1.0 / precision≥0.90 / recall≥0.85 /
evidence≥0.98 / entity≥0.995 / numeric≥0.98 / no_event_fn≤0.10）在 Anchor Gold
上被证明不合理，由标注者与操作者共同重定，并记录重定理由。

## 8. 当前状态与卡点

- ✅ P1.1：80 篇锚样本已选（`anchor_sample.jsonl`），分层覆盖 15 族 + no_event
  + 长文 + 表格 + OCR + 法律意见书(24) + A/B 分歧(42)。
- ✅ P1.2-1.4：本协议与规格已定义；盲材料导出工具
  (`scripts/export-anchor-workbench.py`) 已实现并产出 80 篇盲材料
  （`anchor_workbench/<doc_id>/`，泄漏检查 0 命中）。
- ⏸ P1.2 标注 / P1.3 内容 / P1.7 门槛重认：**卡在独立标注者**。Candidate 是
  Claude，Claude 不能自标。需要：
  - 一个可用的独立模型（DeepSeek 402 未确认解除；GPT/Gemini 需 key），**且**
  - 第二个独立来源（另一模型族或人工），加仲裁。
  - 两个独立来源到位前，Anchor Gold 无法生成，benchmark 无法重算。

操作者决策点：提供至少两个独立标注来源（或明确接受单模型 + 人工抽检的降级方案），
本协议即可启动执行。
