# 共享数据流水线 · 架构详解

> 配套阅读：本目录下 `proposal.md`（动机 + 范围）、`design.md`（决策）、`tasks.md`（拆解）、`specs/*/spec.md`（验收）。这份文档是给后续看代码或运维的人读的"实物图"——它用大白话讲清楚现在长什么样、改完长什么样、为什么这么改。

---

## 一、核心概念白话版

下面这些词在 codebase / openspec / runbook 里反复出现。先把每个词在本项目里的**具体所指**说清楚，后面看图就不卡壳。

### 1.1 agent / 模型 / 账户

- 仓库里只有**一套**策略代码（`stock_analyze/*.py`）。
- 跑这套代码时通过 `--agent claude` 或 `--agent codex` 传一个**身份标签**，CLI 入口 (`stock_analyze/cli.py`) 据此决定读哪份策略 overlay、写哪个数据目录、写哪个 runs.csv。
- 所以"claude agent"和"codex agent"不是两套程序，是**同一程序跑两次**，各自拿不同的策略参数、写各自的账户文件。
- 两个 agent 起始资金 50 万（`competition.yaml.initial_cash`），互不知道对方的持仓与状态，只能通过月度复盘 (`data/competition/monthly_reviews/<month>.json`) 看到对方的"成绩单摘要"。

### 1.2 工作日 daily vs 周末 weekly

| 维度 | daily | weekly |
| --- | --- | --- |
| 频率 | 工作日每天 1 次 | 每周 1 次 |
| 现在什么时候跑 | Mon-Fri 17:30-17:35 | Fri 17:40-17:45 |
| 改完什么时候跑 | Mon-Fri 17:25 触发，~17:35 启动 | **Sat 10:00** |
| 主任务 | ① 更新今天 NAV ② 执行上周五排好的订单（只有周一会有订单到期） | ① 重算因子 ② 选下周持仓 ③ 生成下周一执行的订单 |
| 视角 | 只看自己账户 | 看全市场候选 |
| 会下新单吗 | **不会**——只 execute "已经躺在 pending_orders 里到期的订单" | **会**——build_signals → build_target_orders 写入 pending_orders |
| 网络调用量 | 小（持仓股票最新价 + 基准收盘价 ≈ 10 次） | 大（全候选池 ~250 只 × 5 个接口 ≈ 1250 次） |

一句话：**daily 是收账员，weekly 是研究员。**

### 1.3 NAV（净资产）

- 全称 Net Asset Value。
- 公式：`持仓股票数量 × 当日收盘价 之和 + 现金余额`。
- 每个工作日 daily 跑完会在 `data/<agent>/daily_nav.csv` 加一行（带 benchmark_close 一同写入，用于算超额收益）。
- 这条曲线是 dashboard "收益曲线"图的数据源，也是算 Sharpe / Sortino / max drawdown 的输入。
- 周六周日不开盘，NAV 不更新。

### 1.4 "共享 cache" 到底是啥

- 物理上：`data/shared/cache/` 目录下一堆 csv 文件，比如 `history_000001_20260522_220.csv`、`spot_20260522.csv`。
- **不是内存 cache，不是 redis，就是磁盘文件**。
- 当前代码里 `AkshareProvider` 有 9 个 fetch 方法：
  - 4 个真 cache-first：`basic_info` / `valuation_metrics` / `financial_metrics` / `dividend_yield` —— 先读磁盘 csv，命中即返回，miss 才打网络。
  - 5 个假 cache-first：`spot` / `index_constituents` / `price_history` / `trading_calendar` / `benchmark_close` —— **每次先打网络**，只在网络全失败时读磁盘 csv 兜底。`benchmark_close` 连 csv 都不写。
- 后果：`data/shared/cache/` 名字是"shared"，happy path 上两个 agent 还是各打各的网络，"共享"的只是"灾备时的回退磁盘文件"。
- 本次改造的核心就是把这 5 个方法**也变成真 cache-first**，并且 `benchmark_close` 加上磁盘缓存。

### 1.5 baseline / overlay / 锁字段

- baseline = `configs/competition.yaml`，写明两个 agent 的共同起点（账户初始现金、佣金、印花税、最大单一权重等）。
- overlay = `configs/agents/<agent_id>.yaml`，每个 agent 自己的策略参数（因子权重、行业上限、候选池上限等）。
- 加载顺序：先读 baseline → 再用 overlay 字段覆盖。
- **锁字段**：baseline 里某些字段（`initial_cash`、`schedule.execution`、`trading.*` 等）是"公平性参数"，overlay **不允许**覆盖。loader 在加载时检测到 overlay 试图覆盖锁字段，会抛 `CompetitionBaselineLocked: <field>` 异常并退出。
- 锁字段保护机制实现在 `stock_analyze/competition.py`。

### 1.6 signal / proposal / decision

