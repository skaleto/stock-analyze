# 研究目录浏览器设计

## 目标

让 Dashboard 的“多角色投研”工作区能够分页浏览已落盘的研究目录，并在不加载完整目录到浏览器的前提下，按代码或名称检索 A 股、场内基金与场外基金记录。

## 范围与边界

- 数据源仅为 `data/research/universe_catalogs/latest.json`。
- 浏览器展示三类记录：A 股、场内基金、场外基金。默认打开 A 股。
- A 股、场内基金和场外基金均为研究目录；场外基金始终标注为“非交易研究对照”。
- 所有读取均为只读。接口不得采集行情、调用模型、创建订单、修改正式账户、配置或模型注册表。
- 目录缺失、结构不完整或筛选参数非法时必须返回明确的受控空态或 400 错误；不得猜测或补造记录。

## 备选方案与选择

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 把完整目录塞入现有多角色投研接口 | 不采用 | 约 1.9 万条记录会突破首屏响应边界，且每次打开都加载无关数据。 |
| 前端静态加载三份完整目录后搜索 | 不采用 | 客户端内存、下载量和快照同步都会随目录增长恶化。 |
| 独立的服务端分页目录接口 | 采用 | 每次只返回当前页，可约束请求/响应大小，并保持快照来源唯一。 |

## 后端契约

新增只读端点：`/api/dashboard/research-universe.json`。

请求参数：

| 参数 | 默认值 | 规则 |
| --- | --- | --- |
| `kind` | `a_share` | 仅允许 `a_share`、`exchange_fund`、`otc_fund`。 |
| `query` | 空 | 最多 80 个字符，按代码或名称不区分大小写匹配。 |
| `scope` | 空 | A 股匹配研究指数范围；基金匹配海外暴露分类；不匹配则返回空页。 |
| `page` | `1` | 正整数，超出结果范围时返回空页与准确总数。 |
| `page_size` | `50` | 仅允许 20、50、100，默认 50。 |

响应固定包含：

```json
{
  "schemaVersion": "research-universe-browser-v1",
  "status": "available",
  "asOf": "20260822",
  "kind": "a_share",
  "query": "",
  "scope": null,
  "page": 1,
  "pageSize": 50,
  "total": 1800,
  "scopeOptions": ["csi1000", "hs300", "zz500"],
  "records": []
}
```

返回记录必须是经过投影的公开目录字段，不能透出完整原始 JSON 或绝对路径。

| `kind` | 返回字段 |
| --- | --- |
| `a_share` | `code`、`name`（目录未含名称时为空）、`researchScopes`、`membershipDate`、`recordKind`、`researchOnly` |
| `exchange_fund` | `code`、`name`、`fundType`、`investType`、`benchmark`、`overseasScope`、`classificationStatus`、`tradability`、`researchOnly` |
| `otc_fund` | 与场内基金相同，且 `tradability` 固定为 `otc_non_tradable_research_only` |

所有列表排序固定为代码升序，保证相同快照和参数得到稳定分页。接口只加载一个本地 JSON 快照，并沿用 Dashboard HTTP 缓存；缓存键已经包含完整查询参数。

## 前端交互

在“多角色投研”页的研究范围摘要之后新增“研究目录浏览”区块。

- 三个 Tab：`A股`、`场内基金`、`场外基金`；切换 Tab 时重置页码和筛选条件。
- 搜索框有搜索按钮与清除按钮。输入后按按钮或回车提交；避免每次按键都触发请求。
- 范围下拉项来自接口 `scopeOptions`；选择项只影响当前 Tab。
- 结果使用语义表格：A 股列出代码、名称、指数范围、成员日期；基金列出代码、名称、类型、基准、海外暴露和分类。场外表格在表头和每条记录中标明“非交易研究对照”。
- 分页控件展示“第 X 页 / 共 Y 页 · 共 N 条”，上一页、下一页在边界禁用。
- 加载时保留上一页数据并显示加载状态；受控空态分别说明“目录未生成”与“无匹配结果”；非法响应显示错误横幅。

页面不会提供“运行投研”“刷新目录”或交易入口。

## 实现边界

- 在 `stock_analyze/dashboard_multi_agent_research.py` 中放置目录读取、参数归一化、分页、投影与受控空态。它不能依赖 Tushare、Ark CLI 或正式账户模块。
- 在 `stock_analyze/cli.py` 中注册新 API 路径并将 query 参数传给读取函数。
- 在 `frontend/dashboard/src/api.ts`、`workspaceTypes.ts` 与 `MultiAgentResearchPage.tsx` 添加强类型请求、响应校验和交互界面。
- 复用现有 `useWorkspaceResource` 时，查询 key 必须编码 Tab、关键词、范围、页码和页大小，避免不同页复用错误缓存。

## 测试与验收

- Python：验证三类记录投影、稳定排序、代码/名称搜索、范围筛选、跨页总数、非法参数、目录缺失和 API 路由分发。
- TypeScript：验证响应校验、每个 Tab 的列与研究边界提示、搜索提交、筛选重置、分页边界和加载/空态。
- 端到端发布：现有 Dashboard 前端测试、构建和受限部署测试必须通过；新 API 响应保持在既有 Dashboard 负载上限内。

## 不在本次范围内

- 不采集基金 NAV、持仓、申赎、资金流或新增行情历史。
- 不在 Dashboard 内发起数据采集、模型推理、研报生成或任何交易动作。
- 不将研究目录自动加入正式策略、候选注册表或纸面账户。
