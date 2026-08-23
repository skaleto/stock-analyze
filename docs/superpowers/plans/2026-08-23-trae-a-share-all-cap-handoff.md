# A 股全市场研究扩展 Trae 交接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Trae 从当前已提交状态继续完成 A 股全市场 PIT 数据基础、开发期测评、一次性留出集、隔离纸面 season 和 Dashboard，并由 Claude 对每个阶段执行独立规格与代码质量审查。

**Architecture:** 本文只负责交接基线、执行顺序、停止条件和审查闸门；具体实现细节以三份既有计划为准。数据层先发布 checksummed PIT sources/universe，测评层只在 2018–2024 开发窗运行并通过不可变 guard 控制 2025+ 留出集，运行层只创建独立 season 子账本且保持旧正式模拟账本不变。

**Tech Stack:** Python 3.11、pandas、PyArrow、Tushare、Baostock、unittest、React/TypeScript、Vitest、systemd、Trae、Claude Code。

---

## 1. 交接基线

Trae 必须直接打开现有 worktree，不要在主工作区重新 checkout 同一分支：

```text
$HOME/.config/superpowers/worktrees/New project/research-workflow-universe-expansion-20260821
```

分支与实现基线提交：

```text
branch: codex/research-workflow-universe-expansion-20260821
implementation baseline: d2c58c9 feat: materialize PIT all-cap sleeve membership
```

开始前运行：

```bash
git status --short
git log --oneline -12
git merge-base --is-ancestor d2c58c9 HEAD
```

预期：工作树为空，ancestor 检查退出 0；HEAD 只允许在 `d2c58c9` 之后包含本交接计划提交。如果不一致，先记录差异并保护用户改动，不得 reset、checkout 或覆盖。

必须先读完：

```text
AGENTS.md
configs/research/a_share_all_cap_v2.yaml
docs/superpowers/specs/2026-08-23-a-share-all-cap-strategy-v2.md
docs/superpowers/validation/2026-08-18-evidence-first-research-stop-decision.md
docs/superpowers/plans/2026-08-23-a-share-all-cap-data-foundation.md
docs/superpowers/plans/2026-08-23-a-share-all-cap-evaluation.md
docs/superpowers/plans/2026-08-23-a-share-all-cap-season-dashboard.md
```

当前完成状态：

| 范围 | 状态 | 提交/说明 |
| --- | --- | --- |
| 策略设计与预注册 | 完成 | `27f291f`，合同窗口、袖套、成本、门槛已冻结 |
| 机器可读合同 | 完成并已审查 | `3429cf2` 至 `2bd6f01` |
| 全市场参考源 collector | 完成并已审查 | `110717e` 至 `934debc` |
| PIT 季度成员、每日硬状态、稳定袖套 | 已实现，82 个合同/source/universe 联合测试通过，未完成独立审查 | `d2c58c9` |
| 系统审计与缺口补齐 runbook | 未开始 | 数据基础 Task 4 |
| 生产数据拉取与 readiness evidence | 未开始 | 数据基础 Task 5；受磁盘门槛约束 |
| 开发期测评与 holdout guard | 未开始 | 测评计划 Task 1–5 |
| season、运行时和 Dashboard | 未开始 | season/Dashboard 计划 Task 1–5 |

已知生产约束：ECS `/` 总计约 40GB，仅余约 3.1GB，空闲比例约 7.8%，低于合同要求的 15%。在扩容或经审计的冷数据迁移完成前，不得发布全量新数据，不得删除 `AGENTS.md` 中的任何保护路径。

## 2. Claude 强制审查协议

每个实现 Task 使用相同闭环：

1. Trae 先按 TDD 完成一个 Task 并提交一个边界清晰的 commit。
2. Trae 调用一个新的 Claude 会话做“规格符合性审查”；Claude 只读，不修改文件。
3. Trae 修复全部 P0/P1/P2，运行聚焦测试并提交修复。
4. Trae 调用另一个新的 Claude 会话做“代码质量与数据泄漏审查”；不得复用第一次会话结论。
5. Trae 修复全部 P0/P1/P2，并让 Claude 复审到 `APPROVED`。
6. 把最终审查结论、commit 范围和测试证据写入 `docs/superpowers/validation/reviews/a-share-all-cap/` 后再进入下一 Task。