这三个词容易混。

| 名词 | 是什么 | 谁写 | 写到哪 |
| --- | --- | --- | --- |
| signal | 每周 weekly 跑完得到的"候选股票打分表"——50 只股票，每只一个综合分 + 因子分明细 | weekly run 自动写 | `data/<agent>/signals/<YYYY-MM-DD>.csv` |
| proposal | agent（人类操作员通过 Claude Code / Codex CLI）每月初写的"策略调整建议"——JSON 格式，含要 patch 的 overlay 字段 + 理由 | 人通过 CLI 写 | `data/<agent>/proposals/<YYYY-MM>-strategy.json` |
| decision | 仲裁器（`stock_analyze/proposal_judge.py`）对 proposal 的裁决——通过 / 驳回 / 部分通过 + 理由 | 自动跑的 referee | `data/<agent>/decisions/<YYYY-MM>-decision.json` |

流程：每月 1 号 → 人写 proposal → referee 跑 `agent-judge-proposals` 出 decision → 跑 `agent-apply-approved-proposals` 把通过的 patch 真正写进 `configs/agents/<agent>.yaml`。

### 1.7 factor pipeline

- factor = 一个数值指标，比如 PE（市盈率）、ROE（净资产收益率）、momentum_60（60 日动量）。
- pipeline 步骤（实现在 `stock_analyze/factor_pipeline.py`）：
  1. 拿到每只股票每个 factor 的原始数值
  2. **winsorize**：把超过上下分位数的极端值拉回到分位数边界（防止 1 只爆雷股污染整批）
  3. **z-score**：把每个 factor 标准化成 "我比平均高几个标准差"
  4. **行业中性化**：减去本行业平均 z-score，避免分数高只是因为踩了热门行业
  5. **加权合并**：按 overlay 里的 `factors.<name>.weight` 加权求和，得到每只股票一个 composite score
  6. score 排序取 top_n（baseline 锁定为 50）作为下周持仓候选

### 1.8 谁在哪天下单

A 股周一到周五开盘，周末不开盘。所以：

```
周一 09:30 开盘   ← daily run 当晚才会跑，所以"开盘下单"实际是上周五排好的订单在周一以开盘价 / 当日 vwap 成交
周二~周四         ← daily 跑，但没有新订单到期，也没下新订单 → 只更新 NAV
周五             ← daily 跑（NAV）+ weekly 跑（写下周一执行的订单到 pending_orders.csv）
周六周日          ← 不开盘，无活动；本次改造后周六 10:00 跑 weekly
```

**关键**：weekly 写的订单不会立刻成交。它写到 `data/<agent>/pending_orders.csv`，标记 `execute_on=<下周一>`。下周一 daily 跑时，调用 `execute_due_orders` 才把它们打掉，写进 `trades.csv`。

---

## 二、当前架构（变更前）

### 2.1 一周时序图

```
 时刻 (CST)   周一        周二        周三        周四        周五                  周六  周日
 ─────────────────────────────────────────────────────────────────────────────────────────────
 09:30      开盘
 15:00      收盘
 17:30      claude-daily  claude-daily claude-daily claude-daily claude-daily          -    -
            ├ 打网络拉持仓+基准（10 次接口）                                    ↘
            ├ 更新 NAV，写 daily_nav.csv                                       │
            └ Mon 执行上周五写的订单 → trades.csv                                │
 17:35      codex-daily   codex-daily  codex-daily  codex-daily  codex-daily          -    -
            ├ 又打一遍同样的网络（10 次接口）          ← 重复浪费              │
            └ 同上                                                          │
 17:40      -             -            -            -            claude-weekly         -    -
                                                                 ├ 打网络拉全候选池（~1250 次接口）
                                                                 ├ 算因子，写 signals
                                                                 └ 写下周一执行的订单
 17:45      -             -            -            -            codex-weekly          -    -
                                                                 ├ 又打一遍全候选池（~1250 次接口）  ← 又重复
                                                                 └ 同上
```

一周外部 API 调用次数：
- daily：5 天 × 2 agent × 10 次 = **100 次**
- weekly：1 次 × 2 agent × 1250 次 = **2500 次**
- 合计 ≈ **2600 次**，其中一半是重复打

### 2.2 当前组件 + 数据流

