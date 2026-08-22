# 研究目录浏览器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在“多角色投研”Dashboard 中提供 A 股、场内基金和场外基金的服务端分页、检索和范围筛选浏览器。

**Architecture:** 保持现有多角色投研摘要接口不变，新增一个仅从研究目录快照读取的独立分页端点。前端把摘要与目录页作为两个 React Query 资源；目录资源的 query key 包含 Tab、提交后的关键词、范围、页码和页大小，确保不会复用错误页面的数据。

**Tech Stack:** Python 3.11、现有 `http.server` Dashboard 路由、JSON 研究目录、React 18、TanStack Query、TypeScript、Vitest、unittest。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `stock_analyze/dashboard_multi_agent_research.py` | 目录快照的只读参数校验、投影、排序、筛选和分页。 |
| `stock_analyze/cli.py` | 注册并分发 `research-universe.json`，只传递 HTTP 查询参数。 |
| `tests/test_dashboard_multi_agent_research.py` | 后端目录浏览器契约和失败闭环。 |
| `tests/test_cli_dashboard_routes.py` | 路由注册与查询参数分发。 |
| `frontend/dashboard/src/workspaceTypes.ts` | 强类型的目录页、记录和请求参数。 |
| `frontend/dashboard/src/api.ts` | URL 编码、响应长度限制和运行时响应校验。 |
| `frontend/dashboard/src/useWorkspaceResource.ts` | 为分页资源增加可选的上一页占位数据支持。 |
| `frontend/dashboard/src/useWorkspaceResource.test.tsx` | 验证 opt-in 占位数据不会影响默认资源行为。 |
| `frontend/dashboard/src/MultiAgentResearchPage.tsx` | Tab、搜索、范围筛选、结果表和分页控件。 |
| `frontend/dashboard/src/MultiAgentResearchPage.test.tsx` | 用户交互、加载/空态和研究边界回归。 |
| `frontend/dashboard/src/styles.css` | 目录控制栏、Tab、表格和窄屏滚动样式。 |
| `docs/system-harness.md` | 记录新只读 API 的 curl 验证命令。 |

### Task 1: 后端只读目录分页器

**Files:**

- Modify: `stock_analyze/dashboard_multi_agent_research.py`
- Test: `tests/test_dashboard_multi_agent_research.py`

- [ ] **Step 1: 写入三个失败的目录分页测试**

在测试辅助目录中写入一个含两条 A 股、两条场内基金、两条场外基金的 `latest.json`。新增以下断言：

```python
def test_projects_sorted_a_share_page_and_scope_options(self) -> None:
    payload = build_dashboard_research_universe_data(
        repo_root=root, kind="a_share", query="", scope=None, page=1, page_size=20,
    )
    self.assertEqual([row["code"] for row in payload["records"]], ["000001.SZ", "000002.SZ"])
    self.assertEqual(payload["scopeOptions"], ["csi1000", "hs300"])
    self.assertEqual(payload["total"], 2)

def test_filters_fund_code_or_name_scope_and_paginates(self) -> None:
    payload = build_dashboard_research_universe_data(
        repo_root=root, kind="exchange_fund", query="纳斯", scope="nasdaq_100", page=1, page_size=20,
    )
    self.assertEqual(payload["total"], 1)
    self.assertEqual(payload["records"][0]["code"], "513100.SH")

def test_rejects_invalid_kind_page_size_or_query_length(self) -> None:
    with self.assertRaisesRegex(InvalidDashboardQuery, "kind"):
        build_dashboard_research_universe_data(..., kind="all", query="", scope=None, page=1, page_size=20)
```

再添加目录不存在时返回 `status="unavailable"`、`records=[]`、`total=0` 的测试，以及超过结果页仍保留准确 `total` 的测试。

- [ ] **Step 2: 运行测试确认失败原因是缺少分页构建器**

Run:

```bash
python3.11 -m unittest tests.test_dashboard_multi_agent_research -v
```

