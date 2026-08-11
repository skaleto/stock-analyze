# 本地 Coding Plan 公告语义回填交接

**当前验收候选契约：** `a-share-announcement-mentions-v20` /
`semantic-mentions-v15` / `announcement-mentions-v1-lite` /
`cn-announcement-taxonomy-v9` / `mention-compiler-v1`。

`v1-v19` 是质量纠偏过程中的冻结试验版本：有的通过结构门但未通过人工
语义或未见样本泛化门，不能作为新任务输入。后续执行器只能
消费作业目录内冻结的 profile/prompt/schema/taxonomy，
不得自行回退版本或拼接旧结果。

旧 `semantic-extract-v5`、full-event 输出和按批次生成 `extract_batch*.py` 的做法已经停用。旧说明及产物只允许在 archive 中用于审计，不得作为新任务输入。

## 给 Coding Plan 的提示词

```text
你是一个受约束的公告事件 mention 抽取执行器，不是代码开发者，也不是股票分析师。

工作目录中的每个冻结作业都包含 job.json、input.jsonl、prompt.md、schema.json、taxonomy.json。你必须逐篇、独立地处理文档，严格执行 prompt.md，并只输出符合 schema.json 的 JSON。你的工作仅限从给定 chunks 中抽取：event_type、原文主体名、原文事实、原文日期、原文状态，以及每个字段对应的逐字证据。

禁止事项：
1. 不生成或修改 Python/shell 脚本，不改 prompt/schema/taxonomy/job/input。
2. 不访问互联网，不读取作业目录之外的语料，不调用数据库，不直接导入 ECS。
3. 不推断实体 ID、生命周期、标准日期、金额、币种、单位、重要性、涨跌、收益或交易建议。
4. 不因为可选字段缺失而把明确事件写成 no_event。
5. 不复用上一文档上下文；每篇文档都是独立任务。
6. 不输出 Markdown、解释或 schema 以外字段。
7. 不把公告标题中的泛化标签当事实：例如“权益变动”不是具体 action；
   交易前后测算表也不等于已发生的股东动作。
8. 状态决定要求：已完成/投产事件使用 lifecycle-specific requirements，
   不得套用拟建事件的未来日期要求。
9. `payload.document.name` 可能只是证券简称。issuer 必须使用原文中的公司
   法定全称，主体证据只引用名称本身的最小连续子串，不得引用“证券简称”行、
   整个公告标题或董事会落款。其他主体同样使用 name-only 证据。
10. `subject_roles` 只能进入 `subjects`，`fact_names` 只能进入 `facts`。
    一致行动人合并披露时，把每个明确命名的持有人分别作为同一 mention 的
    holder subject；不得把 holder 拼成一个事实字段。
11. PDF 相邻 chunk 在词中间断开时，只能按原顺序逐字还原；禁止跨非相邻
    chunk 或跨不同表格行合成事实。

每条 evidence.quote 必须是对应 chunk 文本中的连续原文子串。信息不确定就省略该可选字段，但明确发生或计划发生的事件必须抽取。只有文档确实不含 taxonomy 列出的事件时才输出 mentions=[]，并给出简短 no_event_reason。

实际运行必须使用项目的本地 campaign runner，由 runner 启动独立 claude -p 调用、执行 schema/grounding/compiler 校验并逐篇 checkpoint。不要自行批量分类，不要把“零 quarantine”当成质量通过。
```

## 运行入口

```bash
./scripts/run-local-semantic-claude-campaign.sh \
  --job /absolute/path/to/frozen-job \
  --hours 10
```

runner 会：

- 每篇启动 fresh Claude Code；
- 禁用工具，只允许 JSON 输出；
- 逐篇写 checkpoint；
- quota/rate limit 时等待恢复；
- 输出 `run_report.json`、`campaign_state.json` 和失败时的
  `quarantine.jsonl`；
- 不自动导入 ECS。

## 导入前硬门槛

- schema、input/hash、evidence grounding：100% 通过；
- compiler rejected/dropped：0；
- accepted event 严重语义错误：0；
- 主事件误分类、把测算值当已发生事实、主体错绑：0；
- `shareholder_change.action` 必须是明确动作词，泛化“权益变动”和数字：0；
- 完成态项目漏报、电力产能单位丢失：0；
- 强信号 `no_event`：100% 复核；
- 普通 `no_event`：抽查 `max(5, 20%)`，漏报率不得超过 5%；
- quarantine + failed 不得超过 10%；
- 任一严重错误或漏报超标：整批隔离并重跑，不允许部分导入。

此外，验收单位始终是冻结的 10 篇批次：哪怕其中 9 篇通过，只要 1 篇存在
主体错绑、事件误分类、必需事实漏报、证据合成或编译损失，整个批次都不能
导入。修复样本通过只证明缺陷被修复，必须再跑一批包含未见样本的 10 篇
泛化集，且自动门和逐篇人工语义门均为 10/10，才允许扩大历史回填。

自动门通过不等于验收通过。每个 10 篇批次必须逐篇核对“公告实际主事件、
主体、生命周期、事实经济含义、证据范围”；只有人工审计也为 10/10 才可
导入。提示词、taxonomy、compiler 任一变化都必须生成新 profile/prompt
版本并重跑 canary，禁止拿旧输出按新规则重新解释。

完整设计与执行步骤见：

- `docs/superpowers/specs/2026-08-08-quality-gated-semantic-backfill-design.md`
- `docs/superpowers/plans/2026-08-08-quality-gated-semantic-backfill.md`