```
                ┌─────────────────────────────────────────────────────────┐
                │              外部公开数据源                              │
                │   AkShare / eastmoney / tencent / sina / baostock        │
                └────────────────────┬────────────────────────────────────┘
                                     │
                  ┌──────────────────┴──────────────────┐
                  │                                     │
                  ↓ (打两遍)                            ↓ (打两遍)
        ┌─────────────────┐                   ┌─────────────────┐
        │ AkshareProvider │                   │ AkshareProvider │
        │   (claude 实例)  │                   │   (codex 实例)   │
        └─────────────────┘                   └─────────────────┘
                  │                                     │
                  │ 9 个 fetch 方法                       │
                  │  · spot              ← 假 cache-first│
                  │  · index_constituents← 假 cache-first│
                  │  · price_history     ← 假 cache-first│
                  │  · trading_calendar  ← 假 cache-first│
                  │  · benchmark_close   ← 无 cache      │
                  │  · basic_info        ← 真 cache-first│
                  │  · valuation_metrics ← 真 cache-first│
                  │  · financial_metrics ← 真 cache-first│
                  │  · dividend_yield    ← 真 cache-first│
                  ↓                                     ↓
        ┌─────────────────────────────────────────────────────────┐
        │             data/shared/cache/                          │
        │   spot_<date>.csv / history_<code>_<date>_<n>.csv ...   │
        │   (两 agent 写同一目录，但因为大多假 cache-first，写完     │
        │   就被对方的网络结果覆盖，没起到去重作用)                │
        └─────────────────────────────────────────────────────────┘
                  │                                     │
                  ↓                                     ↓
        ┌─────────────────┐                   ┌─────────────────┐
        │ build_signals   │                   │ build_signals   │
        │ build_orders    │                   │ build_orders    │
        │ execute_orders  │                   │ execute_orders  │
        └─────────────────┘                   └─────────────────┘
                  │                                     │
                  ↓                                     ↓
        ┌─────────────────┐                   ┌─────────────────┐
        │ data/claude/    │                   │ data/codex/     │
        │  · daily_nav    │                   │  · daily_nav    │
        │  · trades       │                   │  · trades       │
        │  · pending_ord  │                   │  · pending_ord  │
        │  · signals/     │                   │  · signals/     │
        │  · runs.csv     │                   │  · runs.csv     │
        └─────────────────┘                   └─────────────────┘
```

### 2.3 当前架构的问题

**问题 1：两个 agent 看到不同的"原始数据"**

5/20 真实事件：
- 17:30 claude-daily 跑：拿到 hs300 benchmark_close = 5/19 收盘（陈旧——数据源还没刷出 5/20 收盘）
- 17:35 codex-daily 跑：拿到 hs300 benchmark_close = 5/20 收盘（新鲜——数据源刷出来了）
- 同一基准两个 agent NAV 算出来的超额收益不同步，影响 5/20 当天的对比公平性。

5/21 印证：
- 同一时段两次跑 zz500 收盘价，第一次 8656.31，第二次 8419.84，差 -2.7%。
- 证明早期时段拿到的是上一交易日的残值伪装成今天的数据。

**问题 2：浪费一半网络调用**

两个 agent 几乎打一模一样的接口，特别是 weekly 时全候选池 1250 次接口打两遍 = 2500 次。是接口能承受，但是**没必要**。

**问题 3：cache 命名误导**

`data/shared/cache/` 字面意思是"共享缓存"，但实际只在网络全失败时被读取。新来的人看代码会以为有真正的共享，调试时困惑。

**问题 4：失败模式不明显**

如果 claude-daily 拉到陈旧数据、codex-daily 拉到新鲜数据，dashboard 上的 NAV 曲线会出现 ~0.1% 量级的莫名其妙的"超额"或"落后"，但 service 都报 success，没人会注意到。

---

## 三、目标架构（变更后）

### 3.1 一周时序图

```
 时刻 (CST)   周一            周二            周三            周四            周五          周六                周日
 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
 09:30      开盘
 15:00      收盘
 17:25      market-data     market-data     market-data     market-data     market-data    -                  -
            (prepare task)  (prepare task)  (prepare task)  (prepare task)  (prepare task)
            ├ 打网络一次性拉全候选池（~1250 次接口）
            ├ 写 data/shared/cache/*.csv
            └ 写 market_snapshot_<date>.json
                ↓ ExecStartPost
 17:30      claude-daily    claude-daily    claude-daily    claude-daily    claude-daily    -                  -
 (并行)      ├ --offline 启动
            ├ AkshareProvider(offline=True)
            ├ 9 个 fetch 方法全部 cache-first
            ├ 任何 cache miss → raise CacheMiss → service failed
            └ Mon 执行上周六写的订单 → trades.csv
            codex-daily     codex-daily     codex-daily     codex-daily     codex-daily     -                  -
            (与 claude-daily 同时启动，--no-block 并行)
 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
                                                                                            10:00 weekly-trigger
                                                                                            (no fetch)
                                                                                              ↓ ExecStartPost
                                                                                            claude-weekly   -
                                                                                            ├ --offline 启动
                                                                                            ├ 读 周五 17:25 写的 cache
                                                                                            ├ 算因子，出 signals
                                                                                            └ 写下周一执行的订单
                                                                                            codex-weekly    -
                                                                                            (并行)
```

