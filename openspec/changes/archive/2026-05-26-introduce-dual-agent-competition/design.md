## Context

P0 修正后，模拟成交保守；P1 升级后，单 agent 跑得明白。但单 agent 模式有一个本质问题：**任何超额收益都没有"另一个负责任的策略"作为对照**。基准（hs300/zz500）只能告诉你"是不是跑赢了大盘 beta"，无法告诉你"是不是跑赢了另一种讲得通的多因子策略"。

主流量化研究的做法是先跑历史回测，但本项目坚持"先把前向模拟跑明白"。在引入回测引擎之前，最便宜也最贴近真实生产环境的对照方式就是：**让两个独立 agent 在完全一致的市场条件下、用各自的策略 overlay 同时跑前向**。

这个 change 把"双 agent 并行"做成一等公民工作模式，并约定一份让 OpenAI Codex CLI 直接读懂的协作契约（`AGENTS.md`），让 Codex 接管其中一边。

## Goals / Non-Goals

**Goals**

- 让"两个 agent 共享起跑线、独立运行、定期对比"成为可重复执行的工程流程。
- 保证公平：启动资金、账户、`top_n`、股票池、基准、成本、调仓日完全一致。
- 提供机器可读和人类可读两层的月度对比产出，作为下一周期 agent 决策的输入。
- Dashboard 三 tab 直接看出谁领先、领先多少、风格分歧在哪里。
- 兼容现有单 agent 工作模式，不破坏既有 CLI 和文件。

**Non-Goals**

- 不做自动学习模式（agent 自动产生 config patch 并落地）。MVP 只产出对比报告，策略改动由 agent / 人工驱动。
- 不做历史回测引擎。
- 不做 SQLite/DuckDB 数据库迁移；仍是 CSV/JSON。
- 不接券商。
- 不引入新的第三方依赖。
- 不做实时告警/SLA。

## Decisions

### 1. 单仓库 + 命名空间隔离，而非多 git worktree

每个 agent 各自一个 worktree 看似更"隔离"，但月度对比需要把双方数据放到同一进程里读，跨 worktree 共享变成路径外加层。直接在同一仓库里走目录命名空间（`data/<agent>/`、`reports/<agent>/`、`configs/agents/<agent>.yaml`），月度对比就是一个普通的 Python 进程读两个目录，逻辑简单且更容易测试。

代价：两个 agent 共享 source code，要靠 `AGENTS.md` 约束 Codex 不动 `stock_analyze/*.py`。这层约束是软的（基于 agent 的服从），不是硬的（文件锁）；MVP 接受这个折中。

### 2. 公平字段在 baseline，差异字段在 overlay；锁字段在加载层强制

`configs/competition.yaml` 写所有**保证可比性**的字段。`configs/agents/<agent>.yaml` 只放**允许差异**的字段。`competition.load(agent_id)` 做深合并，并在合并过程中检测 overlay 是否试图覆盖 `BASELINE_LOCKED_PATHS`（如 `initial_cash`、`accounts.*.cash`、`accounts.*.top_n`、`accounts.*.scope`、`accounts.*.benchmark`、`trading.*`、`schedule.execution`、`start_date`），覆盖时直接 raise `CompetitionBaselineLocked(field=...)`。

锁实现走"路径白名单"而不是"字段黑名单"，因为深合并的目标是合并所有 overlay 字段；锁住的是"合并后的最终 config 中这些路径必须等于 baseline 的值"。实现上：合并完成后再过一遍 lock check。

### 3. 共享 cache 路径 + agent 隔离写

`AkshareProvider(cache_dir=data/shared/cache)` 给两侧共用。理由：

- 行情/财务/交易日历对两侧应当是同一份"市场快照"，避免出现"Claude 看到一份数据、Codex 看到另一份"的不可解释差异。
- 节省网络与磁盘成本（约 180 只股票每周的 history+financial+valuation 是非平凡量）。

`data/shared/data_health.json` 也共享，记录这次"市场快照"的整体健康状态。Agent-specific 写入仍走各自目录。

### 4. 起跑日写死在 baseline

