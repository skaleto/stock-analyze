# 公告语义 Anchor 标注者 A 交接

更新日期：2026-07-28

标注者：`codex-a`

状态：第一阶段 80 篇独立盲标完成，尚未形成 Anchor Gold。

## 1. 80 篇与 120 篇的关系

冻结研发集共有 240 篇。第一阶段从中选择 80 篇建立独立 Anchor，目的不是用
80 篇代表全量精度，而是先测量标注分歧、事件边界和困难样本成本，再决定是否
扩到完整 240 篇。

当前可核验的产物中没有一套独立的“120 篇 Anchor 标注”。容易混淆的数字包括：

- 冻结 manifest：240 篇，其中弱标签口径为 180 篇事件候选和 60 篇 no_event；
- Candidate A/B：各 240 份，共 480 份实验输出；
- A/B 自一致结果：74 篇一致、166 篇进入分歧队列；
- 带类别提示重抽：45 篇，已归档为 Disputed，不是 Gold；
- 第一阶段独立盲标工作台：80 篇。

因此，80 是 Anchor 第一阶段规模，不是从 Claude 的 120 篇中遗漏了 40 篇。

## 2. 独立性边界

本轮只读取：

- 每篇盲标工作台中的 `document.json`、`chunks.json`、`tables.json`；
- `entity_whitelist.json`、`revision_context.json`；
- 冻结的 Schema、taxonomy 和标注协议。

本轮没有读取：

- `anchor_sample.jsonl` 中的弱事件族；
- Candidate A/B 输出；
- Silver v0、Disputed 重抽结果；
- Claude 的标注或私有思考材料。

标注严格遵循“有新金额、日期、对象或生命周期变化才形成新事实”的派生文档
政策。法律意见书、会议材料和更正公告不会因为标题像某类事件就被强制归类。

## 3. 交付物

原始标注目录：

```text
data/shared/intelligence/benchmarks/announcement-v1/
  anchor_annotations/codex-a/predictions/
```

目录内有 80 个 JSON 文件，文件名为 `document_id`。每份文件保持冻结语义
Schema 的原始输出形态，不附加 Candidate 或 Gold 元数据。

完整性摘要：

| 项目 | 数量 |
| --- | ---: |
| 文档 | 80 |
| 有事件文档 | 49 |
| no_event 文档 | 31 |
| 事件 | 59 |
| 证据 | 243 |
| 含明确缺失字段的事件 | 27 |

事件类型分布：

| 事件类型 | 数量 |
| --- | ---: |
| risk_warning_delisting | 14 |
| major_contract | 6 |
| merger_restructuring | 5 |
| shareholder_change | 5 |
| guarantee | 4 |
| litigation_arbitration | 4 |
| earnings_flash | 4 |
| buyback | 3 |
| control_change | 3 |
| earnings_forecast | 3 |
| pledge_freeze | 3 |
| dividend | 2 |
| equity_financing | 2 |
| capacity_project | 1 |

没有为了覆盖率强行生成 `investigation_penalty` 正事件。该缺口应由标注者 B
和仲裁流程判断是样本事实、taxonomy 边界还是 A 的漏报。

排序后的逐文件 SHA-256 清单聚合哈希：

```text
ce76f55f4c00b658edd01a781773953844aceb36d2ebc5b3e3215137be160269
```

## 4. 已完成校验

80 份文件统一通过以下检查，结果为 `documents 80 errors 0`：

1. 每篇冻结 `schema.json` 的 Draft 2020-12 Schema 校验；
2. `parse_semantic_document_result` 与冻结 taxonomy 校验；
3. subject 的 `entity_id + role` 必须在该篇实体白名单内；
4. evidence 指向的 chunk 和页码必须存在；
5. quote 在指定 chunk 中必须唯一；
6. `start/end` 必须与原文逐字符一致；
7. 输出目录与 80 个盲标工作台 document_id 一一对应；
8. 输出中不含弱事件族、Candidate、Silver、Gold 或 Disputed 泄漏词。

缺失字段主要来自实体白名单只提供上市公司 issuer，无法合法补入 holder、
counterparty、target、beneficiary 和 controller。原始标注明确记录缺失字段，
没有虚构实体。

## 5. 边界判断示例

- 子公司赴港发行上市：实际发行主体不在 issuer 白名单，且分拆上市不属于现有
  15 类事件，标为 no_event。
- 股权激励回购注销：属于发行人股份回购，按两个激励计划分别记录数量、价格和
  预计完成日期，生命周期为 in_progress。
- 发行股份购买资产：只记 merger_restructuring，不再重复记成募集资金式
  equity_financing。
- 业绩预告提示性公告：只预告将召开董事会、没有修正后数字，标为 no_event。
- 盈利预测实现审核更正：数据属于收购资产的业绩承诺实现，不是上市公司自身
  业绩预告或快报，标为 no_event。
- 减持公告补充更正：只修正合规勾选位置，未改变数量、比例、期间和生命周期，
  标为 no_event。

这些判断是标注者 A 的独立意见，不应在标注者 B 开始前向其暴露。

## 6. Claude 下一步应做什么

1. 保持这 80 份原始 JSON 只读，在导入层附加 `annotator=codex-a`、逐文件
   artifact hash、协议版本和 annotation basis。
2. 完成 P1.5：按事件类型、主体、关键事实和时间进行受约束事件匹配，不能只按
   事件族或 lifecycle 对齐。
3. 完成 P1.6：分别计算证据定位、证据语义支持、事件识别、主体日期金额单位币种、
   no_event 漏报等指标，并输出总体、分族、困难样本和置信区间。
4. 引入独立标注者 B。B 不能读取本目录、Candidate、Silver 或弱事件族。
5. 只把 A/B 分歧项交给仲裁者，记录最终判断、理由和 provenance。
6. 先用 80 篇测分歧率。若某类样本量过小、置信区间过宽或分歧率过高，再按冻结
   规则扩到 240 篇，不要任意追加容易样本。

## 7. 当前不能宣称的结果

- 这 80 份是 Annotator A，不是 Anchor Gold；
- 没有独立 B 和仲裁前，不能计算 Candidate 的真实准确率；
- 没有通过 Anchor Gold 门槛前，不能晋升 Champion；
- 本轮没有写入 canonical 事件、公告因子或正式股票模型；
- 本轮没有修改生产 Provider、生产配置或 ECS 定时任务。