一周外部 API 调用次数：
- market-data：5 天 × 1 次 × 1250 次 = **6250 次**
- daily / weekly：**0 次**（offline 模式不打网络）
- 合计 ≈ **6250 次**，但**只发生一次**——没有重复，两个 agent 看到字节级相同的 cache。

> 总调用次数变高（2600 → 6250），但 happy path 网络耗时只发生在 17:25-17:35 一个十分钟窗口，且公开接口免费。换来的是"两个 agent 同源"的硬保证 + Mon-Thu 天天积累 cache 的韧性。

### 3.2 目标组件 + 数据流

```
                ┌─────────────────────────────────────────────────────────┐
                │              外部公开数据源                              │
                │   AkShare / eastmoney / tencent / sina / baostock        │
                └────────────────────┬────────────────────────────────────┘
                                     │
                                     │ (只打一遍)
                                     ↓
                        ┌────────────────────────┐
                        │ prepare-market-data    │
                        │ (新模块 market_data.py)│
                        │                        │
                        │ AkshareProvider(       │
                        │   offline=False)       │  ← 唯一允许打网络的进程
                        │                        │
                        │ ThreadPoolExecutor(5)  │
                        │ 并发拉 250 只 × 5 接口  │
                        └───────────┬────────────┘
                                    │
                                    ↓
                  ┌─────────────────────────────────────┐
                  │      data/shared/cache/             │  ← 真共享，不只是兜底
                  │   spot_<date>.csv                   │
                  │   constituents_000300_<date>.csv    │
                  │   history_<code>_<date>_220.csv     │
                  │   basic_info_<code>_<date>.csv      │
                  │   valuation_<code>_<date>.csv       │
                  │   financial_<code>_<date>.csv       │
                  │   dividend_<code>_<date>.csv        │
                  │   benchmark_<code>_<date>.csv    ← 新增日级 cache
                  │   trading_calendar_<date>.csv       │
                  ├─────────────────────────────────────┤
                  │  data/shared/market_snapshot_<date> │  ← 新增元数据
                  │   .json (fetched_at/errors/counts)  │
                  ├─────────────────────────────────────┤
                  │  data/shared/runs.csv               │  ← 新增 pipeline 自身账本
                  └─────────────────────────────────────┘
                                    │
                       ┌────────────┴────────────┐
                       │ (两 agent 读同一份)      │
                       ↓                         ↓
        ┌──────────────────────┐    ┌──────────────────────┐
        │ AkshareProvider      │    │ AkshareProvider      │
        │ (offline=True)       │    │ (offline=True)       │
        │ - 仅读 cache         │    │ - 仅读 cache         │
        │ - miss → CacheMiss   │    │ - miss → CacheMiss   │
        └──────────────────────┘    └──────────────────────┘
                  │                          │
                  ↓                          ↓
        ┌─────────────────┐         ┌─────────────────┐
        │ build_signals   │         │ build_signals   │
        │ build_orders    │         │ build_orders    │
        │ execute_orders  │         │ execute_orders  │
        └─────────────────┘         └─────────────────┘
                  │                          │
                  ↓                          ↓
        ┌─────────────────┐         ┌─────────────────┐
        │ data/claude/    │         │ data/codex/     │
        └─────────────────┘         └─────────────────┘
```

### 3.3 新增 / 修改 / 删除

| 类别 | 文件 / 组件 | 说明 |
| --- | --- | --- |
| **新增** | `stock_analyze/market_data.py` | `prepare_market_data()` 主入口；snapshot 元数据生成 |
| **新增** | `stock_analyze/cli.py` 子命令 `prepare-market-data` | CLI 入口，args: `--as-of` / `--scopes` / `--force` |
| **新增** | `data/shared/market_snapshot_<date>.json` | 每次 fetch 的元数据（耗时、行数、错误） |
| **新增** | `data/shared/runs.csv` | pipeline 自身的运行账本（独立于 `data/<agent>/runs.csv`） |
| **新增** | `deploy/systemd/stock-analyze-market-data.{service,timer}` | Mon-Fri 17:25 抓数据 + 触发 daily agent |
| **新增** | `deploy/systemd/stock-analyze-weekly-trigger.{service,timer}` | Sat 10:00 触发 weekly agent（不抓数据） |
| **新增** | `CacheMiss(method, cache_name)` 异常类 | 在 `data_provider.py` |
| **修改** | `AkshareProvider.__init__` 加 `offline: bool = False` 字段 | |
| **修改** | 9 个 fetch 方法统一改成 cache-first | 详见 §4.2 |
| **修改** | `benchmark_close` 新增按日 csv 缓存 | 之前完全没有 |
| **修改** | `run-daily` / `run-weekly` 加 `--offline` 标志 | 透传到 `AkshareProvider(offline=...)` |
| **修改** | 4 个 agent service 文件 `ExecStart` 加 `--offline` | |
| **删除** | 4 个 agent timer 文件 (`stock-analyze-{claude,codex}-{daily,weekly}.timer`) | agent 不再独立调度 |
| **不动** | `stock-analyze-monthly-review.{service,timer}` | 每月 1 号独立链路 |
| **不动** | `stock-analyze-dashboard.service` | 常驻 dashboard |
| **不动** | `stock_analyze/factor_pipeline.py` / `portfolio_controls.py` / `build_target_orders` / `build_signals` | 策略代码完全不动 |
| **不动** | `configs/competition.yaml` 锁字段集合 | 不引入新锁字段 |

