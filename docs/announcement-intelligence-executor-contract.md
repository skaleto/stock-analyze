# 公告语义抽取执行器契约

## 目标

同一份公告任务可以交给 Codex、Claude、DeepSeek API 或其他执行器。
执行器只负责从冻结输入中抽取事实；事件存储、校验、因子和模型链路
不随执行器变化。

生产命令只认版本化 profile 与执行器配置文件。Candidate A/B、Gold 和 Champion
属于历史离线 QA 概念，不是日常抽取协议的一部分，也不会被下游读取。

## 冻结输入

任务目录位于：

```text
data/shared/intelligence/extraction_jobs/<job_id>/
```

执行器只读取：

- `job.json`
- `prompt.md`
- `profile.json`
- `schema.json`
- `taxonomy.json`
- `input.jsonl`

V21 任务额外冻结：

- `document_ir.jsonl`：完整解析结构与来源关系；
- `evidence_packets.jsonl`：不超过 profile 预算的确定性证据包。

V21 的 `semantic_task_id` 只由语义合同、文档、制品与输入决定；
`execution_job_id` 另外绑定 `executor_mode + provider + model +
client_version`。切换 API、Coding Plan 或模型时必须新建 execution job，
不得修改原 job 的执行器字段。

不得修改这些文件，也不得从互联网补写原文没有的信息。每一行必须保持
原有的 `document_id`、`artifact_hash` 和 `input_hash`。

V20 及更早逐行输入契约为 `semantic-payload-v3`。V21 使用
`semantic-payload-v4`，并在同一行记录 task、execution job 与 binding ID。
该行的
`payload.taxonomy_requirements` 已按路由候选裁剪，并明确列出可用生命周期、
必需主体、`all_of`、`one_of_sets`、必需日期和可选事实。所有执行器都必须以
这个逐行字段执行抽取；根目录 `taxonomy.json` 用于版本审计和导入校验，不能用
执行器私有规则替代。

## 输出

输出为同目录下的 `output.jsonl`，每个输入文档至多一行。外层必须包含：

```json
{
  "contract_version": "semantic-extraction-output-v1",
  "document_id": 123,
  "artifact_hash": "sha256...",
  "input_hash": "sha256...",
  "executor": {
    "kind": "coding-plan",
    "provider": "codex",
    "model": "codex"
  },
  "usage": {},
  "result": {}
}
```

`result` 必须符合任务内的 `schema.json`。证据只提交
`chunk_id + 原文逐字 quote`；不得提交偏移量、方向分、强度、目标价、
涨跌预测或买卖建议。

## Coding Plan / Codex / Claude

1. 逐行读取 `input.jsonl`。
2. 将 `prompt.md`、`profile.json` 和该行 `payload` 作为抽取上下文，
   并逐项执行 `payload.taxonomy_requirements`。
3. 只输出 `schema.json` 允许的字段。
4. 将完整外层记录追加到临时文件。
5. 全部完成后原子替换为 `output.jsonl`。
6. 先由统一 runner 做本地 Schema、IR、grounding 和 compiler 校验；
   Coding Plan 本身不连接生产数据库，也不直接导入。
7. 只有通过发布门槛的指定 execution job 才执行导入命令：

```bash
python -m stock_analyze.cli intelligence-semantic-import \
  --repo-root /opt/stock-analyze/app \
  --job <job-id-or-directory>
```

## OpenAI-Compatible API

执行器配置只描述 endpoint、模型、密钥文件环境变量和预算。运行时选择
配置，不存在系统必选 Provider：

```bash
python -m stock_analyze.cli intelligence-semantic-run \
  --repo-root /opt/stock-analyze/app \
  --job <job-id-or-directory> \
  --executor-config <profile-or-yaml>
```

接口失败、JSON 失败和预算耗尽只影响当前语义任务，不阻断公告下载、
解析或研究行情任务。

## 本地确定性闸门

导入器统一执行以下检查：

- 任务、文档、制品和输入哈希一致；
- Schema 和 taxonomy 合法；
- 主体符合白名单，外部主体必须有原文名称证据；
- quote 能在冻结 chunk 中唯一定位；
- 日期、数值、单位、币种和必需事实一致；
- 有效结果写入 canonical event；无事件写入 `no_event`；
- 歧义或失败写入 quarantine，并保留稳定原因码。

任何未通过闸门的语料、LLM 判断或 observing 因子都不能影响模拟下单。

## 幂等与重试

- 相同输入和相同执行器的终态结果不会被覆盖。
- `no_event` 和全 canonical 结果不会重复执行。
- 隔离结果通过新 payload/prompt/schema 版本形成新输入哈希后重试。
- `output.jsonl` 可以逐篇续写；重复运行只处理缺失行。
- V21 校验失败时只允许把完整事件候选重抽一次，不能返回字段补丁；
  再失败则整篇隔离。

## 执行器质量抽检

生产批次默认只使用一个执行器。更换执行器或检测到质量漂移时，从相同任务契约中
抽取小规模分层样本，对 Schema、grounding、接受/隔离、错误、token 与耗时进行
独立检查。抽检结果不执行 import，也不修改执行器资格；不同模型结果一致不等于
Gold，小样本通过也不等于生产批准。

## 上线准则

先完成 1 篇真实文档，再完成 20 篇有界批次。只有当有效率、隔离原因、
耗时和成本符合预算时，才配置自动 API 执行。否则每日服务健康地停在
`awaiting_executor`，由任意兼容执行器接手。
