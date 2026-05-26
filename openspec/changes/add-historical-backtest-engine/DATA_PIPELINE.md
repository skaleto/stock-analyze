# 数据处理链路（全局）

整个 Stock Analyze 系统的数据流总览，包括既有的 forward simulator 链路和本次 `add-historical-backtest-engine` 新增的回测链路。

---

## 1. 三个并行存在的数据通路

```
┌───────────────────────────────────────────────────────────────────────┐
│                                                                       │
│  ① Forward 通路（已有）                                                │
│     每个交易日 ECS 17:25 自动跑                                        │
│     真实 Tushare/Baostock 数据 → 真实模拟交易 → 真实 NAV 累积           │
│                                                                       │
│  ② Backtest 通路（新增）                                               │
│     操作员手动触发                                                     │
│     历史 Tushare 数据 (2021-2026-04) → 模拟回测 → 历史 NAV 序列         │
│                                                                       │
│  ③ Evolution / Gate 通路（部分新增）                                   │
│     LLM 月度演化时 evolution_writer 自动跑                              │
│     在验证窗口跑 backtest → 检查 3 条 floor 阈值 → 通过/拒绝 commit      │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 2. ① Forward 通路（既有，未改）

```
ECS systemd 17:25 Mon-Fri
   │
   ▼
prepare-market-data.service
   │   读 Tushare Pro，写共享缓存
   │
   ├─ data/shared/cache/spot_<YYYYMMDD>.csv       全市场快照
   ├─ data/shared/cache/constituents_*.csv         指数成分
   ├─ data/shared/cache/<code>_history.csv         历史价格
   ├─ data/shared/cache/<code>_*.csv               valuation / fina / div
   ├─ data/shared/data_health.json                 数据源健康
   └─ data/shared/market_snapshot_<date>.json      元数据
   │
   ▼  ExecStartPost (offline=True)
   │
   ├─ stock-analyze-claude-daily.service
   │     │ provider 强制 offline，从 cache 读
   │     ▼
   │     execute_due_orders(as_of=today)
   │        │ 读 spot_<YYYYMMDD>.csv 拿当天开盘价
   │        │ 把 pending 订单成交
   │        ▼
   │     update_nav(as_of=today)
   │        │ 读 spot 拿当天收盘价 mark-to-market
   │        ▼
   │     compute_pending_forward_ic(as_of=today)
   │     generate_dashboard
   │
   └─ stock-analyze-codex-daily.service (同上)

Sat 10:00
   │
   ▼
weekly-trigger.service (不再拉数据)
   │  复用周五 17:25 拉的缓存
   ▼
   stock-analyze-claude-weekly.service
   stock-analyze-codex-weekly.service
       generate_rebalance_orders(as_of=Saturday)
          │
          │ → build_signals
          │     factor_pipeline 全套（winsorize/z-score/行业中性化）
          │     portfolio_controls 全套（top_n / industry_cap / hold_buffer）
          │     生成下周一执行的 pending_orders
          ▼
       update_nav + report + dashboard
