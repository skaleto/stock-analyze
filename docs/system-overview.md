# Stock Analyze · 系统总览

> 一篇看完就懂的入口文档。读完这一份就大致知道：项目在干什么、由哪些模块组成、数据怎么流、两个 agent 怎么协作、dashboard 在哪儿看什么、自己什么时候要动手。

---

## 1. 这是什么

**Stock Analyze** 是一个 A 股**纸面（paper trading）多因子策略系统**，专门用来：

- 用公开数据（AkShare / Baostock 等）每周生成 A 股选股信号。
- 在"下一交易日开盘价 + 滑点 + 佣金 + 印花税"的保守口径下**模拟成交**，更新模拟净值。
- 让 **Claude 与 Codex 两个 agent**在完全相同的市场条件、启动资金、交易成本下**各跑各的策略**，每月对比成绩，刺激彼此优化。
- 把所有过程在一个本地 dashboard 上可视化。

**它不是**：

- 不接券商，不下真单。所有"成交"都是模拟。
- 不构成投资建议。
- 不调任何 LLM API。Agent 思考全部在你自己的开发机上用 Claude Code / Codex CLI 完成。
- 不是回测系统。当前只有**前向模拟**（today onwards）；历史回测留给后续 change。

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│ ECS (Linux + systemd)                                        │
│  · Mon-Fri 17:25 prepare-market-data（共享数据拉取）         │
│      → ExecStartPost 触发两 agent daily 并行（--offline）    │
│  · Sat 10:00 weekly-trigger（复用周五 cache）                │
│      → ExecStartPost 触发两 agent weekly 并行（--offline）   │
│  · 每月 1 号跑 monthly-review + referee/apply + dashboard    │
│  · 不调 LLM API；只产数据 + 输出"待 agent 看的任务包"        │
└──────────────────────────────────────────────────────────────┘
                            │
                            │  scripts/sync-from-ecs.sh (rsync)
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 本地开发机                                                    │
│  · 周六上午：sync → 在 Claude Code 跑 /weekly-review claude  │
│             同时在 Codex CLI 让 codex 跑 weekly review       │
│             → agent 写笔记到 data/<agent>/notes/             │
│             → scripts/sync-to-ecs.sh 推回并刷新 dashboard    │
│  · 月度同上 + /monthly-strategy <agent> 产 proposal JSON     │
│  · sync-to-ecs 默认触发 ECS 裁判，只应用 approved patch       │
└──────────────────────────────────────────────────────────────┘
                            │
                            │  git push origin main
                            ▼
                  ┌─────────────────────┐
                  │ GitHub (origin/main)│
                  └─────────────────────┘
                            ↑
                            │  git pull / rsync (ECS 更新代码)
                            │
                          ECS 用已应用 overlay 跑下一周期
```

三方角色：

| 谁 | 干什么 | 安全边界 |
| --- | --- | --- |
| **ECS** | 自动跑数、出 dashboard/briefings，并应用 referee-approved config patch | systemd timer + sync 后置命令 |
| **本地 Claude Code** | claude 视角分析与提案 | 只动 `data/claude/notes/` 与 `data/claude/proposals/` |
| **本地 Codex CLI** | codex 视角分析与提案 | 只动 `data/codex/notes/` 与 `data/codex/proposals/` |
| **你（人）** | 查看 dashboard、处理 `needs_human`、决定是否回滚或暂停 | 唯一能改 `configs/competition.yaml` 的角色 |

---

## 3. 目录结构

```
configs/
  competition.yaml          # 共享基线：账户、成本、调仓日、起跑日（锁字段集合）
  strategy_v1.yaml          # 老的单 agent 入口（兼容保留）
  preset_quality_low_vol.yaml # 备用 preset 演示
  agents/
    claude.yaml             # claude 策略 overlay（因子/控制/过滤）
    codex.yaml              # codex 策略 overlay