`competition.yaml.start_date` 是硬编码字符串。`competition-init` 在初始化两侧 `state.json` 时把 `created_at` 设成 `start_date`，并且第一次 `update_nav` 必须以 `start_date` 之后的日期为 `as_of`。

这避免"两侧不在同一天 init 导致 NAV 序列起点错位"，也让 dashboard 上的"第 N 周"是一个明确的数字。

### 5. `top_n` / 股票池 / 基准 锁

按 A10 决策。如果某天想做 small-cap 实验，开 Phase 2 单独 change 解锁。

### 6. 月度 review 走"观察模式"，不自动改 config

MVP 不做学习模式。`competition-monthly-review` 只产出 JSON+MD。Agent 自己读 JSON 后决定要不要改 overlay。

把学习模式（patch 协议 + 自动应用）剥离成下一个 change 的好处：

- 不需要在 MVP 里设计 patch 接受/拒绝、回滚、互锁、安全边界。
- 月度对比报告本身是有价值的产出，先把这一层做扎实。
- 等 Codex 实际跑一两个月之后再决定 patch schema 长什么样更靠谱。

### 7. 比较口径：累计收益 + 信息比率，dashboard 默认按累计收益排

按 A10 决策。月报里两个都列；leaderboard.csv 同时记两列；对比 tab 上方"本月胜方"双卡片（累计收益胜方、IR 胜方）。

### 8. 对比 tab 内容

- 顶部 4 张并列卡片：Claude 累计收益、Codex 累计收益、累计差、本月胜方。
- 双线 NAV 曲线（颜色固定：Claude #2457a7，Codex #b76e00；后续多 agent 可拓展）。
- 横向对比表：累计收益、年化、Sharpe、IR、跟踪误差、最大回撤、换手、成本 bps、Win Rate。
- 最近一期持仓重叠度：双账户分别画一个简单 venn-like 条（共有 / Claude only / Codex only 三段宽度），点击展开持仓表。
- 月度对比报告链接列表（指向 `reports/competition/monthly_review_*.md`）。
- 滚动战绩条：按月叠加，绿色=Claude 胜，橙色=Codex 胜，灰色=未结。

不引入前端框架。三 tab 用 CSS `:target` 切换。fragment 之间用 iframe 隔离样式？暂用同页内 div 切换，因为 dashboard CSS 是受控的——只要 fragment 不重复声明 `<style>` 顶级规则，可以平铺。

### 9. CLI 向后兼容

不带 `--agent` 时，CLI 走老路径：`--config/--data-dir/--reports-dir` 默认值不变（`configs/strategy_v1.yaml` + `data/` + `reports/`）。带 `--agent claude` 时，三参数被推导出来；如果用户同时显式传了，显式参数优先（agent 视角只是"shortcut"）。

`competition-init / competition-monthly-review / competition-dashboard` 是新的顶层命令，不依赖 `--agent`。

### 10. Codex 写隔离靠 `AGENTS.md` 约束 + 路径暗示

`AGENTS.md` 明确告诉 Codex：

- 你的 ID 是 `codex`。
- 你的写入区只有 `data/codex/`、`reports/codex/`、`configs/agents/codex.yaml`。
- 不要读/写 `data/claude/`、`reports/claude/`、`configs/agents/claude.yaml`。
- 不要修改 `stock_analyze/*.py`。
- 不要覆盖 baseline-locked 字段。

我们不做 OS-level 权限隔离（chmod/不同用户）。原因：

- 同一仓库内做 chmod 后 git 会反复 reset；
- 双 user 部署给 systemd 模板增加显著复杂度；
- Codex 本身的工程纪律应足够（这是其设计目标的一部分）。

如果将来 Codex 失控，再补 chmod。

### 11. 单元测试范围

不测真实公开数据接口；用人造 DataFrame / 临时目录跑：

- `tests/test_competition.py`：locked field 拒绝、深合并、resolve_agent_paths、共享 cache 路径。
- `tests/test_monthly_review.py`：人造两侧 daily_nav + trades + positions，验证 spread/overlap/correlation/leaderboard 写入。
- `tests/test_dashboard_aggregator.py`：用预制 fragment HTML 拼装，断言三 tab 标记存在 + 关键字段被替换。