---

## 四、详细设计

### 4.1 prepare-market-data 执行流

```
┌─────────────────────────────────────────────────────────────────────┐
│  prepare-market-data --as-of 2026-05-22                             │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ↓
            ┌───────────────────────────────────────────┐
            │  1. trading_calendar()                    │  ← 后续 next_trading_day 用
            │     写 trading_calendar_<date>.csv         │
            └─────────────────────┬─────────────────────┘
                                  │
                                  ↓
            ┌───────────────────────────────────────────┐
            │  2. spot()  全市场行情                     │  ← 含 PE/PB 快照
            │     写 spot_<date>.csv                    │
            └─────────────────────┬─────────────────────┘
                                  │
                                  ↓
            ┌───────────────────────────────────────────┐
            │  3. for scope in [hs300, zz500]:           │
            │       index_constituents(scope)             │
            │       写 constituents_<index>_<date>.csv    │
            └─────────────────────┬─────────────────────┘
                                  │
                                  ↓
            ┌───────────────────────────────────────────┐
            │  4. preselect 合并候选 → top                │
            │     max_fetch_candidates（baseline 锁定 250）│
            │     按现有 preselect 逻辑过滤 ST / 上市天数  │
            └─────────────────────┬─────────────────────┘
                                  │
                                  ↓ ThreadPoolExecutor(max_workers=5)
            ┌───────────────────────────────────────────┐
            │  5. for code in candidates:                │
            │       basic_info(code)                     │
            │       price_history(code, days=220)         │
            │       valuation_metrics(code)               │
            │       financial_metrics(code)               │
            │       dividend_yield(code)                 │
            │     单只股票内 5 个调用串行（避免限流）       │
            │     candidate 之间并发 5 路                  │
            │     单只失败 → snapshot.errors 加一行，整体继续│
            └─────────────────────┬─────────────────────┘
                                  │
                                  ↓
            ┌───────────────────────────────────────────┐
            │  6. for bench in [000300, 000905]:         │
            │       benchmark_close(bench, as_of)         │
            │       写 benchmark_<code>_<date>.csv       │  ← 新增
            └─────────────────────┬─────────────────────┘
                                  │
                                  ↓
            ┌───────────────────────────────────────────┐
            │  7. 写 data/shared/market_snapshot_<date>  │
            │     .json （含 fetched_at / errors / rows） │
            └─────────────────────┬─────────────────────┘
                                  │
                                  ↓
            ┌───────────────────────────────────────────┐
            │  8. 追加 data/shared/runs.csv 一行          │
            │     status=success / partial / failed       │
            └─────────────────────┬─────────────────────┘
                                  │
                                  ↓
                        exit 0 ────→ ExecStartPost 触发 agent
                        exit !0 ───→ ExecStartPost 不执行
```

整体失败判定（exit !0）的条件：
- `spot()` 失败（全市场没拿到 → 后续因子全废）
- 全部 benchmark 失败（基准超额收益没法算）
- 单只候选失败 → 不算整体失败，记到 `snapshot.errors`，整体 continue

### 4.2 AkshareProvider cache-first 统一模板

所有 9 个 fetch 方法的代码结构改成同一模板：

```python
def <method>(self, *args) -> DataFrame:
    # 内存层（避免同次 run 内重复 IO）
    mem_key = make_mem_key(args)
    if mem_key in self._<method>_cache:
        return self._<method>_cache[mem_key].copy()

    # 磁盘层
    cache_name = make_cache_name(args)              # e.g. "history_000001_20260522_220"
    cached = self.load_cache(cache_name)
    if not cached.empty:
        normalized = normalize_<method>(cached)
        self._<method>_cache[mem_key] = normalized
        return normalized.copy()

    # offline 拒绝走网络
    if self.offline:
        raise CacheMiss(method="<method>", cache_name=cache_name)

    # 网络层（仅 prepare-market-data 走到这）
    df = self._fetch_<method>_from_network(*args)   # 沿用现有 sources fallback
    self.write_cache(cache_name, df)
    normalized = normalize_<method>(df)
    self._<method>_cache[mem_key] = normalized
    return normalized.copy()
```

