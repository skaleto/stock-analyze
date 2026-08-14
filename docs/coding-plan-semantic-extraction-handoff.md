# Coding Plan 公告语义抽取资格盲测交接

本文只用于 80 篇冻结参考集的执行器资格验收，不用于生产历史回填。
生产历史回填请使用 `docs/claude-historical-semantic-backfill-prompt.md` 和
`intelligence-semantic-coding-plan-collect`，不要读取 Gold 或运行质量 benchmark。

## 直接交给 Coding Plan 的任务指令

将下面整段原样发给 Coding Plan。任务包路径由准备命令生成；当前推荐路径为：

`$HOME/.config/superpowers/worktrees/New project/market-intelligence/.artifacts/semantic-v27-coding-plan-qualification`

```text
你要完成一轮 A 股公告语义抽取盲测。目标不是修改代码，而是严格按照冻结的 v27
提示词、Schema、taxonomy 和证据合同，为任务包中的每篇公告生成结构化抽取结果。

唯一允许读取的业务输入目录：
$HOME/.config/superpowers/worktrees/New project/market-intelligence/.artifacts/semantic-v27-coding-plan-qualification

先完整阅读该目录中的 README.md、manifest.json、prompt.md、profile.json、
schema.json 和 taxonomy.json，再逐行处理 input.jsonl。document_ir.jsonl 只用于查找
完整冻结原文和核对逐字证据。

硬性边界：
1. 这是盲测。禁止搜索、读取或推断任何 reference、Gold、anchor_annotations、既有
   predictions、acceptance report、DeepSeek 输出或生产 semantic 结果。
2. 禁止联网补充事实，禁止修改源码、配置、数据库、任务输入或任何生产数据。
3. 只能写任务目录下的 output.jsonl.tmp，全部完成并自检后原子重命名为
   output.jsonl；不要运行 import，不要连接 ECS，不要发布结果。
4. input.jsonl 每一行必须且只能对应 output.jsonl 一行；document_id 不得遗漏或重复。
5. 外层 envelope 的 document_id、artifact_hash、input_hash、semantic_task_id、
   execution_job_id、binding_id 必须逐字复制输入行；executor 字段按 README.md 固定值。
6. result 必须严格符合 schema.json。每条 evidence 只能使用冻结输入中存在的
   chunk_id，并引用该 chunk 内连续、逐字可找到的 quote。
7. 只抽取原文明确支持的当前事实。不要输出情绪分、重要性、影响方向、置信度、
   股价预测、目标价、买卖建议或任何外部推断。
8. 对每个 payload.taxonomy_requirements 逐项核对主体、all_of、one_of_sets、日期和
   生命周期。不能满足完整事件合同就不要勉强组装；没有有效事件时给出简短、具体的
   no_event_reason。
9. 同一公告可输出多个 mention。不要把不同主体、不同日期或不同交易拼成一个事件，
   也不要因标题命中某事件类型就跳过正文核验。
10. 可分批处理或使用子任务并行，但所有子任务都必须遵守相同盲测边界和统一输出合同。

完成前执行自检：JSONL 每行可解析、行数等于 manifest.input_document_count、文档 ID
集合完全一致、Schema 字段无增删、mentions/no_event_reason 二选一、所有 quote 可在对应
chunk 中逐字找到。最后只报告完成行数、失败行数、总耗时和可获得的 token 用量，不要
给出质量结论，也不要读取参考答案自行打分。
```

## 准备任务包

在干净的 `market-intelligence` worktree 执行：

```bash
cd "$HOME/.config/superpowers/worktrees/New project/market-intelligence"

python3 -m stock_analyze.cli intelligence-semantic-frozen-prepare \
  --repo-root . \
  --workbench data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench \
  --profile a-share-announcement-mentions-v27 \
  --job .artifacts/semantic-v27-coding-plan-qualification \
  --provider claude \
  --model claude-fable-5 \
  --client-version claude-code-2.1.215
```

上面只是 Claude Code 示例。使用 Codex 或其他 Coding Plan 时，必须在生成任务前把
`provider/model/client-version` 改成实际执行器。切换执行器或模型必须重新生成一个新
目录，不能复用旧任务或直接修改 manifest，因为执行器身份已经进入不可变任务 ID。

导出的任务包登记 80 篇冻结样本；其中当前 65 篇需要 Coding Plan 抽取，15 篇由
确定性路由器直接判为无事件。任务包包含这 65 篇的输入和完整 Document IR，不包含
Gold、参考标注或历史模型答案。15 篇路由结果会在回收阶段按同一规则自动补入
predictions。

## 回收与独立验收

Coding Plan 写完 `output.jsonl` 后，由 Codex 在同一 worktree 执行，Coding Plan 不执行：

