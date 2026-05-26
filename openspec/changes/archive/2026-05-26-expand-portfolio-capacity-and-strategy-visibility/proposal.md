## Why

竞赛框架跑通后用户提出三个明确诉求：

1. **持仓太少**：默认 `top_n=10` 配双账户共 20 只，分散度不够；单股进入/退出对净值冲击太大，因子信号被噪音盖住。希望"每个模型 100 只以内"，让因子工作的样本更稳。
2. **优化链路看不清**：现在 dashboard 能看到选股 / 成交 / NAV / 笔记，但**月度策略提案**（`data/<agent>/proposals/<month>-strategy.json`）和**最新分析任务包**（`data/<agent>/notes/briefings/<...>.md`）没有可视化；用户没法在一张页面里读懂 "这个月我提了什么 → 是否应用 → 下个月效果如何"。
3. **缺一份能读完就懂的中文总览**：当前 README + runbook + 多份 plan 文档分散，新加入者得逐份读才能拼齐全貌。

三件事彼此正交但密度低，放进一个 change 一并落地。

## What Changes

### 1) 把 `top_n` 上调到 50（双账户合计 100）

- `configs/competition.yaml` baseline：
  - `accounts.*.top_n`：`10` → `50`
  - `trading.max_single_weight`：`0.10` → `0.05`（等权 2% × 2.5 倍封顶，避免极端集中）
- `configs/agents/{claude,codex}.yaml` 各自的 `filters.max_fetch_candidates`：`180/200` → `250`，给筛选留更大头部空间
- `configs/strategy_v1.yaml`（单 agent 兼容入口）保持 `top_n: 10` 不变——它不在竞赛轨道，避免回归

### 2) Dashboard 加可视化：策略演进 + 最新任务包 + 双方观察对照

- 每个 agent 视图（page 与 fragment 同步生效）末尾追加：
  - **策略演进时间线**：列出 `data/<agent>/proposals/*-strategy.json` 全部条目，按月份倒序；每行显示月份、`rationale` 摘要、`patch` 改了哪些键、风险、当月与下月实际累计收益（从 `data/competition/leaderboard.csv` 取）。
  - **本期分析任务包**：折叠展示 `data/<agent>/notes/briefings/` 中最新一份 markdown 的完整内容，明确"agent 这周在看什么 / 任务说明 / 输出契约"。
- 聚合 dashboard 的"对比"tab 末尾追加：
  - **本周双方观察对照**：side-by-side 拉双方最新 `*-weekly-review.md`，让用户一眼看到 "两边对本周的判断有没有分歧"。
- 所有新面板 HTML-escape，超长截断，缺失时显示占位文案（不崩溃）。

### 3) 系统总览中文文档

- 新增 `docs/system-overview.md`，~3500 字一篇，从"系统在做什么"到"agent 怎么跑、文件怎么排、阶段怎么衔接"全部覆盖。
- README 顶部增加一行直链。
- 不替换现有 `docs/forward-simulation-runbook.md` / `docs/competition-runbook.md`；总览相当于这两份的入口。

## Capabilities

### New Capabilities

- `competition-portfolio-capacity`：基线 `top_n=50` / `max_single_weight=0.05`；候选池 `max_fetch_candidates=250`；既有 `state.json` 不需要重置（迁移 note）。
- `agent-strategy-evolution-view`：策略演进时间线、最新任务包、双方观察对照三个 dashboard 面板及其空态处理。

### Modified Capabilities

- 无破坏性 spec 修改。`competition-baseline-fairness` 的锁字段集合不变；锁住的字段值变了——这是 baseline 升级，不是协议变化。

## Impact

- **代码**：
  - `stock_analyze/reporting.py`：新增 `render_strategy_evolution_panel`、`render_latest_briefing_panel`、`read_agent_proposals` 三个 helper；page/fragment 都嵌入新面板。
  - `stock_analyze/dashboard_aggregator.py`：compare tab 末尾追加 `_render_observation_pairing`。
  - 无新模块；不引入第三方依赖。
- **配置**：
  - `configs/competition.yaml` 改两个字段；`baseline_hash` 会变。
  - `configs/agents/{claude,codex}.yaml` 各改一个 `max_fetch_candidates`。
- **数据/产物**：
  - 既有 `data/<agent>/state.json` 不动；`top_n` 是配置项而非状态。
  - 下次 `run-weekly --agent <id>` 自动按新 `top_n` 选 50 只。`build_target_orders` 会同时跟既有 10 只持仓对齐——超出的会出现 "新增买入" 订单。
  - dashboard 渲染会自动拉新 proposal / briefing。
- **文档**：`docs/system-overview.md` 新增，README 顶部新增一行。
- **测试**：新增 dashboard 面板渲染测试（策略演进 / 最新 briefing / 观察对照），既有 61 测试不动。
- **不在本次范围**：
  - 提案的自动应用（Phase 2 `enable-monthly-config-evolution`）。
  - 增加新因子。
  - 改基准（hs300/zz500）或交易成本结构。