```

**Forward 数据存放**：

```
data/<agent>/
├── state.json                现金 + 持仓
├── pending_orders.json       待执行订单
├── daily_nav.csv             ★ 每日 NAV 时间序列
├── trades.csv                ★ 模拟成交流水
├── positions.csv             持仓快照
├── latest_signals.csv        最近一期 top50 选股
├── performance_summary.json  全套绩效指标
├── runs.csv                  CLI 运行账本
├── factor_runs/<run_id>.csv  每周因子明细
├── configs/<hash>.json       完整 config snapshot
└── notes/briefings/*.md      ECS 生成给 LLM 的任务包
```

★ = 本次新增的 backtest 通路与 forward 共享相同的 schema 结构（在不同的 `data_root` 下）。

---

## 3. ② Backtest 通路（本次新增）

```
                          一次性 (~15 min)
                         ┌─────────────────┐
                         │ Tushare Pro     │
                         │  (live API)     │
                         └────────┬────────┘
                                  │
                                  ▼
              python3 -m stock_analyze prepare-backtest-data \
                --start 2021-01-01 --end 2026-04-30

              stock_analyze/backtest/data_prep.py::prepare_backtest_data()
                │
                │ 7 个 endpoint，幂等续跑：
                │   • pro.trade_cal       → trade_cal.csv
                │   • pro.stock_basic     → stock_basic.csv
                │   • pro.daily           → daily/<iso>.csv (per trading day)
                │   • pro.daily_basic     → daily_basic/<iso>.csv
                │   • pro.fina_indicator  → fina_indicator/<code>.csv
                │   • pro.adj_factor      → adj_factor/<code>.csv
                │   • pro.index_weight    → index_weight/<idx>_<YYYY-MM>.csv
                │
                ▼
       data/shared/backtest_cache/
                │
                │ (★与 data/shared/cache/ 隔离，互不污染)
                │
                ▼
       PointInTimeView(as_of=t, cache_root=backtest_cache)
                │
                │ 唯一的数据读取出口，强制 point-in-time：
                │   • daily / daily_basic: trade_date <= t
                │   • fina_indicator:      ann_date  <= t
                │   • index_weight:        最近月度快照 YM <= t
                │   • stock_basic:         list_date <= t AND
                │                          (delist_date 空 或 delist_date > t)
                │
                ▼
       engine.run_backtest(overlay, start, end, ...)
                │
                ▼
       BacktestProvider (薄壳)
                │ 实现 simulator 需要的 5 个方法:
                │   next_trading_day / price_snapshot / benchmark_close /
                │   execution_quote / execution_price
                │
                ▼
       for d in trade_days:
           ┌─ pending orders 执行 (T+1)
           │    简化版本（engine 内部）:
           │    匹配 execute_after <= d 的订单
           │    按当天 open 价 + 滑点 + 佣金 + 印花税 计算成交
           │
           ├─ NAV 更新
           │    每个账户 = cash + Σ(持仓 × current close)
           │
           └─ 周五（信号日）:
                │ MVP 简化版信号生成:
                │   PointInTimeView.daily_basic(as_of=d)
                │   按 pe_ttm 升序取 top_n
                │   等权 target_value 每股
                │   diff vs 当前持仓 → pending_orders (执行于下个交易日)
                │
                │ ※ MVP 未走完整 factor_pipeline；
                │   原因见 IMPLEMENTATION_REPORT.md §4.1
                ▼
       输出（与 forward 同 schema）:
         data/<agent>/backtest/<run_id>/
         ├── daily_nav.csv             ★同 forward schema
         ├── trades.csv                ★同 forward schema
         ├── signals.csv               ★同 forward schema
         ├── performance_summary.json
         └── report.md                 (人类可读)
```

**Backtest 数据存放**：

```
data/shared/backtest_cache/    ★ 全局共享，所有回测共用
├── trade_cal.csv
├── stock_basic.csv
├── daily/<iso>.csv
├── daily_basic/<iso>.csv
├── fina_indicator/<ts_code>.csv
├── adj_factor/<ts_code>.csv
├── index_weight/<idx>_<YYYY-MM>.csv
└── _meta.json                  幂等进度

data/<agent>/backtest/         ★ 每个 agent 单独
├── <run_id>/                   研究型 backtest 输出
│   ├── daily_nav.csv
│   ├── trades.csv
│   ├── signals.csv
│   ├── performance_summary.json
│   └── report.md
├── training/<YYYY-MM>/         (Task 11 计划：ECS 月度自动跑)
│   └── performance_summary.json (briefing 引用)
└── validation/<YYYY-MM>/       (Task 12 实施：gate 自动跑)
    └── performance_summary.json (briefing 引用)
```

---

## 4. ③ Evolution / Gate 通路（本次新增 hook）

```
LLM 在本地 Claude Code session 跑 /monthly-strategy claude
   │
   │ LLM 读 briefing + 思考 + 写新 yaml
   ▼
operator triggers evolution_writer.write_evolution(...)
   │
   │ ┌─ overlay_guard.validate (已有)
   │ │   schema / 锁字段 / 因子白名单 / 权重区间
   │ │   raise OverlayGuardError → 早退
   │ │
   │ ├─ ★★ backtest.gate.validate_overlay_via_backtest (新增) ★★
   │ │   ┌─ engine.run_backtest on 验证窗口 (2025-01 ~ 2026-04)
   │ │   │   in_memory=True，只要 metrics
   │ │   │
   │ │   ├─ 检查阈值 (从 competition.yaml.backtest.floor 读):
   │ │   │   • abs(max_drawdown) > 0.25 → raise
   │ │   │   • sharpe < -0.5            → raise
   │ │   │   • cum_return < -0.15       → raise
   │ │   │
   │ │   ├─ raise BacktestFloorBreach(breach_type, metrics)
   │ │   │     │
   │ │   │     ▼
   │ │   │   _write_floor_breach_log:
   │ │   │     data/<agent>/evolution_log/<month>-floor-breach.md
   │ │   │     yaml 不变，_history 不变
   │ │   │     LLM 必须重新设计
   │ │   │
   │ │   └─ 返回 BacktestMetrics → 注入到 evolution_diff JSON
   │ │
   │ ├─ backup _history/<old_hash>.yaml
   │ ├─ 写新 configs/agents/<agent>.yaml
   │ ├─ 写 data/<agent>/evolution_log/<month>.md
   │ ├─ 写 data/<agent>/evolution_diff/<month>.json (含 backtest_metrics)
   │ └─ 追加 data/<agent>/config_evolution.csv
   │
   ▼
operator triggers ./scripts/sync-to-ecs.sh
```

---

## 5. 三段时间窗口纪律

由 `add-historical-backtest-engine` 引入，写进 `CLAUDE.md / AGENTS.md §9`（Task 15，待操作员合入）：

```
2021-01-01 ────── 2024-12-31 │ 2025-01-01 ── 2026-04-30 │ 2026-05-18 ──→
    训练窗口（48 个月）       │  验证窗口（16 个月）       │  Live OOS
    LLM 自由探索              │  gate 准入判定用           │  真实竞赛
                              │                            │
                              ▼                            ▼
                          briefing 仅 5 个聚合数字       不存在
                          (information isolation)         (真实历史尚未发生)
```

briefing 实施位置：`agent_briefing.render_training_section` / `render_validation_section`。

---

## 6. Dashboard 视图集成

```
reports/competition/dashboard.html (专业版)
   │
   ├─ Claude tab
   │   ├─ 4 张账户卡片
   │   ├─ 净值曲线
   │   ├─ ★ 历史回测 vs 真实运行（render_backtest_vs_live_panel）★
   │   ├─ 因子诊断
   │   ├─ 策略演进时间线
   │   └─ ...
   │
   ├─ Codex tab (同上)
   └─ 对比 tab

reports/competition/simple.html (新手版)
   └─ (不显示 backtest 面板，保持 ≤80KB anti-goal)
```

---

## 7. 关键 Schema 兼容性

Forward 和 Backtest 在 csv schema 上**完全一致**，因此：

- ✅ 同一 dashboard 渲染器既能读 forward 的 `data/<agent>/daily_nav.csv` 也能读 backtest 的 `data/<agent>/backtest/<run_id>/daily_nav.csv`
- ✅ 同一 `performance.compute_account_performance` 既能计算 forward 也能计算 backtest 的指标（实际 backtest engine 复用部分逻辑，部分自实现 — 见 IMPLEMENTATION_REPORT.md §4.1）
- ✅ `render_backtest_vs_live_panel` 在同一坐标系画两条 NAV 曲线
- ✅ trades.csv 的 columns 完全一致，便于跨域对比成本/换手分析

---

## 8. 数据隔离边界

| 数据 | 谁能读 |
|---|---|
| `data/shared/cache/` (forward) | claude + codex（共享） |
| `data/shared/backtest_cache/` (backtest) | claude + codex（共享） |
| `data/<agent>/state.json` 等 forward state | 仅该 agent（运行时） |
| `data/<agent>/backtest/<run_id>/` | 仅该 agent + dashboard 渲染 |
| `data/competition/monthly_reviews/` | 全员（公开战报） |

对手透明度（`CLAUDE.md §7.1`）规则不变：
- ✅ 可读对手 `configs/agents/<other>.yaml` 和 `config_evolution.csv`
- ❌ 不可读对手 `evolution_log/`、`backtest/`（思考过程私有）、`state.json` 等

---

## 9. 失败模式与降级

| 故障 | 行为 |
|---|---|
| Tushare API 失败 / token 无效 | `prepare-backtest-data` exit 2，写到 `_meta.json.errors`（待加） |
| Backtest cache 缺失 | `evolution_writer` 中 gate 软降级（log warning），跳过 floor 检查，继续 commit |
| Floor breach | yaml + `_history` 完全不动；写 `<month>-floor-breach.md` |
| simulator 时钟参数为 None | 走 forward 默认行为（`date.today()` + 默认路径） |
| in_memory 模式下中断 | 最终输出未生成；下次重跑即可 |

---

## 10. 演进路线（参考）

`add-historical-backtest-engine` MVP 落地后，未来可扩展：

1. **桥接完整 factor_pipeline 到 backtest**：让回测真正用 overlay 的因子配置（而非简化 low-PE top-N）
2. **Walk-forward CV**：把验证窗口切成滚动子窗口，更严谨的 OOS 评估
3. **多场景压力测试**：`--scenario stress` 用 2x 滑点 / 2x 佣金跑同一 overlay
4. **双 overlay 对决**：`--compare codex.yaml` 同窗口直接对比
5. **因子分位 IC 时序面板** dashboard
6. **行业 / 风格暴露归因**

均列为后续 OpenSpec change，本 change 完成后由操作员排优先级。

---

## 附录：本次实施 commit 列表

```
522239d  specs: 4 capability spec files
307254f  simulator clock/path parameterization (3 functions + 3 helpers)
68fbdba  simulator: address reviewer feedback (docstring + combined-kwargs test)
857e46c  backtest package scaffolding (types.py)
dd671cf  prepare_backtest_data (7 Tushare endpoints, idempotent)
d561788  prepare-backtest-data CLI
52cb3bc  PointInTimeView (no future leakage)
f5467eb  engine.run_backtest + BacktestProvider
fd5869e  backtest research CLI
1acdacc  markdown report renderer
9539010  competition.yaml backtest.floor config
239e253  gate (BacktestFloorBreach + thresholds)
f1e3e7c  evolution_writer integration (gate hook)
4446609  agent_briefing isolation (training full / validation aggregate)
32f8950  reporting: backtest-vs-live panel
0e62e12  e2e tests (synthetic cache + CLI subprocess + gate breach)
```

17 个 commit，260 个测试，全链路跑通。