Expected: `ImportError` 或 `AttributeError` 指向尚不存在的 `build_dashboard_research_universe_data`。

- [ ] **Step 3: 实现最小的参数归一化、记录投影和分页**

在 `dashboard_multi_agent_research.py` 定义不变量与构建器：

```python
RESEARCH_UNIVERSE_BROWSER_SCHEMA = "research-universe-browser-v1"
RESEARCH_UNIVERSE_KINDS = frozenset({"a_share", "exchange_fund", "otc_fund"})
RESEARCH_UNIVERSE_PAGE_SIZES = frozenset({20, 50, 100})

def build_dashboard_research_universe_data(
    *, repo_root: str | Path | None, kind: str, query: str,
    scope: str | None, page: int, page_size: int,
) -> dict[str, object]:
    ...
```

要求实现：

- 用 `InvalidDashboardQuery` 拒绝未知 `kind`、`page < 1`、不在 `{20,50,100}` 的 `page_size`、超过 80 字符的 `query`、超过 128 字符的 `scope`；错误消息只包含参数名和约束。
- 读取 `latest.json`；不可读时返回受控 `unavailable` 响应，字段完整且不含异常路径。
- A 股从 `payload["a_share"]["records"]` 读取，基金从 `payload["funds"]["records"]` 按 `market_source` 筛选；丢弃非映射或缺少代码的行。
- 仅投影规格声明的字段。A 股的 `name` 不在当前快照时返回空字符串；不要发起补数或外部读取。
- 用 `casefold()` 对代码和名称匹配，范围分别匹配 A 股 `research_scopes` 和基金 `overseas_scope`；先按代码升序，再计算 `total` 与切片。
- `scopeOptions` 为当前 Tab 未筛选完整集合的稳定排序；基金不把空范围放入选项。
- 每个响应始终含 `schemaVersion`、`status`、`asOf`、`kind`、`query`、`scope`、`page`、`pageSize`、`total`、`scopeOptions`、`records` 与 `executionEffect="none_research_only"`。

- [ ] **Step 4: 运行后端分页测试确认通过**

Run:

```bash
python3.11 -m unittest tests.test_dashboard_multi_agent_research -v
```

Expected: 现有摘要读取测试和新增分页、搜索、非法参数测试全部通过。

- [ ] **Step 5: 提交后端分页器**

```bash
git add stock_analyze/dashboard_multi_agent_research.py tests/test_dashboard_multi_agent_research.py
git commit -m "feat: paginate research universe catalog"
```

### Task 2: 注册受控 Dashboard API 路由

**Files:**

- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_cli_dashboard_routes.py`

- [ ] **Step 1: 写入失败的路由与分发测试**

```python
def test_research_universe_api_dispatches_bounded_query(self) -> None:
    expected = {"status": "available", "records": []}
    with TemporaryDirectory() as tmp, mock.patch(
        "stock_analyze.dashboard_multi_agent_research."
        "build_dashboard_research_universe_data", return_value=expected,
    ) as builder:
        status, payload = self._serve_api(
            Path(tmp), "kind=otc_fund&query=纳斯&scope=nasdaq_100&page=2&page_size=50",
            path="/api/dashboard/research-universe.json",
        )
    self.assertEqual(status, 200)
    self.assertEqual(payload, expected)
    builder.assert_called_once_with(
        repo_root=Path(tmp).resolve(), kind="otc_fund", query="纳斯",
        scope="nasdaq_100", page=2, page_size=50,
    )