### 12. systemd 错峰

两个 agent 同时跑 `run-daily` 时会同时刷共享 cache，可能造成临时锁等待。错开 5 分钟启动：

- `claude-daily.timer` 16:30
- `codex-daily.timer` 16:35
- `claude-weekly.timer` 周五 17:00
- `codex-weekly.timer` 周五 17:05
- `monthly-review.timer` 每月 1 号 09:00
- `dashboard.service` 持续运行

错峰不是硬约束（共享 cache 是 read-mostly + 文件写 atomically），但能避免不必要的 retry。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 两侧 overlay 选择过于趋同 → 实际跑出来高度相关 | 比较失去意义 | 月度报告显式输出 `daily_return_correlation` + `position_overlap_ratio`，并在报告里给出"差异化建议"段落 |
| 共享 cache 写入竞争 | 偶发 IO 错误 | AkshareProvider 已用 atomic write（pandas to_csv → rename）；systemd 错峰；必要时加文件锁（Phase 2） |
| Codex 越权读 claude 数据 | 不公平 | `AGENTS.md` 强约束 + 在 monthly_review 中显式公开"双方应当看到的对方数据"，让"偷看"不再是必要选项 |
| Codex 修改 `stock_analyze/*.py` | 破坏 claude 跑步 | `AGENTS.md` 红线 + 单元测试 + Git diff 在合入前人工 review |
| 起跑日漂移 | NAV 序列起点错位 | baseline 锁 + `competition-init` 写入 `state.created_at = start_date` |
| 单 agent 命令被破坏 | 老用户回归 | CLI 默认行为不变；新增 `--agent` 是可选参数；测试覆盖单 agent 路径 |
| 共享 cache 把"行情更新瞬态"暴露给双方 | 公平性轻微受损 | 双方在同一进程内顺序跑（systemd 错峰），并发性可忽略 |
| 学习模式延后导致 Codex 调优手动 | 进化慢 | MVP 接受；Phase 2 跟进 |
| 三 tab dashboard 加载慢 | UX 退化 | fragment 渲染各自缓存；对比 tab 计算放进 monthly_review 提前算好 |

## Migration Plan

1. **第一次 `competition-init`**：
   - 检查 `configs/competition.yaml` 与 `configs/agents/{claude,codex}.yaml` 都存在。
   - 创建 `data/{shared,claude,codex,competition}/` 与 `reports/{claude,codex,competition}/`。
   - 各侧调用 `simulator.initialize(merged_config, store)`。
   - 写一份 `data/competition/competition_metadata.json`（含 `start_date`, `competition_id`, baseline hash）。
   - 输出 `Competition initialized: <competition_id> start=<start_date>`。

2. **既有单 agent 用户**：CLI 默认路径继续可用；旧 `data/` 不被清理。

3. **回滚**：
   - 如果竞赛模式失败，可以直接 `rm -rf data/{shared,claude,codex,competition} reports/{claude,codex,competition}`，再回到单 agent 模式。
   - 单 agent 模式的 `data/` 与 `reports/` 不动。

4. **演进**：Phase 2 引入学习模式 / 文件锁 / 多 agent (>2) 时，只扩 `configs/agents/` 与 leaderboard schema，不破坏既有目录布局。

## Open Questions

- 是否需要每周（而非每月）的轻量级对比卡片？倾向不需要——周度信号噪音大，月度更合适；但 dashboard 对比 tab 已经显示最新一次的差距，效果相当。
- `monthly_review.json` 是否应当跟随每个 agent 各放一份（让 agent 不需要跨目录）？倾向单一份在 `data/competition/`，agent 只读不写。
- Codex 在跑出明显落后时是否应当被允许"跨边界看 claude 的最新 signals"？目前答案是不能，只能通过月度报告看快照。这条留给 Phase 2 看实际操作再调。
- `start_date` 取何值？建议第一次 `competition-init` 时由 CLI 写到 `competition.yaml`（如果当前是占位符 `auto`），之后冻结。
