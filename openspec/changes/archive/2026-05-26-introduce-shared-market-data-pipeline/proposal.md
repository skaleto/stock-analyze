## Why

当前每天 / 每周两个 agent 各自独立打公开数据接口（AkShare / eastmoney / tencent / sina / baostock）：

- daily：两 agent 各打 ~10 次（持仓行情 + 基准收盘）
- weekly：两 agent 各打 ~1000 次（全候选池 ~250 只 × 4-5 个接口）

代码层面 `data/shared/cache/` 是"共享缓存目录"，但 `spot()` / `index_constituents()` / `price_history()` / `trading_calendar()` / `benchmark_close()` 都是**先打网络再写缓存**——缓存只在网络全失败时作为兜底读取。所以 happy path 上的网络调用并没有真正共享。

实际观察到的后果：

- **5/20 16:30/16:35 数据差异**：claude 17:30 拿到的 hs300 benchmark_date 是 5/19（陈旧），codex 17:35 拿到的是 5/20（新鲜）；两个 agent 看到的"原始数据"不一致。
- **5/21 catch-up 印证**：同一时段两次跑，zz500 收盘价从 8656.31 → 8419.84（-2.7%），证明早期时段拿到的是上一交易日的残值伪装成今天的数据。

观察出的本质问题：**fetch 数据这件事**应该是一个**独立任务**，与 agent 策略计算完全解耦。模型本身（claude / codex）只跑策略，不打网络。

更进一步：weekly 在它执行的那一时点（周五 17:40）**没有任何数据是"必须现拉"的**。所有它需要的因子原料（PE / ROE / 20-60 日动量 / 60 日波动率 / 股息率 / 行业 / 基准收盘）都可以由一个每天跑的数据任务累积进缓存。只要 Mon-Fri 每天都拉一遍今天的快照，到了 Fri 17:40 weekly 直接读 cache 就能算出信号。

## What Changes

引入一条独立的"数据流水线"任务，agent 服务退化为纯策略计算 + 强制 offline。**daily 与 weekly 完全错开到不同自然日**——daily 在工作日收盘后跑，weekly 挪到周六上午跑：

```
17:25  Mon-Fri  stock-analyze-market-data.timer
  └ prepare-market-data.service
      ├ ExecStart= prepare-market-data
      │   · trading_calendar
      │   · spot 全市场
      │   · index_constituents(hs300) + (zz500)
      │   · 对 max_fetch_candidates 候选池每只：basic_info / price_history(220天) / valuation / financial / dividend
      │   · benchmark_close(000300) + (000905)
      │   · 写 data/shared/cache/<file>.csv
      │   · 写 data/shared/market_snapshot_<date>.json (含 fetched_at / errors / row counts)
      │   · 写 data/shared/runs.csv 行（pipeline 自身的运行账本）
      │
      └ ExecStartPost= 并行触发 daily agent service：
          systemctl start --no-block claude-daily.service
          systemctl start --no-block codex-daily.service

10:00  Sat      stock-analyze-weekly.timer
  └ weekly-trigger.service
      └ ExecStart= 并行触发 weekly agent service：
          systemctl start --no-block claude-weekly.service
          systemctl start --no-block codex-weekly.service
        （**不再跑 prepare-market-data**，直接用周五 17:25 拉到的 cache）
```

Agent 端用强制 offline 模式跑：

- 所有 fetch 方法 cache-first
- `--offline` 标志下 cache miss → `raise CacheMiss` → service `failed` → dashboard 立即红色
- **绝不偷偷打网络补救**（这是"硬保证两 agent 同源"的关键）

新增 / 修改：

- `stock_analyze/market_data.py`（新模块）：`prepare_market_data(scope, as_of, force)` 主入口；fetch 编排；snapshot 元数据
- `stock_analyze/cli.py`：新增 `prepare-market-data` 子命令；现有 `run-daily` / `run-weekly` 加 `--offline` 标志
- `stock_analyze/data_provider.py`：
  - `AkshareProvider(offline=False)` 增 offline_mode 字段
  - 上层方法（`spot`, `index_constituents`, `price_history`, `trading_calendar`, `basic_info`, `valuation_metrics`, `financial_metrics`, `dividend_yield`, `benchmark_close`）改 cache-first：先 `load_cache`，命中即返回；miss 时按 offline 决定是否打网络或 raise
  - `benchmark_close` 新增按日缓存（之前完全没有）
  - 新增 `CacheMiss(method, cache_name)` 异常
- `deploy/systemd/`：
  - 新增 `stock-analyze-market-data.{service,timer}` — Mon-Fri 17:25 抓数据，完成后触发 daily agent
  - 新增 `stock-analyze-weekly-trigger.{service,timer}` — Sat 10:00 触发 weekly agent（用周五缓存，不再拉网络）
  - `stock-analyze-{claude,codex}-{daily,weekly}.service` 改用 `--offline` 启动
  - **删除** `stock-analyze-{claude,codex}-{daily,weekly}.timer` 4 个 timer（agent 不再独立调度，全部由两个 pipeline timer 触发）
- `docs/competition-runbook.md` + `docs/system-overview.md` + `README.md` 同步新拓扑

## Capabilities

### New Capabilities

- `shared-market-data-fetch` — 独立 `prepare-market-data` 任务、`data/shared/market_snapshot_<date>.json` schema、错误聚合行为、`run-ledger` 记账。
- `offline-cache-first-provider` — `AkshareProvider` 的 cache-first 改造（9 个方法）、`offline_mode` 标志、`CacheMiss` 异常、`benchmark_close` 新增缓存。
- `pipeline-systemd-orchestration` — 两个 pipeline timer：`market-data.timer` (Mon-Fri 17:25) 抓数据 + 触发 daily agent；`weekly-trigger.timer` (Sat 10:00) 触发 weekly agent（用周五缓存）。agent 不再有独立 timer。

### Modified Capabilities

- 无新增 spec 修改；`competition-baseline-fairness` / `multi-agent-runtime` 的锁字段集合不变，agent 视角的目录布局不变，只是 agent 调用 provider 时多了 `offline=True` 参数。

## Impact

- **代码**：1 新模块 + provider 重构（~150 行）+ CLI 加 2 个子命令/标志 + systemd 拓扑改 4 个文件。
- **配置**：无新增配置字段。`competition.yaml` / `agents/*.yaml` 不动。
- **数据 / 产物**：
  - 新增 `data/shared/market_snapshot_<date>.json`
  - 新增 `data/shared/runs.csv`（pipeline 自身账本，独立于 `data/<agent>/runs.csv`）
  - 现有 `data/<agent>/runs.csv`、`daily_nav.csv`、`trades.csv` 等不变
- **网络**：~5000 次 / 周（vs 当前 ~2000，换来硬保证 + 数据韧性）。所有调用集中在 17:25 一次，分钟级完成。
- **失败模式**：
  - prepare-market-data 失败 → ExecStartPost 不再触发 agent（不打错单子）
  - agent 失败（CacheMiss）→ 自身 service failed，另一 agent 不受影响（并行启动）
  - 老 cache 不会被偷用——严格 fail-fast
- **文档**：3 处运维 / 总览文档 + 1 处 README 顶部时间表
- **不在范围**：
  - 不改 baseline 锁字段、不改 agent 配置 schema
  - 不修 ECS 上的 `data/cache/` 旧目录（一旦切换，那个目录可以日后人工清理）
  - 不引入新的第三方数据源
  - 不实现"自动用昨天 cache 兜底"（严格模式，cache miss 就 fail）

## Status

**ACTIVE** — 待用户确认任务职责定义后开干。