注意点：
- **顺序**：内存 → 磁盘 → 网络。当前代码 5 个假 cache-first 是"网络 → 磁盘兜底"，本次直接颠倒。
- **offline 检查在磁盘 miss 之后**：因为磁盘 miss + online 才需要打网络；如果先 check offline 会跳过磁盘读，错过 cache 命中。
- **return copy()**：避免上层修改影响内存 cache。

### 4.3 systemd 拓扑

```
                        ┌────────────────────────────────┐
                        │  /etc/systemd/system/           │
                        └────────────────────────────────┘
                                       │
        ┌─────────────────────────────┬┴────────────────────────────┐
        ↓                              ↓                              ↓
┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│ market-data.timer    │  │ weekly-trigger.timer │  │ monthly-review.timer │
│ Mon..Fri *-*-*       │  │ Sat *-*-*            │  │ *-*-01 01:00:00 UTC  │
│ 09:25:00 UTC         │  │ 02:00:00 UTC         │  │ (1 号 09:00 CST)     │
│ (17:25 CST)          │  │ (10:00 CST)          │  │ 不在本次变更范围      │
└──────────┬───────────┘  └──────────┬───────────┘  └──────────────────────┘
           │                          │
           ↓                          ↓
┌──────────────────────┐  ┌──────────────────────┐
│ market-data.service  │  │ weekly-trigger.service│
│ ExecStart=           │  │ ExecStart=/bin/true   │
│   python -m ...      │  │                       │
│   prepare-market-data│  │ (no fetch)            │
│ ExecStartPost=       │  │ ExecStartPost=        │
│   systemctl start    │  │   systemctl start     │
│   --no-block         │  │   --no-block          │
│   claude-daily       │  │   claude-weekly       │
│ ExecStartPost=       │  │ ExecStartPost=        │
│   systemctl start    │  │   systemctl start     │
│   --no-block         │  │   --no-block          │
│   codex-daily        │  │   codex-weekly        │
└──┬──────────────┬───┘  └──┬──────────────┬───┘
   │              │          │              │
   ↓              ↓          ↓              ↓
┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐
│ claude- │  │ codex-  │  │ claude- │  │ codex-  │
│ daily   │  │ daily   │  │ weekly  │  │ weekly  │
│ .service│  │ .service│  │ .service│  │ .service│
│         │  │         │  │         │  │         │
│ ExecStart= python ... run-daily/weekly --offline                │
│ (没有对应的 timer 文件了，只能通过 ExecStartPost 间接拉起)        │
└─────────┘  └─────────┘  └─────────┘  └─────────┘
```

要点：
- **两个 timer 单独管 daily 与 weekly**，按自然日完全错开。
- **agent service 没有 timer**——它们只能被 ExecStartPost 拉起或运维手工 `systemctl start`。
- ExecStart 失败时 ExecStartPost 不执行（systemd 默认行为）→ 数据没拉到，agent 不会跑出脏数据。
- `--no-block` 让两 agent 并行启动；其中一个 fail 不影响另一个。

### 4.4 CacheMiss fail-fast 路径

```
agent service ExecStart 启动
       │
       ↓
build_signals / build_orders / execute_orders
       │
       ↓
某处调用 provider.price_history("600519", as_of=today)
       │
       ↓
内存 cache miss
       │
       ↓
load_cache("history_600519_20260522_220") → 空 DataFrame
       │
       ↓
self.offline == True
       │
       ↓
raise CacheMiss(method="price_history",
                cache_name="history_600519_20260522_220")
       │
       ↓
unhandled exception 冒到 main()
       │
       ↓
RunLedger 写一行 `status=failed, error=cache_miss:price_history:...`
       │
       ↓
exit code != 0
       │
       ↓
systemd 标记 service `failed`
       │
       ↓
dashboard 下次刷新读 runs.csv 看到 failed → 显示红色
```

整条路径**不会发起任何 outbound HTTP**——这是"硬保证两 agent 同源"的关键。

---

## 五、节拍表（一周时间线）

变更后一周完整时间表（CST）：