Claude 调用要求：

- 在上述 worktree 根目录执行。
- 使用 `claude -p --permission-mode plan --effort high` 或 Trae 内等价的只读 Claude review 能力。
- 不使用 `--dangerously-skip-permissions`。
- 提示 Claude读取 `AGENTS.md`、冻结合同、对应实现计划和实际 diff。
- 输出必须按严重度列出 `file:line`、触发场景、影响、建议修复和缺失测试；没有问题时明确输出 `APPROVED`。
- Claude 的“无问题”不能替代测试；测试通过也不能替代 Claude 审查。

规格审查提示词模板：

```text
你是独立规格审查员，只读审查，不得修改文件。请先阅读 AGENTS.md、
configs/research/a_share_all_cap_v2.yaml、
docs/superpowers/specs/2026-08-23-a-share-all-cap-strategy-v2.md，
以及当前 Task 所属计划。审查 git diff <BASE>..<HEAD> 是否完整且严格满足冻结合同，
重点检查 PIT/可用时间、幸存者偏差、缺失数据 fail-closed、保护账本、checksum/manifest、
一次性 holdout 和磁盘 15% 门槛。按 P0/P1/P2/P3 输出 findings，每条给 file:line、
触发场景、影响、修复建议和应新增测试。不要评论未改动的无关旧代码。
若无 P0/P1/P2，最后一行输出 APPROVED。
```

代码质量审查提示词模板：

```text
你是独立代码质量和量化回测审查员，只读审查，不得修改文件。请审查
git diff <BASE>..<HEAD>，并读取相关 tests。重点检查：未来数据泄漏、同日生效、
PIT as-of join、历史退市/ST/停牌/涨跌停、袖套缓冲、流动性分位数、T+1、下一开盘、
ADV 2%/5%、交易成本、缓存/manifest 身份绑定、原子发布、路径穿越/符号链接、
字符串 dtype、内存峰值、断点续跑和错误恢复。按 P0/P1/P2/P3 输出 findings，
每条给 file:line、可复现输入和缺失测试。若无 P0/P1/P2，最后一行输出 APPROVED。
```

## 3. Phase A：关闭 `d2c58c9` 的审查缺口

**Files:**

- Review: `stock_analyze/research/a_share_all_cap_universe.py`
- Review: `stock_analyze/research/a_share_all_cap_universe_store.py`
- Review: `tests/test_research_a_share_all_cap_universe.py`
- Contract: `docs/superpowers/plans/2026-08-23-a-share-all-cap-data-foundation.md` Task 3

- [ ] **Step 1: 重新验证已提交实现**

```bash
.venv/bin/python -m unittest tests.test_research_a_share_all_cap_universe -v
.venv/bin/python -m unittest \
  tests.test_research_a_share_all_cap_contract \
  tests.test_research_a_share_all_cap_sources \
  tests.test_research_a_share_all_cap_universe \
  tests.test_cli_research_all_cap -v
python3 -m compileall -q \
  stock_analyze/research/a_share_all_cap_universe.py \
  stock_analyze/research/a_share_all_cap_universe_store.py \
  tests/test_research_a_share_all_cap_universe.py
git diff --check
git status --short
```

预期：聚焦测试全部通过、compileall 退出 0、工作树为空。

- [ ] **Step 2: 让 Claude 做 Task 3 规格审查**

审查范围固定为 `934debc..d2c58c9`。除通用模板外，要求 Claude 特别核对：季度评审日到下一交易日才生效、上一期袖套只在原始 eligibility 通过后应用、排名 3,800 以后为 `nano_watch` 且零资金、行业区间为 `in_date <= review_date < out_date`、新进/保留成交额门槛分别为前 80%/90%、缺失状态不默认正常。

- [ ] **Step 3: Trae 修复 findings 并补回归测试**

所有修复保持在 Task 3 两个模块及其测试内；如果必须改变冻结合同，立即停止并报告，不能自行修改合同。

