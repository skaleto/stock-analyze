## Why

`align-beginner-friendly-multi-factor-foundations` 已经把单个策略的可信度做到"跑得明白"的程度。下一个真问题是：**怎么知道这套策略相对其它策略到底好不好？**

业内常见做法是用基准（hs300/zz500）做相对收益参照——这套已经在 P1 里加了。但更直观、更刺激、也更能暴露问题的做法是：**让两个策略在完全一致的市场条件下并排跑，定期看谁赚得多、为什么赚到、风格是不是发散**。

本 change 把"两个 agent 同时跑"做成一个一等公民工作模式：

- 一边是 `claude`（由 Claude 模型决定策略 overlay），一边是 `codex`（由 OpenAI Codex CLI 决定）。
- 同样的启动资金、同样的账户、同样的成本、同样的基准、同样的调仓日。
- 各自有独立的状态、信号、订单、持仓、NAV、报告，互不污染。
- 每月有一次正式的对比环节，把双方业绩、风格、暴露、因子有效性放到一张 review 报告里，agent 据此（在人工/agent 自己驱动下）调整下个月策略。
- Dashboard 上加 3 个 tab：Claude、Codex、对比，看一眼就知道当下谁领先、领先多少、是不是只在赌同一个行业 beta。

把"竞赛"做成一等公民工作模式后，新增因子/策略改动都会立刻有量化反馈——这是当前单 agent 模式拿不到的反馈循环。

## What Changes

引入一个聚焦的"双 agent 竞赛"能力包，覆盖 4 个可独立验收的能力域：

- **公平基线锁定**：新增 `configs/competition.yaml`，把启动资金、账户构成、`top_n`、股票池、基准、交易成本、调仓与执行日期、起跑日固化下来；每个 agent 的 overlay (`configs/agents/<agent>.yaml`) 试图覆盖这些字段时加载层 raise `competition_baseline_locked:<field>`。
- **多 agent 运行时**：CLI 新增 `--agent claude|codex`，自动定位 config/data/reports 目录、走共享 cache（`data/shared/cache/`）、写各自的运行账本。`run-daily / run-weekly / report / dashboard / rebalance / execute / update-nav / init` 全部支持 agent 视角。`competition-init` 一次性初始化两侧。
- **月度对比 review**：`competition-monthly-review --month YYYY-MM` 读取两侧 `performance_summary.json` / `latest_signals.csv` / `factor_diagnostics/*.csv` / `positions.csv`，产出 `data/competition/monthly_reviews/<month>.json`（机器可读）+ `reports/competition/monthly_review_<month>.md`（人类可读），刷新 `data/competition/leaderboard.csv` 滚动榜单。
- **聚合 dashboard（三 tab）**：新增 `competition-dashboard` 命令，把每侧的 dashboard 渲染为 fragment，再装到 `reports/competition/dashboard.html`，三 tab：Claude / Codex / 对比；对比 tab 渲染双线 NAV、关键指标横向对比表、持仓重叠度、滚动战绩、月度报告链接列表。

新增/修改文件清单：

```
configs/competition.yaml                    # 新增（baseline）
configs/agents/claude.yaml                  # 新增（overlay）
configs/agents/codex.yaml                   # 新增（overlay）
stock_analyze/competition.py                # 新增（加载/锁/路径解析）
stock_analyze/monthly_review.py             # 新增
stock_analyze/dashboard_aggregator.py       # 新增
stock_analyze/cli.py                        # 修改（--agent + 3 条子命令）
stock_analyze/reporting.py                  # 修改（generate_dashboard 增 mode=fragment）
stock_analyze/store.py                      # 修改（共享 cache 目录支持）
AGENTS.md                                   # 新增（Codex 入口文档）
docs/competition-runbook.md                 # 新增（人类运维手册）
deploy/systemd/stock-analyze-{claude,codex}-{daily,weekly}.{service,timer}  # 新增
deploy/systemd/stock-analyze-monthly-review.{service,timer}                 # 新增
tests/test_competition.py                   # 新增
tests/test_monthly_review.py                # 新增
tests/test_dashboard_aggregator.py          # 新增
```

不删任何现有文件。单 agent 命令（不带 `--agent`，用现有 `--config/--data-dir/--reports-dir`）继续可用，不会被破坏。

## Capabilities

### New Capabilities

- `competition-baseline-fairness`：共享 baseline 加载、深合并 overlay、锁字段强制、`competition-init` 双侧初始化。
- `multi-agent-runtime`：`--agent` CLI、agent-namespaced 状态/报告目录、共享 cache、单 agent CLI 向后兼容。
- `monthly-comparison-review`：月度对比 JSON/MD、滚动 leaderboard、持仓重叠度与日收益相关性、风格暴露差异。
- `multi-agent-dashboard`：fragment 渲染、三 tab 聚合 dashboard、对比 tab 关键内容。

### Modified Capabilities

- 无（不破坏既有 spec；新需求作为补充）。

## Impact

- **代码影响**：3 个新模块 + 3 个既有模块的小幅扩展。无破坏性 API 变化。
- **配置影响**：`configs/strategy_v1.yaml` 仍可作为单 agent 路径使用；竞赛模式走 `competition.yaml + agents/*.yaml`。
- **数据/文件影响**：新增 `data/shared/`、`data/claude/`、`data/codex/`、`data/competition/`、`reports/claude/`、`reports/codex/`、`reports/competition/`。旧 `data/` 默认仍由单 agent 写入；竞赛模式下旧目录留空或被作为兼容路径。
- **依赖影响**：无新增第三方依赖。
- **文档影响**：`AGENTS.md`（仓库根）+ `docs/competition-runbook.md`。
- **不在本次范围**：自动学习模式（agent 自动产生 config patch 并应用）。Phase 2 单独立 change `enable-monthly-config-evolution` 跟踪。