stock_analyze/              # Python 包
  cli.py                    # 所有 CLI 子命令入口
  competition.py            # baseline + overlay 加载与锁字段校验
  config.py                 # 单 agent 配置加载与 v1→v2 迁移
  data_provider.py          # AkShare/Baostock 数据接口 + 缓存 + 降级
  strategy.py               # 信号生成主流程
  factor_pipeline.py        # winsorize → z-score → 行业中性化 → 加权
  portfolio_controls.py     # 行业上限、持仓缓冲、max_holding_days
  simulator.py              # 模拟成交、NAV 更新、订单生命周期
  performance.py            # 年化/Sharpe/Sortino/超额/IR/换手/成本
  diagnostics.py            # 因子覆盖率 + 前向 RankIC
  run_ledger.py             # 运行账本 + 配置快照
  monthly_review.py         # 月度对比 review
  dashboard_aggregator.py   # 三 tab 聚合 dashboard
  reporting.py              # 单 agent dashboard + 周报 + 笔记 / 提案面板
  agent_briefing.py         # 周/月任务包 markdown 生成
  store.py                  # CSV/JSON 持久化

deploy/systemd/             # 8 个 .service / .timer 单元
docs/                       # 运维、总览、规划文档
openspec/                   # 所有 OpenSpec change 记录
scripts/                    # ECS↔本地 rsync 脚本
.claude/commands/           # Claude Code slash command 模板
tests/                      # 67 个单元测试

data/                       # 运行时产物（gitignored）
  shared/                   # 两侧共用的 AkShare 缓存与 data_health.json
  competition/              # 月度对比、leaderboard、competition_metadata
  claude/                   # claude 自己的 state/orders/positions/nav/...
    notes/                  # agent 周/月笔记
      briefings/            # ECS 自动生成的任务包（agent 只读）
    proposals/              # agent 月度策略提案
    factor_runs/            # 每周因子快照
    factor_diagnostics/     # 覆盖率 + 前向 IC 累计
    configs/                # 历史 config_hash → 完整 config snapshot
  codex/                    # codex 同结构

reports/                    # 渲染产物（gitignored）
  claude/                   # claude 的 dashboard.html、dashboard_fragment.html、weekly_report.md
  codex/                    # codex 同上
  competition/              # 聚合 dashboard 与月度对比 markdown