- [ ] **Step 4: 让新的 Claude 会话做代码质量复审**

复审范围从 `934debc` 到修复后的 HEAD。只有最终结论为 `APPROVED` 且聚焦测试全部通过，Phase A 才完成。

- [ ] **Step 5: 提交审查修复与证据**

建议提交：

```bash
git commit -m "fix: close all-cap PIT universe review findings"
```

## 4. Phase B：完成数据基础和生产 readiness

严格执行 `docs/superpowers/plans/2026-08-23-a-share-all-cap-data-foundation.md` Task 4–5。

### Task B1：系统审计与可续跑缺口补齐 runbook

- [ ] 先为 `scripts/system-audit.sh` 写失败测试，新增 source manifest、universe manifest、财报代码覆盖、历史状态代码覆盖、股票主表计数和文件系统空闲比例。
- [ ] 缺少 PIT status 或空闲比例低于 15% 必须是 `FAIL`；本地无数据只能是受控 `WARN`，不能伪造 `PASS`。
- [ ] 在 `docs/system-harness.md` 写出 statements、status、all-cap sources 的精确可续跑命令，规定 transient systemd、journal、前后磁盘记录和禁止 `--force`。
- [ ] 运行 `python3 -m unittest tests.test_system_audit_script -v` 与 `./scripts/system-audit.sh`。
- [ ] 提交 `ops: audit all-cap research data readiness`。
- [ ] 依照第 2 节完成两轮 Claude 审查；审查还要确认脚本不读取、重写或清理正式账本。

### Task B2：解决 15% 磁盘硬门槛

- [ ] 只读盘点 ECS 文件系统、`/opt/stock-analyze/app/data` 和可验证的冷数据候选，记录文件数、字节数、当前 reader 和 canonical replacement。
- [ ] 首选扩容；只有用户明确授权且有 canonical replacement 时才迁移/删除冷数据。
- [ ] 不得删除 `data/shared/cache/`、`data/shared/backtest_cache/`、研究模型、纸面账户、通知状态、备份证据或 AGENTS.md §3 中任一路径。
- [ ] 重新运行远程 audit；只有空闲比例 `>= 0.15` 才允许开始全量拉取和 universe publication。

如果无法满足该门槛，写 `insufficient_data:filesystem_free_fraction`，Phase B 在此停止；不得为了赶进度降低阈值。

### Task B3：生产数据拉取、PIT universe 物化与不可变证据

- [ ] 通过 transient systemd 依次补 statements、Baostock status 和 all-cap reference sources；保存 unit 名、开始/结束时间、退出码和 journal 摘要。
- [ ] 每个 collector 断点续跑，不使用 `--force`，失败后不得推进 `latest.json`。
- [ ] 运行 universe materializer，验证 source/cache identity、checksum、年份分区、重复键、每日硬状态完整性和发布后空闲比例。
- [ ] 运行数据基础计划 Task 5 的聚焦测试、compileall、全量测试和 `git diff --check`。
- [ ] 新建 `docs/superpowers/validation/2026-08-23-a-share-all-cap-data-readiness.md`，记录 checksums、日期边界、年度/袖套 row/code counts、状态缺失、财报覆盖、磁盘前后字节和最终 `ready` 或 `insufficient_data`。
- [ ] 提交 `docs: record all-cap data readiness evidence`。
- [ ] 让 Claude 审查“证据是否足以支持 ready”，而不是只审代码。Claude 未输出 `APPROVED` 时不能进入 Phase C。

## 5. Phase C：开发期测评，禁止提前打开 2025+

严格执行 `docs/superpowers/plans/2026-08-23-a-share-all-cap-evaluation.md` Task 1–5。每个 Task 单独提交并执行两轮 Claude 审查。

### Task C1：冻结决策日和 PIT 袖套适配器

- [ ] 实现策略/袖套特定决策周期和 `effective_date` as-of attachment。
- [ ] 拒绝重复 code/effective-date、成员来源晚于 signal date 以及不完整 manifest。
- [ ] 保持旧 HS300/ZZ500 路径行为不变。
- [ ] 提交 `feat: attach PIT all-cap sleeves on frozen decision dates`。