```

再添加 `page=bad` 和 `page_size=bad` 返回 `400`、`error="invalid_query"` 的断言。

- [ ] **Step 2: 运行路由测试确认端点尚未识别**

Run:

```bash
python3.11 -m unittest tests.test_cli_dashboard_routes -v
```

Expected: 新测试失败，因为路径尚未加入 `_is_dashboard_api_path` 或未分发到构建器。

- [ ] **Step 3: 实现路由和原始参数解析**

在 `_is_dashboard_api_path` 加入路径，并在 `_serve_dashboard_api` 的多角色分支前加入：

```python
if canonical_path == "/api/dashboard/research-universe.json":
    raw_page = (params.get("page") or ["1"])[0]
    raw_page_size = (params.get("page_size") or ["50"])[0]
    try:
        page, page_size = int(raw_page), int(raw_page_size)
    except ValueError as exc:
        raise InvalidDashboardQuery("page and page_size must be integers") from exc
    return build_dashboard_research_universe_data(
        repo_root=repo_root,
        kind=(params.get("kind") or ["a_share"])[0],
        query=(params.get("query") or [""])[0],
        scope=(params.get("scope") or [None])[0] or None,
        page=page,
        page_size=page_size,
    )
```

不得把 `market`、`agent` 默认值传给该构建器，也不得在该分支导入数据提供者或模型模块。

- [ ] **Step 4: 运行路由测试确认通过**

Run:

```bash
python3.11 -m unittest tests.test_cli_dashboard_routes -v
```

Expected: 新端点识别、精确参数分发和 400 错误映射均通过。

- [ ] **Step 5: 提交 API 路由**

```bash
git add stock_analyze/cli.py tests/test_cli_dashboard_routes.py
git commit -m "feat: expose research universe browser api"
```

### Task 3: 前端类型、安全请求与保留上一页

**Files:**

- Modify: `frontend/dashboard/src/workspaceTypes.ts`
- Modify: `frontend/dashboard/src/api.ts`
- Modify: `frontend/dashboard/src/useWorkspaceResource.ts`
- Modify: `frontend/dashboard/src/useWorkspaceResource.test.tsx`
- Modify: `frontend/dashboard/src/api.test.ts`

- [ ] **Step 1: 先添加失败的 API 契约测试与占位数据测试**

在 `api.test.ts` 使用 mock `fetch` 验证：

```ts
await expect(fetchResearchUniverse({
  kind: "exchange_fund", query: "纳斯", scope: "nasdaq_100", page: 2, pageSize: 50,
})).resolves.toMatchObject({ kind: "exchange_fund", total: 1 });
expect(fetch).toHaveBeenCalledWith(
  "/api/dashboard/research-universe.json?kind=exchange_fund&query=%E7%BA%B3%E6%96%AF&scope=nasdaq_100&page=2&page_size=50",
  expect.anything(),
);
```

并断言超过 100 条 `records`、错误的 `executionEffect` 或基金记录缺少 `tradability` 时拒绝响应。为 `useWorkspaceResource` 添加一个 `keepPreviousData` 为 `true` 时，在新的分页 key 首次请求中保留前页数据的异步测试；默认选项仍应返回新 key 的空值。

- [ ] **Step 2: 运行前端测试确认缺少 API、类型和选项**

Run:

```bash
npm test -- --run src/api.test.ts src/useWorkspaceResource.test.tsx
```

Expected: TypeScript/Vitest 失败，指出 `fetchResearchUniverse` 与新类型/选项不存在。

- [ ] **Step 3: 实现强类型请求、响应校验与 opt-in 占位数据**

在 `workspaceTypes.ts` 增加：

```ts
export type ResearchUniverseKind = "a_share" | "exchange_fund" | "otc_fund";
export type ResearchUniverseRecord = { code: string; name: string; recordKind: string; researchOnly: true; ... };
export type ResearchUniversePage = { schemaVersion: "research-universe-browser-v1"; ... };
```

在 `api.ts` 使用 `URLSearchParams` 构造 `kind`、`query`、可选 `scope`、`page`、`page_size`；响应上限设为 80,000 字节。校验 `pageSize` 属于 20/50/100、`records.length <= pageSize`、`total >= 0`、页码是正整数、所有字符串字段有界、以及三类记录所需字段。

给 `useWorkspaceResource` 增加第三个可选参数：

```ts
type WorkspaceResourceOptions = { keepPreviousData?: boolean };