| 周几 | 时刻 | 任务 | 触发方 | 类型 | 网络 |
| --- | --- | --- | --- | --- | --- |
| 周一 | 09:30 | A 股开盘 | (外部) | - | - |
| 周一 | 09:30-10:00 | 上周六的 pending_orders 在开盘 vwap 成交（账面，无真单） | - | - | - |
| 周一 | 15:00 | A 股收盘 | (外部) | - | - |
| 周一 | 17:25 | prepare-market-data | market-data.timer | pipeline | **打网络** |
| 周一 | ~17:35 | claude-daily + codex-daily 并行 | market-data.service ExecStartPost | agent | 0（offline） |
| 周二 | 17:25 | prepare-market-data | market-data.timer | pipeline | **打网络** |
| 周二 | ~17:35 | claude-daily + codex-daily 并行 | market-data.service ExecStartPost | agent | 0 |
| 周三 | 17:25 | prepare-market-data | market-data.timer | pipeline | **打网络** |
| 周三 | ~17:35 | claude-daily + codex-daily 并行 | market-data.service ExecStartPost | agent | 0 |
| 周四 | 17:25 | prepare-market-data | market-data.timer | pipeline | **打网络** |
| 周四 | ~17:35 | claude-daily + codex-daily 并行 | market-data.service ExecStartPost | agent | 0 |
| 周五 | 17:25 | prepare-market-data | market-data.timer | pipeline | **打网络** |
| 周五 | ~17:35 | claude-daily + codex-daily 并行 | market-data.service ExecStartPost | agent | 0 |
| 周六 | 10:00 | weekly-trigger（不抓数据） | weekly-trigger.timer | pipeline | 0 |
| 周六 | 10:00 | claude-weekly + codex-weekly 并行（用周五 cache） | weekly-trigger.service ExecStartPost | agent | 0 |
| 周日 | - | 无任务 | - | - | - |
| (每月 1 号) | 09:00 | monthly-review + judge-proposals + apply-approved | monthly-review.timer | review | 0 |

---

## 六、故障路径

### 6.1 prepare-market-data 失败

**触发条件**：spot 全失败 / 两个 benchmark 都失败 / 进程崩溃。

```
prepare-market-data 进程 exit != 0
       ↓
systemd 标记 market-data.service `failed`
       ↓
ExecStartPost 不执行
       ↓
claude-daily / codex-daily 都不会启动
       ↓
data/<agent>/runs.csv 当天不写新行
       ↓
data/shared/runs.csv 写一行 status=failed
       ↓
dashboard 显示红色（market-data 链路）
       ↓
运维操作：
  ① 看 /var/log/journal/...market-data.service 找原因
  ② 修复后 `systemctl start stock-analyze-market-data.service` 手动重跑
  ③ 重跑成功后 ExecStartPost 会自动拉起 daily agent
```

### 6.2 单只候选股票拉取失败

**触发条件**：某只股票 5 个接口里有 1-5 个失败。

```
prepare-market-data ThreadPoolExecutor 工作线程
       ↓
某只 code 调用 financial_metrics 抛异常
       ↓
工作线程 catch → snapshot.errors.append({code, method, message})
       ↓
继续拉下一只
       ↓
最终 snapshot.errors 长度 < 总候选数的 5%
       → 整体仍 status=success（或 partial）
       → ExecStartPost 仍触发 agent
       ↓
agent 跑到这只股票时 cache_name 不存在
       ↓
provider.financial_metrics(code) → cache miss → offline → raise CacheMiss
```

**这是已知的设计冲突**：partial 拉取下 agent 仍会 CacheMiss。处理方案二选一：

A. 让 `prepare-market-data` 在 errors > 0 时 exit !=0（最严格）
B. 在 factor_pipeline 层把这只股票的缺失因子值标 NaN，min_factor_coverage 过滤掉这只候选

**本次变更默认行为**：当 critical（spot / 两个 benchmark）失败时整体 fail；单股某接口失败仍记 errors 整体 continue，agent CacheMiss 时该股从候选剔除（按 `min_factor_coverage` 自然过滤）。

> 这部分逻辑会在实现阶段补 spec scenario：fetch partial → agent factor coverage drops → 该股被剔除而不是整跑 fail。

### 6.3 周六 weekly 跑时周五 cache 不存在

**触发条件**：周五 prepare-market-data 失败且运维没有手动补。

```
Sat 10:00 weekly-trigger.timer 触发
       ↓
weekly-trigger.service ExecStart=/bin/true → 成功
       ↓
ExecStartPost 拉起 claude-weekly + codex-weekly
       ↓
两 agent 都 --offline 启动
       ↓
都在第一个 provider 调用时 raise CacheMiss
       ↓
两 agent service 都 `failed`
       ↓
data/<agent>/runs.csv 各写一行 status=failed
       ↓
dashboard 显示红色（两 agent 都 weekly failed）
       ↓
运维操作：
  ① `prepare-market-data --as-of 2026-05-22 --force`
     （注意 as_of 用周五日期，不是周六）
  ② 成功后 `systemctl start stock-analyze-claude-weekly.service stock-analyze-codex-weekly.service`
     手动拉起 weekly
```

### 6.4 单个 agent service 失败

**触发条件**：claude-daily 因为代码 bug / cache 文件损坏挂掉，codex-daily 正常。

```
market-data.service 成功
       ↓
ExecStartPost --no-block 并行启动两 agent
       ↓
claude-daily 跑到一半 raise 异常 exit !=0
       ↓
codex-daily 完全不受影响，继续跑完
       ↓
systemd 各自标记两 service 状态
       ↓
data/claude/runs.csv 写一行 status=failed
data/codex/runs.csv 写一行 status=success
       ↓
dashboard 一红一绿
```