### Task C2：同样本、同成本的六个基线/候选回放

- [ ] 固定六个 trial：官方袖套指数、PIT 市值加权、PIT 等权、旧透明范围、router_only、all_cap_v2。
- [ ] 强制相同 rows、dates、costs；复用下一开盘、T+1、整手、精确涨跌停、2% 基础 ADV 和 5% 硬上限。
- [ ] `all_cap_v2` 不修改当前两个 overlay 的因子权重，只在袖套内归一化。
- [ ] 提交 `feat: replay all-cap baselines and transparent candidates`。

### Task C3：袖套独立门槛、容量、DSR/PBO

- [ ] 四个有资金袖套分别计算净超额、IC/ICIR、fold/年份稳定性、回撤、成本压力、成交率、ADV、清算天数、集中度、DSR 和 PBO。
- [ ] 聚合 35/30/25/10 只能展示，不能掩盖任何失败袖套。
- [ ] 容量披露固定为 1/5/10/20 倍资金和正常/成交减半/连续跌停三种清算情景。
- [ ] 提交 `feat: gate all-cap evidence per sleeve and capacity`。

### Task C4：一次性 holdout guard

- [ ] 先实现和审查 guard，不读取任何 2025-01-01 之后的收益。
- [ ] marker 必须在读取 holdout returns 之前 exclusive-write，绑定 contract hash 与 development artifact checksum，第二次调用必须失败。
- [ ] development 未全部通过时只写失败证据，不得打开 holdout。
- [ ] 提交 `feat: guard one-time all-cap holdout evaluation`。

### Task C5：只运行 2018–2024 development

- [ ] 运行四段 purged expanding walk-forward，只生成一个不可变 development result。
- [ ] 结果必须列出六个 trial、四个 folds、全部袖套门槛、DSR/PBO 试验数和 `pass`/`failed`/`insufficient_data`。
- [ ] 失败或数据不足时原样提交并停止候选，不能调参、改频率、缩样本或打开 2025+ 修复结果。
- [ ] 提交 `research: record all-cap development result`。
- [ ] 让 Claude 对结果、trial registry 完整性和“没有 2025+ 观察”做最终证据审查。

只有 development 为 `pass` 且 Claude `APPROVED`，才可执行一次 holdout。holdout 失败则保留结果并停止，不进入 Phase D。

## 6. Phase D：隔离 paper season 和 Dashboard

仅当 Phase C 的 development 与一次性 holdout 都通过时执行。严格遵循 `docs/superpowers/plans/2026-08-23-a-share-all-cap-season-dashboard.md` Task 1–5。

### Task D1：season 路径隔离和资格锁

- [ ] 新状态只能写到 `data/a_share/<agent>/seasons/all_cap_v2/` 和对应 reports 子目录。
- [ ] 无 `--season` 时旧运行时与路径必须字节级保持原行为。
- [ ] 未通过袖套资金为零且留在总账户现金，不得重新分配。
- [ ] 提交 `feat: isolate qualified all-cap paper season`，完成两轮 Claude 审查。

### Task D2：只读快照 provider 和袖套内预选

- [ ] Dashboard/runtime provider 只读本地 verified parquet；任何 request handler 不得调用外部 provider。
- [ ] 先按 sleeve 过滤再预选，保留当前持仓，不使用全局大市值偏好。
- [ ] 提交 `feat: serve all-cap sleeves from verified snapshots`，完成两轮 Claude 审查。

### Task D3：调仓周期、部分目标移动和 sleeve 子账本

- [ ] 所有 sleeve 每日更新 NAV，只有到期 sleeve 生成新目标。
- [ ] 订单不超过 target gap、2% ADV 和剩余换手预算；5% ADV 为断言，不是可调参数。
- [ ] 缺失 ADV 或精确涨跌停数据时禁止新增买入。
- [ ] 提交 `feat: schedule and cap all-cap sleeve rebalances`，完成两轮 Claude 审查。

### Task D4：Dashboard season/袖套观测