placeholderData: options.keepPreviousData
  ? (previousData) => previousData
  : undefined,
```

默认 `keepPreviousData` 为 `false`，所以其余工作区行为不变。

- [ ] **Step 4: 重跑 API 与资源 hook 测试确认通过**

Run:

```bash
npm test -- --run src/api.test.ts src/useWorkspaceResource.test.tsx
```

Expected: 新目录 URL、运行时校验和 opt-in 上一页占位行为通过；默认资源测试保持通过。

- [ ] **Step 5: 提交前端数据层**

```bash
git add frontend/dashboard/src/workspaceTypes.ts frontend/dashboard/src/api.ts frontend/dashboard/src/api.test.ts frontend/dashboard/src/useWorkspaceResource.ts frontend/dashboard/src/useWorkspaceResource.test.tsx
git commit -m "feat: add typed research universe page client"
```

### Task 4: 实现目录浏览器交互和响应式结果表

**Files:**

- Modify: `frontend/dashboard/src/MultiAgentResearchPage.tsx`
- Modify: `frontend/dashboard/src/MultiAgentResearchPage.test.tsx`
- Modify: `frontend/dashboard/src/styles.css`

- [ ] **Step 1: 写入失败的页面交互测试**

扩展页面 fetch mock，使摘要请求和 `research-universe.json` 请求按 URL 返回不同 payload。新增测试覆盖：

```tsx
await user.click(screen.getByRole("tab", { name: "场内基金" }));
await waitFor(() => expect(fetch).toHaveBeenCalledWith(
  expect.stringContaining("kind=exchange_fund"), expect.anything(),
));

