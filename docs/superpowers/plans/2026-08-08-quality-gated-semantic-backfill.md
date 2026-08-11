# 质量门控公告语义历史回填执行计划

> **执行说明：** 按任务顺序执行；任何批次未通过质量门时停止扩大范围，但继续保留诊断和可恢复状态。

**目标：** 清理旧临时产物，用统一的轻量 mention 契约和本地 Claude Code 在 10 小时监督窗口内重验旧 canonical 事件并处理高信号积压，所有结果经确定性校验和人工审计后再导入 ECS。

**架构：** ECS 负责下载、解析、冻结作业和最终入库；本机 runner 为每篇文档启动独立 Claude Code 调用；项目现有 `run_job` 负责 schema、grounding、mention compiler 和 checkpoint；新的 campaign supervisor 负责 quota 恢复、质量统计和批次门控。

---

## Task 1：冻结现状与归档清单

**产物：** `archive/semantic-extraction/2026-08-08/manifest.json`

1. 记录本地旧 worker 文件数、作业数、版本、字节数和 SHA-256。
2. 记录 ECS extraction jobs 的同类清单及数据库 counts。
3. 打包旧 v5/修复版本作业，验证 archive 可列目录、hash 匹配。
4. 只从活跃作业目录移走已归档旧文件；不删除 PDF/chunks/SQLite lineage。

## Task 2：建立 Claude Code Provider

**Files:**

- Create: `stock_analyze/intelligence/semantic/claude_code_provider.py`
- Test: `tests/test_intelligence_semantic_claude_code_provider.py`

1. 先写失败测试：命令构造、JSON wrapper 解析、usage 映射、quota 错误分类、schema 失败。
2. 实现 `SemanticExtractionProvider` adapter；默认 `--tools ""`，每篇 fresh `claude -p`，只收 JSON。
3. 将 quota/rate limit 映射为 retryable `SemanticProviderError`，其他 CLI/解析错误 fail closed。
4. 运行 focused tests。

## Task 3：建立可恢复 campaign supervisor

**Files:**

- Create: `stock_analyze/intelligence/semantic/local_campaign.py`
- Create: `scripts/run-local-semantic-claude-campaign.sh`
- Test: `tests/test_intelligence_semantic_local_campaign.py`

1. 先写失败测试：10 小时 deadline、逐文档 checkpoint、quota 等待、停止文件、批次统计。
2. supervisor 每次只运行一个冻结作业；调用现有 `run_job`，不自己实现事件编译。
3. 复用作业的 `run_report.json`、`quarantine.jsonl`，并额外输出
   `campaign_state.json` 汇总执行状态、quota 等待和整批质量门；不再复制一套
   `quality_report/audit_queue` 中间格式。
4. 达到 quota 时等待后重试；达到 deadline 或 stop file 时正常退出。

## Task 4：扩展质量门

**Files:**

- Modify: `stock_analyze/intelligence/semantic/exchange.py`
- Modify: `configs/intelligence_extraction_profiles/a_share_announcement_mentions_v1.json`
- Test: `tests/test_intelligence_semantic_exchange.py`

1. 为所有 title/routing 强信号事件类型建立 `no_event` review policy，而不是只覆盖调查、退市和重组。
2. 任何 mention compiler rejection/drop 都使该文档 quarantine，禁止带损导入。
3. 输出每篇的强信号、编译接受/拒绝数和 review 原因，供 supervisor 门控。
4. 运行 exchange/mentions/validation focused tests。

## Task 5：改写唯一交接文档

**Files:**

- Replace: `docs/local-semantic-extraction-coding-plan-handoff.md`
- Modify: `docs/announcement-intelligence-runbook.md`

文档只描述当前冻结的轻量 mention 契约、固定输入输出、禁止生成批次脚本、10 篇批次、质量门、quota 恢复、导入审批和恢复方法。旧 full-event/remediation 说明仅保留在 archive。

## Task 6：暂停 DeepSeek 并部署准备能力

1. 停止并 disable `stock-analyze-intelligence-semantic.timer`，确认 service 不在运行。
2. 仅同步 Task 2-5 所需代码/配置/文档到 ECS。
3. 运行 ECS focused tests 和 1 篇 prepare dry run，不调用 DeepSeek。

## Task 7：清理旧活跃产物

1. 校验本地和 ECS archive 可恢复。
2. 清除本地旧批次脚本和旧 output 副本，只保留 archive、当前 runner 和质量报告。
3. 清除 ECS extraction_jobs 中已归档旧 job dirs，保留 `.locks` 和当前验收作业。
4. 记录清理前后字节数和文件数。

## Task 8：运行 10 小时 Claude campaign

1. 从旧 canonical 事件按 event type 分层选择首批 10 篇，生成带 repair context 的冻结 mention 作业。
2. 拉到本机；运行 canary；Codex 检查全部 positive 和全部强信号 `no_event`。
3. 通过后导入 ECS，使对应旧 run 进入 replacement lineage。
4. 继续下一批 10 篇；优先重验旧 canonical，再处理未见高信号历史积压。
5. 每批记录：输入、事件、no_event、quarantine、编译损失、审计结论、耗时和 usage。
6. Claude quota 命中时保持 checkpoint，等待恢复后继续；不切换为 DeepSeek 偷跑历史批次。
7. 10 小时到达后停止新文档，完成当前文档并生成最终报告。

**当前执行候选：** `a-share-announcement-mentions-v20` /
`semantic-mentions-v15` / `cn-announcement-taxonomy-v9`。V1-V19 已冻结为
失败诊断、局部修复证据或已被替换的旧合同，不得由执行器自行回退。

**原子批次门：** 修复集通过后必须再跑 10 篇未见泛化集。任一文档出现
quarantine、compiler rejection/drop、主事件误分类、主体错绑、必需事实
漏报或证据合成，整批拒绝；禁止把 9/10 的通过项部分导入。

## Task 9：最终验收

1. 运行所有 semantic focused tests。
2. 核对导入批次的 hash、run、candidate、canonical event 和 replacement lineage。
3. 对比积压前后：旧 canonical 已重验数、高信号新处理数、剩余数。
4. 计算抽样漏报率、严重错误率、quarantine 率和事件族覆盖。
5. 生成 `reports/intelligence/2026-08-08-claude-backfill-campaign.md`。
6. DeepSeek 保持暂停，直到历史 campaign 质量验收和剩余计划明确；不自动恢复。

## 2026-08-09 执行结果

- Task 1-7 已完成：旧 worker 和 ECS 作业均可恢复归档，DeepSeek 保持暂停。
- Task 8 已完成 10.129 模型小时：30 份报告、172 篇完成、51 篇失败诊断，
  quota 等待 0。
- 3 个完整 10 篇批次通过自动门和人工门并导入，共新增 7 条 canonical 事件；
  其他批次全部隔离，未部分导入。
- V20 有一批 10/10 通过，后一批因半年报裸表格缺少语义表头证据为 9/10，
  整批拒绝。这说明 fail-closed 链路有效，但不把 V20 描述成零失败模型。
- Task 9 已完成：273 项 semantic tests 全绿，ECS 活跃作业为 0，最终报告见
  `reports/intelligence/2026-08-09-claude-backfill-campaign.md`。