```bash
python3 -m stock_analyze.cli intelligence-semantic-frozen-collect \
  --repo-root . \
  --workbench data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench \
  --job .artifacts/semantic-v27-coding-plan-qualification \
  --predictions .artifacts/semantic-v27-coding-plan-qualification/predictions.jsonl \
  --report .artifacts/semantic-v27-coding-plan-qualification/compile-report.json

python3 -m stock_analyze.cli intelligence-semantic-quality-evaluate \
  --reference data/shared/intelligence/benchmarks/announcement-v1/anchor_annotations/codex-a/annotator-a.jsonl \
  --predictions .artifacts/semantic-v27-coding-plan-qualification/predictions.jsonl \
  --output .artifacts/semantic-v27-coding-plan-qualification/quality-report.json
```

第一条命令验证 envelope、执行器身份、哈希、Schema、taxonomy、主体、日期、数值、单位、
逐字证据和事件编译；任何失败都保留稳定错误码。第二条命令才读取隐藏参考答案计算质量，
两条命令均为离线验收，`production_import=false`。

如果首轮 compile report 为 `partial`，只允许生成一次受约束修复：

```bash
python3 -m stock_analyze.cli intelligence-semantic-frozen-repair-prepare \
  --repo-root . \
  --workbench data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench \
  --source-job .artifacts/semantic-v27-coding-plan-qualification \
  --source-predictions .artifacts/semantic-v27-coding-plan-qualification/predictions.jsonl \
  --repair-job .artifacts/semantic-v27-coding-plan-repair-1 \
  --provider claude \
  --model claude-fable-5 \
  --client-version claude-code-2.1.215
```

修复执行器只处理 `repair-job/input.jsonl`，并按其中的 `payload.repair_context` 修正
稳定错误码，仍然看不到 Gold。返回 `repair-job/output.jsonl` 后执行：

```bash
python3 -m stock_analyze.cli intelligence-semantic-frozen-repair-collect \
  --repo-root . \
  --workbench data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench \
  --source-job .artifacts/semantic-v27-coding-plan-qualification \
  --source-predictions .artifacts/semantic-v27-coding-plan-qualification/predictions.jsonl \
  --repair-job .artifacts/semantic-v27-coding-plan-repair-1 \
  --predictions .artifacts/semantic-v27-coding-plan-repair-1/predictions.jsonl \
  --report .artifacts/semantic-v27-coding-plan-repair-1/compile-report.json
```

修复结果必须返回完整对象而不是字段补丁。系统会验证源输出哈希、首次编译结果哈希、
修复任务身份并重新编译全量 80 篇；第二次仍失败就保留失败，不允许继续循环修复或读取
Gold 调参。

## 判定口径

合同最低合格线：

- 最终 predictions 覆盖 80 篇，Coding Plan 的 65 行输出完整，Schema 100%；
- 事件 precision >= 90%，recall >= 80%；
- evidence grounding >= 99%；
- 已抽取数值 precision >= 98%；
- no-event 误报率 <= 10%。

“可替代当前 DeepSeek v27”使用更严格的非劣化判断：

- 事件误报仍为 0，事件召回不低于当前 90.20%；
- grounding 100%，已抽取数值 precision 100%，no-event 误报为 0；
- 数值参考覆盖率不低于当前 61.04%；
- 分事件族和逐文档差异中没有新的系统性漏抽、主体混淆或历史事实误当当前事实。

当前 DeepSeek v27 冻结基线为 80 篇、precision 100%、recall 90.20%、grounding
100%、已抽取数值 precision 100%、no-event 误报 0、数值参考覆盖率 61.04%。最低线
通过只说明合同可用，不自动说明已经达到 DeepSeek 水平，更不代表因子具备投资有效性。

## 2026-08-12 Claude 内容验收

Claude 完成首轮 65 篇输出和一次限定修复后，本地确定性编译覆盖 80/80 篇，最终事件
precision 100%、recall 92.16%、grounding 100%、已抽取数值 precision 100%、数值参考
覆盖率 61.04%、no-event 误报 0。按内容指标已达到 DeepSeek v27 非劣化线，且事件召回
高 1.96 个百分点；这些结果只来自离线冻结集，没有导入生产或触发交易。

本轮首任务在执行前错误地绑定为 `codex/coding-plan-current`，实际却由 Claude 执行；限定
修复任务已正确绑定 `claude/claude-fable-5/claude-code-2.1.215`。因此本轮可证明内容与
编译链路合格，但不能登记为审计完备的 Claude 正式基线。下一次正式资格运行必须从
`frozen-prepare` 开始填写真实 provider/model/client-version，不得事后改 manifest。

## 我会回传的结论

拿到 `output.jsonl` 后，Codex 会给出：

1. 完整性与编译错误清单；
2. 总体指标及与 DeepSeek v27 的差值；
3. 分事件族、困难样本和逐文档差异；
4. 误报、漏报、证据、主体、日期和数值错误的代表案例；
5. “不合格 / 合同合格但不可替代 / 可替代 / 明显更优”四档结论；
6. 若不达标，只修改通用 prompt/profile/compiler 中有共性的部分，不针对 Gold 样本写特例。