await user.type(screen.getByRole("searchbox", { name: "搜索研究目录" }), "纳斯");
await user.click(screen.getByRole("button", { name: "搜索" }));
expect(await screen.findByText("纳斯达克100ETF")).toBeInTheDocument();
expect(screen.getByText("第 1 页 / 共 2 页 · 共 60 条")).toBeInTheDocument();
```

还要断言：切换 Tab 清空关键词/范围并回到第 1 页；最后一页“下一页”禁用；场外 Tab 显示“非交易研究对照”；无匹配结果与目录不可用分别显示不同消息；页面没有运行、刷新目录或交易按钮。

- [ ] **Step 2: 运行页面测试确认新控件尚不存在**

Run:

```bash
npm test -- --run src/MultiAgentResearchPage.test.tsx
```

Expected: 因找不到 Tab、搜索框、分页文本或结果表失败。

- [ ] **Step 3: 实现状态机、表格和控件**

在 `MultiAgentResearchPage` 添加：

```tsx
const [kind, setKind] = useState<ResearchUniverseKind>("a_share");
const [draftQuery, setDraftQuery] = useState("");
const [query, setQuery] = useState("");
const [scope, setScope] = useState<string | null>(null);
const [page, setPage] = useState(1);
const browserKey = `research-universe:${kind}:${query}:${scope ?? ""}:${page}:50`;
```

用 `fetchResearchUniverse` 和 `useWorkspaceResource(browserKey, true, loader, { keepPreviousData: true })` 获取页数据。Tab 改变、搜索提交、清除、范围改变都必须把页码设为 1；上一页/下一页只改变页码。使用语义 `<table aria-label="研究目录结果">`，按 `kind` 切换列；对场外记录在表头与 `tradability` 单元格加研究对照标签。

在 `styles.css` 增加局部类：

```css
.research-universe-tabs { display: flex; gap: 8px; ... }
.research-universe-controls { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(150px, .45fr) auto auto; ... }
.research-universe-table-wrap { overflow-x: auto; border: 1px solid var(--line); }
.research-universe-pagination { display: flex; justify-content: space-between; align-items: center; ... }
```

窄屏媒体规则把控制栏改为单列，结果表保持水平滚动；不要改变其他工作区的全局表格类。

- [ ] **Step 4: 重跑页面测试确认通过**

Run:

```bash
npm test -- --run src/MultiAgentResearchPage.test.tsx
```

Expected: Tab、搜索、筛选重置、分页边界、空态和“非交易研究对照”断言全部通过。

- [ ] **Step 5: 提交浏览器界面**

```bash
git add frontend/dashboard/src/MultiAgentResearchPage.tsx frontend/dashboard/src/MultiAgentResearchPage.test.tsx frontend/dashboard/src/styles.css
git commit -m "feat: browse research universe in dashboard"
```

### Task 5: 文档、综合验收与发布准备

**Files:**

- Modify: `docs/system-harness.md`

- [ ] **Step 1: 写入只读接口验证命令**

在现有多角色投研 curl 命令之后增加：

```bash
curl -s 'http://127.0.0.1:8765/api/dashboard/research-universe.json?kind=a_share&page=1&page_size=50'
curl -s 'http://127.0.0.1:8765/api/dashboard/research-universe.json?kind=exchange_fund&query=%E7%BA%B3%E6%96%AF&page=1&page_size=50'
```

- [ ] **Step 2: 执行与变更匹配的后端综合测试**

Run:

```bash
python3.11 -m compileall -q stock_analyze tests
python3.11 -m unittest tests.test_dashboard_multi_agent_research tests.test_cli_dashboard_routes -v
```

Expected: 全部通过；目录缺失和无效请求均按受控路径处理。

- [ ] **Step 3: 执行前端套件和生产构建**

Run:

```bash
pyshim_dir=$(mktemp -d)
ln -s /opt/homebrew/bin/python3.11 "$pyshim_dir/python3"
PATH="$pyshim_dir:$PATH" npm test -- --run
test_status=$?
unlink "$pyshim_dir/python3" && rmdir "$pyshim_dir"
exit "$test_status"
npm run build
```

Expected: 全部 Vitest 测试和 TypeScript/Vite 构建通过。

- [ ] **Step 4: 执行变更检查并提交文档**

Run:

```bash
git diff --check
git add docs/system-harness.md
git commit -m "docs: document research universe browser api"
git status --short --branch
```

Expected: 没有空白或尾随空格错误，工作树仅保留用户明确要求保留的内容。

- [ ] **Step 5: 走现有受限发布流程**

在用户授权部署后，运行：

```bash
export SA_ECS_REMOTE=root@120.55.188.242:/opt/stock-analyze/app
export SA_ECS_SSH_OPTS='-i $HOME/.ssh/<ssh-key-file>'
./scripts/deploy-app-to-ecs.sh capture-preimage > /tmp/research-universe-browser-preimage.manifest
./scripts/deploy-app-to-ecs.sh capture-release-input > /tmp/research-universe-browser-release.manifest
export SA_DASHBOARD_PREIMAGE_MANIFEST=/tmp/research-universe-browser-preimage.manifest
export SA_DASHBOARD_RELEASE_INPUT_MANIFEST=/tmp/research-universe-browser-release.manifest
./scripts/deploy-app-to-ecs.sh deploy
```

Expected: 发布仅同步现有 allowlist 中的 Dashboard 文件与前端构建产物；之后访问新 API，验证 A 股首页、基金查询和场外研究对照标识。

## 计划自检

- 规格中的三类 Tab、分页、搜索、范围筛选、只读边界、异常空态和窄屏可用性分别由 Task 1–4 覆盖。
- 所有 API 名称、字段名和参数名与规格一致：`research-universe.json`、`kind`、`query`、`scope`、`page`、`page_size`。
- 计划不包含行情/NAV/持仓采集、模型调用或正式策略变更。
- 每个实现任务先写失败测试、验证红灯、最小实现、验证绿灯，再做独立提交。