- [ ] API 展示总账户、四袖套、候选漏斗、数据覆盖、gross/net/cost stress、回撤、换手、成交、ADV、清算、容量、DSR/PBO 和 gate reasons。
- [ ] 不完整 manifest 只返回受控 `unavailable`；页面不触发采集、研究或交易。
- [ ] 运行后端 unittest、前端 Vitest 和 build。
- [ ] 提交 `feat: show all-cap season and sleeve evidence`，完成两轮 Claude 审查。

### Task D5：部署与隔离纸面验证

- [ ] 保存部署前旧正式账本 hashes、failed units、timer/child service 状态和磁盘比例。
- [ ] 只使用 `./scripts/deploy-app-to-ecs.sh`，不得自行 rsync 账户数据。
- [ ] 只初始化通过资格的 `all_cap_v2` season；不迁移、不清零、不重写旧根账本。
- [ ] 验证 Dashboard HTTP 200、schema、season ledgers、manifests、timers、child terminal results 和部署后旧账本 hashes 不变。
- [ ] 新建 `docs/superpowers/validation/2026-08-23-a-share-all-cap-paper-season.md` 并提交。
- [ ] 让 Claude 对部署证据做最终只读审查；任何正式账户变动都是 P0，必须停止。

## 7. 每阶段验证矩阵

| 阶段 | 必跑验证 | 通过条件 |
| --- | --- | --- |
| A | contract/source/universe/CLI 聚焦测试、compileall、diff-check | 零聚焦失败，Claude 两轮 APPROVED |
| B | system audit test、本地/远程 audit、manifest verifier | PIT status 完整，checksum 正确，磁盘空闲 `>=15%`，readiness=`ready` |
| C | features/campaign/evaluation/holdout 测试、开发期运行 | 无 2025+ 泄漏，四段 OOS，全部资金袖套过门槛 |
| D | season/competition/provider/simulator/API/UI 测试、前端 build | 旧路径与账本不变，新 season 隔离，Dashboard 只读 |
| 最终 | `git diff --check`、compileall、全量 unittest、前端 test/build、本地/远程 audit | 无新增失败；所有相关失败清零；Claude 最终 APPROVED |

仓库全量测试当前基线曾出现 8 failures、1 error、6 skipped，涉及 semantic config versions、notifier、label version 和磁盘门槛等既有问题。Trae 必须保存基线与最终结果逐项对比：当前改动造成的失败必须清零；与本活动相关的磁盘/readiness 失败不能豁免；无关既有失败只能如实记录，不能宣称“全量测试全部通过”。

## 8. 绝对停止条件

出现以下任一情况，Trae 必须 fail closed，保留证据，不得自行绕过：

- 磁盘空闲低于 15%，或无法证明待迁移/删除数据有 canonical replacement。
- PIT 状态、行业有效区间、来源日期、单位、manifest 或 checksum 缺失/冲突。
- 任何 2025+ 收益在 development gate 之前被读取。
- development 或 holdout 任一资金袖套失败。
- Claude 提出未关闭的 P0/P1/P2。
- 需要改变冻结因子权重、频率、成本、袖套边界、样本窗口或准入门槛。
- 需要触碰正式账户、模型 Registry、旧 competition ledger、真实券商或下单接口。

停止不是删除失败结果。必须提交 immutable `failed` 或 `insufficient_data` 证据，让后续决策基于真实结果。

## 9. Trae 最终交付清单

- [ ] 当前分支完整 commit 列表与每个 Task 的 commit 对应关系。
- [ ] 每个 Task 两轮 Claude review 及最终 `APPROVED` 证据。
- [ ] 数据 source/universe/feature/result manifests 与 checksums。
- [ ] 数据覆盖、状态缺口、年度/袖套 counts、磁盘前后字节。
- [ ] development 与 holdout 的不可变结果；失败版本不得隐藏。
- [ ] season 资格、资金留现金规则、旧正式账本 hash 不变证据。
- [ ] 后端测试、前端测试/build、本地/远程 audit 的原始摘要。
- [ ] Dashboard URL/HTTP/schema 和四袖套观测截图或结构化响应摘要。
- [ ] 最终明确结论：`ready_for_isolated_paper`、`failed_gate` 或 `insufficient_data`，不得使用模糊的“基本完成”。
