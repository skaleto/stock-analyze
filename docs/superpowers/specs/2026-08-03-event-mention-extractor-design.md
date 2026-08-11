# 事件级最小抽取器设计

## 目标

把公告语义链从“一次 LLM 直接生成可入库事件”改成“LLM 圈出原文事实，本地编译器
生成事件”。保持 DeepSeek、Codex、Claude 和 Coding Plan 可替换，同时提高真实公告
的可用率，并确保通过事件的关键经济数值没有已知语义错误。

## 现状问题

当前 `announcement-events-v1-lite` 要求模型同时处理事件类型、生命周期、主体 ID、
事实类型、单位、日期、去重键和证据 ID。路由没有命中时还会把全部 15 类 taxonomy
发给模型；真实样本文档 1329840 的 taxonomy 要求约 2.7 万字符，而单类事件通常只有
约 1.5 至 2 千字符。一个事件失败会隔离整篇文档，数值区间也只能被错误压成单值或
拒绝。

## 方案选择

采用“事件 mention + 本地 compiler”，不继续扩写万能 prompt。

- 不采用继续迭代 V14 旧 Schema：改动小，但已经由 V11-V13 证明收益不稳定。
- 不采用每篇多模型投票：成本高，并且不能替代原文证据和确定性经济语义校验。
- 采用单执行器、事件级最小抽取：模型只做最擅长的文本定位，本地代码承担可复算逻辑。

## 新契约

Schema `announcement-mentions-v1-lite` 的每个 mention 只包含：

- `event_type`
- `subjects[]`: `role`、原始名称、内嵌的 `chunk_id + quote`
- `facts[]`: taxonomy 字段名、原始值、内嵌证据
- `dates[]`: 日期类型、原始日期、内嵌证据
- `status`: 可选的原始状态文字和证据

模型不输出生命周期、实体 ID、数值、单位、币种、周期、dedupe key、置信度或交易信号。
证据直接嵌在字段下，不再要求模型维护全局 evidence ID 引用图。

## 本地编译

`mention_compiler.py` 对每个 mention 独立执行：

1. 校验 `chunk_id + quote` 是原文唯一连续子串。
2. issuer 映射为文档白名单证券 ID，外部主体保留逐字名称。
3. 只保留目标 event type 声明的事实和日期。
4. 从标题、状态原文和事实词判断 lifecycle。
5. 归一化中文日期、金额、比例、每股值和周期。
6. 把数值区间拆成 lower/upper，不允许静默取第一个数字。
7. 调用现有 taxonomy validator 和 canonicalizer。
8. 只丢弃失败 mention，保留同文档内其他有效事件。

编译器输出仍是现有 `announcement-events-v1-lite`，因此存储、事件因子、研究模型和
Dashboard 不需要理解新的 LLM 格式。

## 路由与输入

扩充高精度标题词，例如“新建…基地项目”“建设项目”“投资建设”，避免无信号文档
退化为全部 15 类。Job payload 使用精简 `mention_templates`，每类只列主体角色、事实
名和日期名，不再发送生命周期矩阵、required/optional、单位规则和 dedupe 规则。

## 失败处理

- JSON 或 mention Schema 失败：允许一次修复请求。
- 单条证据、主体、事实或日期失败：编译器记录原因并丢弃对应字段。
- mention 仍不满足 taxonomy：仅隔离该 mention。
- 有其他有效 mention：文档结果继续成功。
- 全部 mention 失败且标题有强事件信号：进入 quarantine，不能伪装成 no_event。
- 未验证 prose、partial mention 和 quarantine 永不生成因子或交易信号。

## 验收

先使用 6 篇已知真实失败公告验证产能项目、诉讼、分红、重大合同、业绩快报和配股。
准入要求：

- 通过事件的主体、关键金额、日期、比例和每股值无人工复核错误。
- 数值区间保留上下界。
- 一条坏事件不影响同文档其他有效事件。
- 输入 contract 明显小于旧 taxonomy contract。
- 所有测试通过；canary 不 import；生产 timer 保持关闭。

只有小样本满足这些条件，才扩大到分层样本。更大样本仍不直接恢复生产。