`--no-block` 是关键——它让两 agent 真正独立，一个的失败不会传染另一个。

---

## 七、关键决策

### 7.1 为什么 weekly 改到周六 10:00

- **错开自然日**：之前 daily + weekly 都在周五傍晚连跑 30 分钟，运维 grep 日志时容易把两波数据混淆。挪到周六，时间边界清晰。
- **早于工作时间**：周一上班前已经能在 dashboard 看到一周复盘。
- **不影响交易**：A 股周六不开盘，weekly 提前 / 推迟 24 小时都不影响周一开盘成交。
- **避开 monthly-review 时段**：monthly-review 每月 1 号 09:00 跑，周六 10:00 跑 weekly 即便 1 号撞上周六也错开 1 小时。

### 7.2 为什么严格 fail-fast 而不是用昨天 cache 兜底

讨论过"agent 找不到今天 cache 时自动回退到昨天 cache"。否决：

- 静默用旧数据 → dashboard 看不到异常 → NAV 偷偷漂移 → 月底复盘时才发现某天数据是上周的。
- 严格 fail-fast → service 立刻 failed → dashboard 立刻红色 → 立刻被发现 → 立刻修。
- 失败可见性 > 数据可用性，是这个项目的核心权衡。

如果将来真的有"低值低频接口允许 stale 几天"的需求（比如行业字段），可以按方法粒度配置；本次不做。

### 7.3 为什么不用 dispatch-agents.sh

早期草案设计了一个 shell 脚本，根据 `date +%u` 判断今天是 Mon-Thu 触发 daily 还是 Fri 触发 weekly。挪到 Sat 之后分发逻辑消失了：

- Mon-Fri 那个 timer 永远只触发 daily
- Sat 那个 timer 永远只触发 weekly

每个 timer 语义单一，ExecStartPost 直接写死 service 名即可。shell 脚本反而是多一层依赖（要 chmod +x、要确认路径、要 set -e、要测）。**当 if 分支退化为常量时，把脚本去掉**。

### 7.4 为什么并发拉 5 路

- 250 只候选 × 5 个接口 = 1250 次顺序调用 × 平均 2-3 秒 = 30-60 分钟。超出 17:25 → 17:35 的 10 分钟窗口。
- `ThreadPoolExecutor(max_workers=5)` 让 5 只股票同时拉，单股内 5 个接口仍串行（避免单只触发限流）。
- 压到 ~10 分钟，正好卡在 daily agent 启动前。
- 如果实际并发触发限流，可调 max_workers 到 3。

### 7.5 为什么 benchmark_close 也要 cache

- 当前代码里 `benchmark_close` 是唯一**完全不写 cache** 的方法。
- 这意味着每次 daily / weekly 都重新打网络拉基准收盘价。两 agent 各打一遍。
- 5/20 数据漂移事件就是因为 claude 17:30 拿 5/19 收盘（数据源没刷新）、codex 17:35 拿 5/20 收盘。
- 加上 `benchmark_<code>_<YYYYMMDD>.csv` 日级缓存后，prepare-market-data 在 17:25 一次拉到，两 agent 17:35 读同一份。

---

## 八、与已有改造的关系

| 已存在的 change | 与本次的关系 |
| --- | --- |
| `introduce-dual-agent-competition` | 引入 baseline + overlay + 锁字段架构。本次**不动锁字段集合**，只是给 provider 加 offline 参数。 |
| `enable-cli-based-agent-analysis` | 引入 CLI 工作流（agent-prepare-weekly / agent-judge-proposals 等）。本次**不动这些命令**，只新增 `prepare-market-data` 子命令。 |
| `expand-portfolio-capacity-and-strategy-visibility` | top_n 50、max_fetch_candidates 250、dashboard 加 strategy-evolution 面板。本次**复用 max_fetch_candidates 的值**作为 prepare-market-data 的候选数。 |
| `tighten-audit-findings` | F1-F10 审计修复（已 archived）。本次**不重复其内容**，但承袭了"严格失败可见"的设计哲学。 |

---

## 九、阅读路径建议

新来的人按这个顺序看：

1. `README.md` 顶部（10 分钟）—— 整体定位
2. 本文档 §1（核心概念白话）（20 分钟）—— 词汇表
3. 本文档 §2-3（架构对比）（15 分钟）—— 直觉
4. `proposal.md`（10 分钟）—— 动机
5. `design.md`（30 分钟）—— 决策记录
6. 本文档 §4-6（详细设计 + 节拍表 + 故障路径）（30 分钟）—— 实操细节
7. `specs/*/spec.md`（20 分钟）—— 形式验收标准
8. `tasks.md`（10 分钟）—— 实施清单

合计 ~2.5 小时读完，应该足以独立修改这条链路上的代码。