```

---

## 4. 数据流

### 4a. 每日（周一到周五）

```
T 日 17:25 (ECS systemd timer)
  ┌─ stock-analyze-market-data.service  ← 唯一允许打外网的进程
  │    python3 -m stock_analyze prepare-market-data
  │    └─ trading_calendar / spot / index_constituents / preselect
  │       └─ ThreadPoolExecutor(5) 并发拉每只候选的 5 个接口
  │       benchmark_close(000300) + benchmark_close(000905)
  │       写 data/shared/cache/*.csv + market_snapshot_<date>.json
  │    ExecStartPost (ExecStart 成功后)：
  │       └─ systemctl start --no-block stock-analyze-claude-daily.service
  │       └─ systemctl start --no-block stock-analyze-codex-daily.service
  │
  ├─ ~17:35 stock-analyze-claude-daily.service (--offline)
  │    └─ execute_due_orders → 把 T-1 的待执行单按 T 日开盘价模拟成交
  │       update_nav         → 按 T 日收盘价更新净值
  │       compute_pending_forward_ic → 补算 5 日前向 RankIC
  │       generate_dashboard → 刷 reports/claude/dashboard.html
  │       (任何 cache miss → raise CacheMiss → service failed)
  └─ ~17:35 stock-analyze-codex-daily.service (--offline)
       └─ 同上，读同一份 cache，两 agent 看到字节级相同的原始数据
```

执行规则保守：

- 停牌、买入涨停、卖出跌停 → 订单保留 `pending` + 写 `unfilled_reason`。
- T+1：当日买入今日不可卖。
- 现金不足或可卖股不足 → 部分成交 + 残单保留。
- 无可见行情 → 不成交。

### 4b. 每周六（信号日，复用周五 cache）

```
Sat 10:00 CST (ECS systemd timer)
  ┌─ stock-analyze-weekly-trigger.service  ← /bin/true，不再次拉数据
  │    ExecStartPost：
  │       └─ systemctl start --no-block stock-analyze-claude-weekly.service
  │       └─ systemctl start --no-block stock-analyze-codex-weekly.service
  │
  ├─ stock-analyze-claude-weekly.service (--offline，读周五 cache)
  │    run-weekly --offline
  │    └─ generate_rebalance_orders
  │         · 从 cache 取股票池（hs300 + zz500 共 ~800 只）
  │         · 跑因子流水线（winsorize → z-score → 行业中性化 → 归一化加权）
  │         · 应用组合控制（行业上限 / 持仓缓冲 / max_holding_days）
  │         · 选前 50 名 × 2 账户 = 100 只目标持仓
  │         · 对照当前持仓 diff 出买卖订单 → 写 pending_orders.json (execute_on=下周一)
  │       update_nav
  │       compute_pending_forward_ic
  │       generate_weekly_report → reports/claude/weekly_report.md
  │       generate_dashboard
  │       build_weekly_briefing → data/claude/notes/briefings/<sat-date>-weekly.md  ← agent 待办
  └─ stock-analyze-codex-weekly.service (--offline) 同理
```

下周一 daily 跑时，execute_due_orders 把周六生成的订单按周一开盘价成交。

### 4c. 每月 1 号

> 由 OpenSpec change `enable-llm-direct-strategy-evolution` 实施：referee
> 退化为锁字段守卫，LLM 在本地直接改 yaml + 写 evolution_log，无 ECS 端 apply。

```
09:00 CST / 01:00 UTC ECS
  competition-monthly-review --month <prev>
    └─ compute_review(month, [claude, codex])
       · 各 agent 年化/Sharpe/IR/换手/成本/win rate
       · 比较：胜方、累计差、持仓重叠度 (Jaccard)、日收益相关性
       · 共同因子驱动 / 分歧因子驱动
    └─ write_review
       · data/competition/monthly_reviews/<month>.json
       · reports/competition/monthly_review_<month>.md
       · data/competition/leaderboard.csv（upsert）
    └─ build_monthly_briefing for each agent
       · data/<agent>/notes/briefings/<month>-monthly.md  ← agent 月度待办
       · 含 "对手 overlay 摘要" + "对手历史改动(近 3 个月)" 两段

01:10 competition-dashboard
  生成三 tab 聚合页 reports/competition/dashboard.html
```

ECS 端不再跑 `agent-judge-proposals` / `agent-apply-approved-proposals` —— 这两个命令已经在源码里删除。

### 4d. 本地分析闭环（你 + agent CLI）

```
周六上午 / 月初
  ./scripts/sync-from-ecs.sh --exclude-cache
    └─ 拉 data/、configs/、reports/ 到本地

  Claude Code:  /weekly-review claude   (or  /monthly-strategy claude 2026-05)
  Codex CLI:    do weekly review for codex (or do monthly strategy for codex)
    └─ agent 自己读 CLAUDE.md / AGENTS.md → 找最新 briefing → 写笔记/演化
    └─ 周度只写 markdown 笔记
    └─ 月度直接改 configs/agents/<agent>.yaml + 写 evolution_log + evolution_diff
        + 追加 config_evolution.csv（由 evolution_writer.write_evolution 原子化执行）
    └─ 跑 `validate-overlay --agent <agent>` 通过

  ./scripts/sync-to-ecs.sh
    └─ 推 data/<agent>/notes/、data/<agent>/evolution_log/、data/<agent>/evolution_diff/、
       data/<agent>/config_evolution.csv、configs/agents/<agent>.yaml、_history/ 回 ECS
    └─ 远端仅刷 dashboard，无 referee/apply 步骤

ECS:
  dashboard 显示新的演化时间线（month / from→to hash / diff 摘要 / evolution_log 链接 /
  当月 + 次月实际收益）；新 overlay 进入下一周期的 daily/weekly 运行
```

月度策略变化的好坏由 LLM 自负——守卫只确认 schema 合法、不踩 baseline 锁字段、factor 名在白名单、weight 在 `[0, 1]`。

---

## 5. 公平基线与 overlay

竞赛的公平性靠**两层配置**保证：

### 5a. `configs/competition.yaml`（不可改）

定义了**所有保证可比性的字段**：起跑日、初始资金 100 万、双账户各 50 万、`top_n=50`、股票池（hs300/zz500）、基准（000300/000905）、交易成本（佣金 0.03% + 印花税 0.05% + 滑点 0.05% + 单股上限 5%）。

`stock_analyze/competition.py` 加载时，如果发现 agent overlay 试图覆盖以下字段会 `raise CompetitionBaselineLocked`：

- `competition_id`、`start_date`
- `initial_cash`、`accounts.*.cash`、`accounts.*.top_n`、`accounts.*.scope`、`accounts.*.benchmark`
- `schedule.execution`、`schedule.signal_day`
- `trading.*`（所有交易成本相关）

### 5b. `configs/agents/<agent>.yaml`（agent proposal 可改）

每个 agent 通过月度 proposal 影响以下 overlay 字段；实际写回由 referee-approved apply 命令执行：

- `factors`：哪些因子、各自权重、方向 (`high` / `low`)
- `factor_processing`：winsorize 上下分位、是否行业中性化、最小因子覆盖率
- `portfolio_controls`：单行业上限、持仓缓冲、最大持有天数
- `filters`：最小市值、最小成交额、必需字段、回退字段

只允许出现 `agent_id`、`strategy_id`、`name`、`factors`、`factor_processing`、`portfolio_controls`、`filters` 七个顶层键；其它键直接被拒。

当前两个 agent 的差异化设定：

- **claude**：价值 + 质量 + 动量（PE/PB/ROE/毛利率/资产负债率/20 日动量/60 日动量），单行业 30%，持仓 buffer 50%。
- **codex**：质量 + 低波 + 股息（ROE/毛利率/资产负债率/60 日动量/低波 60 日/股息率），单行业 25%，持仓 buffer 60%。

---

## 6. 因子流水线（每周六跑一次）

```
原始候选池
  ↓ 预筛（PE 正、最小成交额、最小市值、必需字段非空、ST 排除）
~250 只候选
  ↓ 对每个因子分别做：
    1) winsorize 在 1% / 99% 处夹边（防止极端值主导）
    2) z-score 标准化（让权重在同一量纲）
    3) 行业内 demean（行业中性化；缺失行业归 "未分类" 桶）
    4) 乘方向符号 (high → +1, low → -1)
    5) 乘配置权重
  ↓ 按可用因子重新归一权重（缺失因子按比例分摊给其他因子）
  ↓ 覆盖率 < min_factor_coverage 的股票被剔除并写 insufficient_factor_coverage warning
  ↓ 综合分 = Σ (有效因子 z-score × 方向 × 归一权重)
按综合分降序排列
```

每周这份完整明细写入 `data/<agent>/factor_runs/<run_id>.csv`，列含：原值 / winsorize 后 / z-score / neutralize / 方向 / weight / contribution。可重现：`score == sum(contribution per code)`。

---

## 7. 组合构建控制

```
按综合分降序遍历候选
  ↓ 单行业上限：累计某行业权重 ≥ max_industry_weight 时跳过该股
  ↓ 持仓缓冲：当前持有但排名落到 [top_n, top_n × (1 + hold_buffer_pct)] 区间内的保留
  ↓ max_holding_days：持有超过该天数的强制重新评估
凑齐 top_n=50 只 × 2 账户 = 100 只目标持仓
  ↓ build_target_orders
单股目标市值 = min(账户总值 / top_n, 账户总值 × max_single_weight)
                = min(2%, 5%) × 账户总值 = 2%
按 100 股整数倍截尾；现金不足时减档
生成 buy / sell 订单 → 写 pending_orders.json，等下个交易日按开盘价模拟成交
```

---

## 8. 绩效与归因

每次 `update_nav` 后由 `performance.compute_account_performance` 汇总：

| 指标 | 口径 |
| --- | --- |
| 累计收益 | `total_value_T / total_value_0 − 1` |
| 年化收益 | 日收益均值 × 252 |
| 年化波动 | 日收益样本标准差 × √252 |
| Sharpe | (年化收益 − risk_free_rate) / 年化波动 |
| Sortino | (年化收益 − risk_free_rate) / 年化下行半标准差 |
| 最大回撤 | NAV 序列回撤峰谷比 |
| 最大回撤天数 | 自最高点到对应最低点的自然日数 |
| 累计超额 | `Π(1+r_account) − Π(1+r_benchmark)` |
| 年化超额 | 日超额均值 × 252 |
| 跟踪误差 | 日超额标准差 × √252 |
| 信息比率 | 年化超额 / 跟踪误差 |
| 周换手率 | (本周 buy 名义 + sell 名义) / 周初组合市值 |
| 成本占比 (bps) | 累计 (commission + stamp_tax + slippage) / 累计成交金额 × 10000 |
| Win Rate | FIFO 配对的 round-trip 中 pnl > 0 的比例 |

数据不足时（NAV < 2 个点）相关字段为 `null`，dashboard 显示 `-`。

---

## 9. 因子诊断

### 9a. 覆盖率

每次 `run-weekly` 在 `data/<agent>/factor_diagnostics/coverage.csv` 追加每个因子的 `coverage_pct, missing_count, mean, p5, p50, p95, std`，dashboard 渲染最近 12 周的覆盖率热力图，低于阈值的格子标红。

### 9b. 前向 5 日 RankIC

当 NAV 历史包含某 `signal_date` 之后 ≥ 5 个交易日的实际收益时，按当时各股票的 z-score 与 T 到 T+5 实际收益做 **Spearman rank IC**（不依赖 scipy，自实现），写入 `data/<agent>/factor_diagnostics/forward_ic.csv`。不足时写 `ic_status=insufficient_history` 占位，后续到达足够历史时自动回填。

Dashboard 渲染最近 12 周的 forward IC 折线，让你一眼看出哪些因子在最近样本里还有解释力、哪些已经衰减。

---

## 10. 运行账本与配置快照

```
每次 CLI 命令进入 RunLedger.run(command):
  · append data/<agent>/runs.csv 一行 status=running
  · try { 实际跑命令 }
    finally: append 一行 status=success / failed + duration_ms + error_summary
  · 计算 config_hash = sha256(canonical_json(config))[:12]
  · 若 data/<agent>/configs/<hash>.json 不存在，写入完整 config snapshot
  · code_version = .git/HEAD 短 SHA（不依赖外部 git 二进制）
```

这意味着每次 dashboard 显示的任何数据都能反向追溯到：哪次运行、哪份 config、哪个 commit。出现"昨天的结果今天突然变了"时，对照 hash 就能定位是 config 改了还是数据源刷新。

---

## 11. Dashboard 三 tab + 简化版

竞赛 dashboard 一次 `competition-dashboard` 同时产出**专业版**和**简化版**两份 HTML,共享 `data/*` 输入,渲染层不同:

```
reports/competition/
├── dashboard.html        ← 专业版 (3 tab,~270 KB)
├── simple.html           ← 新手简化版 (~12 KB)
└── simple/
    ├── claude.html       ← Claude 单 agent 简化版
    └── codex.html        ← Codex 单 agent 简化版
```

`serve-dashboard` HTTP 路由别名:

```
GET /                          → reports/competition/simple.html  (默认新手)
GET /simple.html               → reports/competition/simple.html
GET /simple/claude.html        → reports/competition/simple/claude.html
GET /simple/codex.html         → reports/competition/simple/codex.html
GET /pro.html                  → reports/competition/dashboard.html  (别名)
GET /competition/dashboard.html → reports/competition/dashboard.html  (向后兼容)
GET /claude/dashboard.html     → reports/claude/dashboard.html       (单 agent fragment 页)
```

### 专业版 (`dashboard.html`,3 tab)

```
┌─[ Claude ]─[ Codex ]─[ 对比 ]──────────────────────────┐
│                                                       │
│ Claude tab(嵌入 reports/claude/dashboard_fragment.html)│
│  · 4 张账户卡片(最新资产)                              │
│  · 净值曲线                                            │
│  · 绩效解释 4×3 卡片矩阵(年化/Sharpe/IR/成本…)         │
│  · 本期选股信号                                        │
│  · 因子贡献均值                                        │
│  · 待执行订单                                          │
│  · 候选股价格走势                                      │
│  · 因子诊断(覆盖率热力图 + 前向 IC 折线)               │
│  · 当前持仓 / 近期交易 / 数据源 / 最近运行             │
│  · 近期 agent 笔记(最近 5 篇 markdown 折叠)            │
│  · 策略演进时间线(每月 proposal + 当月与次月实际收益)  │
│  · 本期分析任务包(最新 weekly + monthly briefing)      │
│                                                       │
│ Codex tab 同结构                                       │
│                                                       │
│ 对比 tab                                               │
│  · 4 张卡片(双方累计 / 累计差 / 最近一月胜方)          │
│  · 累计净值双线                                        │
│  · 9 行横向指标对比表                                  │
│  · 持仓重叠条(独占 / 共有 / 独占 三段宽度)             │
│  · 滚动战绩(按月色块)                                  │
│  · 月度报告链接列表                                    │
│  · 本周双方观察对照(两侧最新周笔记并列)                │
└───────────────────────────────────────────────────────┘
```

CSS `:target` 切 tab,纯静态,无 JS 框架。

### 简化版 (`simple.html`)

面向新手,只 8 个 section,文件 ≤ 80 KB:

```
[简化版] [专业版] [策略演进]                       ← 顶部 tab bar
👤 我的账户         总资产 / 今日 / 本月            ← Section 1
📊 两位 AI 的成绩   Claude / Codex 各一卡片         ← Section 2
📈 净值曲线        双线 + 沪深300 + 中证500 (SVG)   ← Section 3
📦 Claude 持仓 Top10                              ← Section 4
📦 Codex 持仓 Top10                               ← Section 5
🔍 持仓重叠       两位都持有 / 仅 Claude / 仅 Codex ← Section 6
🔄 最近 5 笔模拟成交                              ← Section 7
🧭 本月策略调整摘要(可选,从 evolution_log 读)     ← Section 8
```

简化版**不包含**因子覆盖率、前向 IC、因子贡献明细、运行账本、数据源状态、agent 笔记内容、factor_runs/* — 这些只在专业版。

---

## 12. Agent CLI 分析闭环

详见 `CLAUDE.md`（Claude Code 入口）、`AGENTS.md`（Codex CLI 入口）。摘要：

- **周度**：ECS 自动生成 `data/<agent>/notes/briefings/<date>-weekly.md` → 你 sync → agent 跑 `/weekly-review claude`（或 codex 类似指令）→ agent 自己 Read 任务包 + 写 markdown 笔记到 `data/<agent>/notes/<date>-weekly-review.md` → 你 sync 回 ECS。**周度不改 config**。

- **月度**：ECS 自动生成 `data/<agent>/notes/briefings/<month>-monthly.md`（含完整月度对比 JSON 摘要 + 近 4 周笔记 + 锁字段清单）→ 你 sync → agent 跑 `/monthly-strategy claude 2026-05` → 输出 markdown 月度复盘 + 严格 JSON proposal 到 `data/<agent>/proposals/<month>-strategy.json` → 你 sync 回 → ECS 裁判写 decision → 只自动应用 `approved` patch。

每份 briefing 是**五段**结构：角色 / 数据快照 / 任务 / 输出契约 / 可选参考。agent 看到的就是一段固定模板，写什么、写到哪、不要碰什么完全明确。

安全机制：

- 锁字段清单**直接写在月度 briefing 里**，agent 提案前就知道哪些不能改。
- slash command 体内**显式禁止**修改 `configs/`、`stock_analyze/`、`tests/`、`openspec/`、对方目录等。
- 整个链路无 LLM API 调用；agent 是 Claude Code / Codex CLI 本身，由你触发。

---

## 13. 关键产物清单

| 文件 | 谁写 | 用途 |
| --- | --- | --- |
| `data/<agent>/state.json` | simulator | 账户现金 + 持仓 |
| `data/<agent>/pending_orders.json` | simulator | 待执行订单，包含 status / attempts / unfilled_reason |
| `data/<agent>/daily_nav.csv` | simulator | 按 date+account_id upsert 的净值时间序列 |
| `data/<agent>/trades.csv` | simulator | 模拟成交流水 |
| `data/<agent>/positions.csv` | simulator | 当前持仓快照（含 industry、hold_since） |
| `data/<agent>/latest_signals.csv` | simulator | 最近一期 TopN 选股表 |
| `data/<agent>/performance_summary.json` | reporting | 全套绩效指标 |
| `data/<agent>/runs.csv` | run_ledger | 每次 CLI 调用的账本 |
| `data/<agent>/configs/<hash>.json` | run_ledger | 完整 config snapshot |
| `data/<agent>/factor_runs/<run_id>.csv` | simulator | 每周因子完整明细 |
| `data/<agent>/factor_diagnostics/coverage.csv` | simulator | 每周因子覆盖率 |
| `data/<agent>/factor_diagnostics/forward_ic.csv` | diagnostics | 前向 5 日 RankIC 累积 |
| `data/<agent>/notes/briefings/*.md` | ECS（agent_briefing） | agent 待办任务包 |
| `data/<agent>/notes/*.md` | agent | 周/月分析笔记 |
| `data/<agent>/evolution_log/<YYYY-MM>.md` | agent | 月度策略演化思考记录（LLM 直接写） |
| `data/<agent>/evolution_diff/<YYYY-MM>.json` | evolution_writer | 月度策略演化结构化 diff |
| `data/<agent>/config_evolution.csv` | evolution_writer | 策略演化 / 回滚审计行 |
| `configs/agents/_history/<config_hash>.yaml` | evolution_writer | 上版 overlay 备份（每次演化前） |
| `data/competition/competition_metadata.json` | competition-init | 起跑日 / baseline_hash |
| `data/competition/monthly_reviews/<month>.json` | monthly_review | 机器可读对比 |
| `data/competition/leaderboard.csv` | monthly_review | 按月滚动战绩 |
| `data/shared/cache/*.csv` | data_provider | 公开数据缓存（两侧共用） |
| `data/shared/data_health.json` | data_provider | 数据源健康日志 |
| `reports/<agent>/dashboard.html` | reporting | 单 agent 仪表盘 |
| `reports/<agent>/dashboard_fragment.html` | reporting | 给聚合页嵌入的片段 |
| `reports/<agent>/weekly_report.md` | reporting | 中文周报 |
| `reports/competition/dashboard.html` | dashboard_aggregator | 三 tab 聚合页 |
| `reports/competition/monthly_review_<month>.md` | monthly_review | 人类可读月报 |

`data/` 与 `reports/` 全部 gitignored，不进版本控制。

---

## 14. 一周一月一年的工作节奏

| 频率 | 谁触发 | 命令 / 动作 |
| --- | --- | --- |
| Mon-Fri 17:25 | ECS systemd | `prepare-market-data`（pipeline）→ ExecStartPost 触发两 agent `run-daily --offline` 并行 |
| Sat 10:00 | ECS systemd | `weekly-trigger`（占位）→ ExecStartPost 触发两 agent `run-weekly --offline` 并行（读周五 cache，自带 briefing） |
| 周六上午 / 周末 | 你 + agent CLI | sync-from-ecs → `/weekly-review claude` + `do weekly review for codex` → sync-to-ecs |
| 每月 1 号 09:00 CST | ECS systemd | `competition-monthly-review` + `competition-dashboard`（无 referee/apply 步骤） |
| 每月 1-2 号 | 你 + agent CLI | sync-from-ecs → `/monthly-strategy claude` + `do monthly strategy for codex` → `validate-overlay` → sync-to-ecs |
| 季度 | 你 | 翻 leaderboard 与 monthly reviews，决定是否调整 baseline 或新增 OpenSpec change |
| 任意时刻 | 你 | `competition-dashboard` 刷新；`openspec list` 看变更状态 |

---

## 15. 安全边界

| 项 | 强制方式 |
| --- | --- |
| 启动资金 / 账户 / 成本 / 调仓日不可改 | `competition.load()` 锁字段；overlay 试图覆盖直接 raise |
| top_n / 股票池 / 基准 一致 | 同上 |
| agent 不能跨写对方目录 | `CLAUDE.md` / `AGENTS.md` 行为约束 + slash command 禁止条款 |
| agent 不能改 `stock_analyze/`、`configs/competition.yaml`、operating manual | 同上 |
| 月度策略演化边界 | `overlay_guard.validate` 只校验 schema + 锁字段 + factor 白名单 + weight 范围；策略好坏 LLM 自负（人类授权 2026-05-23） |
| 无 LLM API 依赖 | 整个 stack 没有任何 HTTP 调用到 anthropic.com / openai.com |
| 真单 | 不可能。代码里没有任何券商 SDK 也没有任何下单链路 |
| 敏感凭据 | 仅 `EASTMONEY_COOKIE` 环境变量；不写入仓库、配置、日志；systemd 用 EnvironmentFile 隔离权限 |

---

## 16. 限制与不在范围

- **数据**：公开接口受网络 / 风控 / 限流影响；data_provider 已加多源降级和重试，但不保证不掉数据。
- **financials**：未严格按公告日 point-in-time 截断（待 change `introduce-point-in-time-fundamentals`）。
- **历史回测**：当前是前向模拟。历史回测引擎留给 change `add-historical-backtest-baseline`。
- **历史指数成分**：用当下成分倒推历史会有幸存者偏差，回测时再补。
- **组合优化器**：未引入 CVXPY / PyPortfolioOpt。当前组合控制是规则式。
- **告警**：没有钉钉/邮件告警，全靠你看 dashboard。

---

## 17. 后续 change 路线图

按优先级建议：

1. `add-historical-backtest-baseline`：把当前规则跑 3-5 年历史，输出年化 / 超额 / 最大回撤 / 夏普 / 换手；提供样本外检验。
2. `introduce-point-in-time-fundamentals`：按公告日生效财务因子。
3. `add-research-factor-toolkit`：因子衰减、相关性、行业暴露归因、风格暴露归因。
4. `migrate-run-ledger-to-sqlite`：CSV 账本 → SQLite/DuckDB，加索引、原子写、备份。
5. `add-alerting-and-sla`：任务失败 / NAV 停更 / pending 超期 / 回撤超阈值告警。
6. `introduce-portfolio-optimizer`：在既有约束下接 CVXPY 做加权。

每个 change 都走 OpenSpec：proposal → design → tasks → specs，验证通过后实施。

---

## 18. 术语表

- **agent**：参赛策略的拥有者。当前两个：`claude`（由 Claude Code 操作）、`codex`（由 Codex CLI 操作）。
- **baseline**：`configs/competition.yaml` 中的共享公平字段。
- **overlay**：`configs/agents/<agent>.yaml` 中的 agent 自由配置。
- **briefing**：ECS 周/月自动生成给 agent 看的 markdown 任务包，位于 `data/<agent>/notes/briefings/`。
- **note**：agent 自己写的分析 markdown，位于 `data/<agent>/notes/`。
- **proposal**：agent 月度策略提案 JSON，位于 `data/<agent>/proposals/`。
- **review**：竞赛月度对比，位于 `data/competition/monthly_reviews/`。
- **leaderboard**：按月战绩 CSV，位于 `data/competition/leaderboard.csv`。
- **config_hash**：当前 overlay+baseline 合并后的 12 字符 sha256；每次 config 变化都重新计算。
- **run_id**：每次 CLI 命令调用的唯一 ID，写入 `runs.csv` 与 `factor_runs/`。
- **forward IC**：5 日前向 Spearman rank IC，因子有效性指标。
- **TopN**：每个账户的目标持仓数量。当前 `top_n=50`，双账户合计 100。

---

## 进一步阅读

- 运维细节：[docs/competition-runbook.md](competition-runbook.md)
- 单 agent 模式（老）：[docs/forward-simulation-runbook.md](forward-simulation-runbook.md)
- P1 方法学计划：[docs/quant-beginner-alignment-plan-2026-05-19.md](quant-beginner-alignment-plan-2026-05-19.md)
- 早期 model gap review：[docs/quant-model-gap-review-2026-05-18.md](quant-model-gap-review-2026-05-18.md)
- OpenSpec 变更：`openspec/changes/`
