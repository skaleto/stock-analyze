# Claude 历史公告语义抽取提示词

> 适用于已经由 `intelligence-semantic-prepare --executor-mode coding_plan`
> 生成的生产历史回填任务。不要用于冻结 Gold 资格评测。

把下面整段交给 Claude，只替换 `{JOB_DIR}`：

```text
你要执行一批 A 股历史公告的结构化语义抽取。你不是在设计抽取方案、制定 benchmark
或修改代码；你的唯一任务是严格执行任务包中已经冻结的合同，并把结果写回任务包。

任务目录：
{JOB_DIR}

开始前完整阅读：
1. CODING_PLAN.md
2. job.json
3. prompt.md
4. schema.json
5. taxonomy.json
6. coding_plan/shards.json

执行要求：
1. 按 coding_plan/shards.json 的顺序逐片处理 input_parts/part-*.jsonl，每片最多 25 篇；
   可以最多并行 4 个互不重叠的分片，但每篇公告必须独立判断，禁止把多篇内容混入同一结果。
2. prompt.md 是唯一语义判断规则，schema.json 是唯一 result 结构，taxonomy.json 是唯一
   事件字典。不要创造私有标准，不要调整门槛，不要自己做 benchmark。
3. document_ir_parts 仅用于核对完整冻结原文；evidence_packet_parts 用于核对本次可见证据。
   禁止联网补事实，禁止读取 Gold、reference、既有 predictions、canonical 事件、数据库、
   其他模型输出或其他批次结果。
4. 每个输入恰好输出一行到同名 output_parts/part-*.jsonl。外层 envelope 的 document_id、
   artifact_hash、input_hash、semantic_task_id、execution_job_id、binding_id 必须逐字复制；
   executor.kind 固定为 coding-plan，provider/model/client_version 严格使用 CODING_PLAN.md 的绑定值。
5. result 必须严格符合 schema.json。每条证据只能引用冻结输入中存在的 chunk_id，quote 必须是
   对应 chunk 中连续、逐字存在的原文。只抽取原文明示的当前事实，不输出情绪、影响方向、置信度、
   股价预测、目标价或买卖建议。
6. 对每个 taxonomy candidate 分别核对主体、生命周期、日期和必需事实。同一公告可以有多个 mention；
   不要把不同主体、日期或交易拼成一个事件。无法组成完整事件时不要猜，使用具体 no_event_reason。
7. 每片先写 part-xxxx.jsonl.tmp。完成后自检 JSONL 可逐行解析、document_id 无遗漏和重复、身份字段
   完全一致、mentions 与 no_event_reason 二选一、所有 quote 均可逐字定位；再原子替换正式 part 文件。
8. 不运行 collect，不运行 import，不连接 ECS，不修改源码、配置、任务输入或生产数据。

全部完成后停止。最后只报告：任务 ID、完成分片数、输出行数、失败或歧义 document_id、总耗时和
可获得的 token 用量。不要自行评价质量，也不要通过反复修改结果迎合校验器。
```

## Codex 验收

Claude 返回后由 Codex 执行：

```bash
python3 -m stock_analyze.cli intelligence-semantic-coding-plan-collect \
  --repo-root . \
  --job <job-id-or-directory>
```

`collect` 只写任务目录内的原始输出、规范化输出、隔离清单和校验报告，
不写 `semantic_runs`、canonical events 或研究因子。首次完整提交失败时，系统会生成
`coding_plan/repair-1/`，其中只有失败输入、完整 IR、原输出、错误码和 `REPAIR.md`。
Claude 只写 `repair-1/output.jsonl`，不得修改原四个输出分片；修复结果必须一次覆盖全部
失败 document_id，缺行或混入已通过文档都会被拒绝且不消耗修复机会。第二次仍失败则
保留隔离，不继续循环。

只有 Codex 确认 `status=ready_to_import`，或明确接受“有效行入库、失败行隔离”的
部分结果后，才执行现有 `intelligence-semantic-import`。
