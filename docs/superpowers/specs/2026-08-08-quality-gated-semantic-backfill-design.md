# 质量门控的公告语义历史回填设计

**日期：** 2026-08-08  
**状态：** 已批准执行  
**范围：** 公告语料的历史语义抽取、旧结果重验和归档清理；不改变模拟交易，不允许原文直接触发交易。

## 1. 现状与问题

现有链路已经具备 PDF 下载、文本解析、作业冻结、Provider 抽取、确定性校验、事件入库和研究因子生成能力，但历史回填质量不足：

1. 旧 `semantic-extract-v5` 让大模型同时完成事件识别、实体 ID、生命周期、日期金额标准化和事件组装，任务过重。
2. 旧 taxonomy 把一些可选事实当成事件成立条件，导致“信息不全”被误判成 `no_event`。
3. 历史 Coding Plan 生成了大量批次专用 Python 规则，批量把程序性公告归为无事件，形成不可见的漏报。
4. 过去把“JSON 合法、零 quarantine”当成质量好，未对高信号 `no_event` 和语义正确性做门控。
5. 旧 288 个 canonical 事件仍可能影响研究特征；早期 mention-v1 样本太少，尚不能证明替换质量。

## 2. 简化后的唯一主线

```text
ECS 下载/解析 -> 冻结小批输入 -> Claude/Codex/DeepSeek 只抽 source mentions
-> 本地确定性编译 -> 严格质量门 -> 审核通过后导入 ECS
-> canonical events -> point-in-time factors -> Base vs Base+Event 研究评估
```

大模型只负责：事件类型、原文主体名、原文事实、原文日期、原文状态及逐字段证据。

代码只负责：实体 ID、生命周期、日期/金额/单位/币种标准化、去重、校验、持久化、因子和模型评估。

当前质量验收候选契约固定为：

- profile: `a-share-announcement-mentions-v20`
- prompt: `semantic-mentions-v15`
- schema: `announcement-mentions-v1-lite`
- taxonomy: `cn-announcement-taxonomy-v9`
- compiler: `mention-compiler-v1`

`v1-v19` 的试验批次分别因编译损失、字段经济含义错误、主事件误分类、
强信号漏报、人工语义审计或未见样本泛化失败而冻结。V20 在 V15 prompt
和 V9 taxonomy 上补齐不可变证据合同；只有自动门和逐篇人工门同时通过的
完整 10 篇批次才允许导入。

Provider 只是执行器。Claude Code、Codex Coding Plan、DeepSeek API 或未来 Provider 必须消费同一批 `job.json/input.jsonl/prompt.md/schema.json/taxonomy.json`，不得改变下游契约。

## 3. 历史处理顺序

### Phase A：旧 canonical 事件重验

按事件类型分层选择旧事件对应文档，使用 `intelligence-semantic-repair-prepare` 建立带 `repair_context` 的冻结 mention 修复作业。新结果导入成功后，通过 `semantic_run_replacements` 使旧结果退出当前视图，但保留审计历史。

优先级：监管/退市/诉讼 > 重组/股权/回购 > 合同/中标 > 业绩/分红 > 其他。

### Phase B：未处理的高信号历史积压

只处理 title/routing 命中事件类型、研究股票池、解析质量可用的文档。按事件族分层，避免只吃最新债券或治理公告。

### Phase C：低信号与抽样审计

前两阶段通过后，才处理低信号文档，并保留 5% 随机审计样本衡量漏报。

## 4. Claude Code 执行边界

- 每个文档使用全新、无历史上下文的 `claude -p` 调用。
- Claude 不读取代码库、不写脚本、不访问数据库、不导入 ECS，只输出 schema JSON。
- 固定 prompt、schema、taxonomy 和输入 hash；禁止批次内临时改规则。
- 每处理一篇立即 checkpoint，支持进程退出、睡眠和 quota 恢复后续跑。
- quota/rate limit 不计为文档失败；记录等待状态并指数退避，恢复后从 checkpoint 继续。
- 10 篇为一个审计批次；未过门槛不得导入，也不得自动扩大批次。

## 5. 硬质量门槛

### 契约门

- 输入清单、文件 hash、文档 ID：100% 一致。
- JSON/schema/枚举合法率：100%。
- 每条 evidence 必须是指定 chunk 的连续原文子串：100%。
- 编译器 `rejected` 或 `dropped`：必须为 0；否则整篇 quarantine。
- accepted event 的主体、事件类型和证据对应错误：必须为 0；任一严重错误暂停该批。
- 公告主事件误分类、把未来测算当已发生事实、泛化标题充当 action：必须为 0。
- 生命周期要求由确定性模板提供；完成态项目不得因缺少未来日期而漏报。
- 重复文档输出、重复事件、跨文档证据：必须为 0。

### 漏报门

- title/routing 命中强事件类型但输出 `no_event`：100% 二次复核，不得静默导入。
- 普通 `no_event`：每批抽查 `max(5, 20%)`。
- 抽查漏报率大于 5%：整批不得导入，修订 prompt/编译规则后重跑。

### 运行门

- 首批 10 篇 canary；通过后仍保持每批 10 篇。
- quarantine + failed 比例大于 10%：暂停扩批并定位原因。
- 不以“零 quarantine”单独证明质量；必须同时通过语义和漏报审计。
- 只有完整批次通过门槛才导入 ECS。未审核产物只允许留在本地候选区。

## 6. 清理策略

### 保留

- 原 PDF、解析文本/chunks、SQLite lineage、semantic runs、canonical event 历史。
- 新版冻结 mention 作业、质量报告、修复替换关系和可复现 manifest。

### 归档后移出活跃目录

- 本地 `.local-intelligence-semantic-worker` 里的旧 v5 批次脚本、输出和临时报表。
- ECS `extraction_jobs` 中已完成且已归档的旧 v1-v19 作业目录。
- 已被本文替代的旧 Coding Plan 交接说明。

归档必须包含文件列表、字节数、SHA-256 和恢复命令；校验归档可读后才允许删除活跃副本。禁止删除数据库 lineage 或原始语料。

## 7. 模型贡献与验收

抽取事件不会直接下单。它们先生成 point-in-time 事件因子，再运行同一股票、日期、标签和切分下的 Base 与 Base+Event 配对实验。

只有同时满足下列条件才允许进入 champion 候选：

- feature coverage 足够且无未来泄漏；
- OOS IC/预测指标有稳定增益；
- 组合回放的净成本收益或风险调整收益改善；
- 不同时间窗/事件族下结果稳定；
- registry 审核通过并显式激活。

本轮 10 小时任务只证明“历史抽取质量和可恢复执行链路”，不宣称策略收益提升。

## 8. 回滚

- 未导入批次：删除候选输出即可，不影响 ECS。
- 已导入修复：保留旧 run 和 replacement lineage，可按 repair 记录撤销当前替换。
- Claude 执行异常：停止监督器，checkpoint 保留，DeepSeek 定时器保持暂停。
- 质量门失败：整批隔离，不做部分导入。
