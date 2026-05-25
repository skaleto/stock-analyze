# Design · add-historical-backtest-engine

## 1. 目标与场景

一个回测引擎，服务两个独立场景：

**Gate 前端**（自动）：
- 触发：LLM 每月跑 `/monthly-strategy` 写完新 overlay 时
- 调用方：`evolution_writer.write_evolution()` 内部调
- 时间窗口：固定 = 验证窗口 2025-01-01 ~ 2026-04-30
- 输出：5 个总结指标 + pass/fail
- 用途：硬底线把关 + 软指标记录到 evolution_log
- 失败后果：yaml 回滚，LLM 重新设计

**Research 前端**（手动）：
- 触发：操作员手动跑 CLI
- 时间窗口：任意（命令行参数）
- overlay：任意（live / 历史 / 临时）
- 输出：完整 markdown 报告 + 所有 CSV 明细
- 用途：人工实验、因子探索、历史复盘
- 失败后果：无 — 报告生成完结束

底层共享 `stock_analyze/backtest/engine.py::run_backtest()`，两前端是薄壳子。

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│ stock_analyze/backtest/                                     │
│                                                             │
│  engine.py                                                  │
│    run_backtest(overlay, start, end, universe,              │
│                 data_root, out_dir, *, in_memory=False)     │
│      -> BacktestResult                                      │
│    · 取 universe(date) 序列（point-in-time 历史成分）        │
│    · 按交易日循环：                                          │
│      · simulator.execute_due_orders(as_of=date, ...)        │
│      · simulator.update_nav(as_of=date, ...)                │
│      · Friday: simulator.generate_rebalance_orders(...)     │
│    · 输出与 forward 同 schema：                              │
│      · daily_nav.csv / trades.csv / signals.csv             │
│      · performance_summary.json / factor_runs/*.csv         │
│                                                             │
│  data_prep.py                                               │
│    prepare_backtest_data(start, end, force=False) -> None   │
│    · 拉 Tushare 5 年历史到 data/shared/backtest_cache/      │
│    · 幂等，可断点续跑                                        │
│                                                             │
│  gate.py                                                    │
│    validate_overlay_via_backtest(overlay) -> Metrics        │
│    · 跑验证窗口（2025-01 ~ 2026-04）回测                    │
│    · 检查三条硬底线                                          │
│    · raise BacktestFloorBreach(breach_type, metrics)        │
│      或 return metrics                                      │
│                                                             │
│  report.py                                                  │
│    render_markdown_report(result) -> str                    │
│    render_dashboard_fragment(result, live_nav) -> html      │
└─────────────────────────────────────────────────────────────┘

集成点：
  · evolution_writer.write_evolution → gate.validate_overlay_via_backtest
  · cli.py → 加 backtest / prepare-backtest-data 子命令
  · reporting.py → 加历史回测对比面板（专业版 dashboard）
  · simulator.py → 时钟参数 as_of（向后兼容）
```

## 3. 数据层

### 3.1 预热

```bash
python3 -m stock_analyze prepare-backtest-data \
  --start 2021-01-01 --end 2026-04-30
```

拉取 Tushare：
- `pro.daily(start_date, end_date)` — 全 A 股 OHLCV，按日批量
- `pro.daily_basic(start_date, end_date)` — PE/PB/dv_ttm/total_mv，按日批量
- `pro.fina_indicator(ts_code, start_date, end_date)` — ROE/毛利/负债率/净利同比，按股
- `pro.index_weight('000300.SH'|'000905.SH', trade_date)` — 月度成分
- `pro.adj_factor(ts_code, start_date, end_date)` — 前复权因子
- `pro.stock_basic()` — list_date / delist_date / industry
- `pro.trade_cal(start_date, end_date)` — 交易日历

写入 `data/shared/backtest_cache/`：

```
data/shared/backtest_cache/
├── daily/YYYY-MM-DD.csv              # 当日全市场 OHLCV
├── daily_basic/YYYY-MM-DD.csv        # 当日全市场 PE/PB/...
├── fina_indicator/<ts_code>.csv      # 单股财务历史
├── adj_factor/<ts_code>.csv          # 单股复权因子
├── index_weight/000300_YYYY-MM.csv   # 月度成分
├── index_weight/000905_YYYY-MM.csv
├── stock_basic.csv                   # 全 A 股基础信息
├── trade_cal.csv                     # 交易日历
└── _meta.json                        # 预热进度、上次更新时间
```

**幂等性**：每次启动检查 `_meta.json`，已拉过的日期 / 股票跳过。中断后重跑只补缺失。

**与 forward cache 隔离**：`data/shared/backtest_cache/` 与 `data/shared/cache/` 完全独立，互不读写。

### 3.2 Point-in-time 规则

回测引擎在时间 `t` 调仓时，**只能看 `t` 之前可获取的数据**：

| 数据 | 可见性规则 |
|---|---|
| OHLCV | `trade_date < t`（不含当天，避免用未来开盘前信息） |
| daily_basic（PE / PB / dv_ttm） | `trade_date < t` |
| 财务指标 | `ann_date <= t`（公告日 ≤ 信号日） |
| 行业分类 | 用 `t` 时点生效的 SW 一级分类（Tushare 提供历史版本） |
| 指数成分 | 用 `t` 之前最近的月度快照 |
| 退市股 | `list_date <= t` 且（`delist_date is null` 或 `delist_date > t`） |

`engine.py` 内部封装 `point_in_time_view(date)` 抽象层，所有数据访问走这一层。

### 3.3 幸存者偏差处理

每次调仓的 universe：

```python
def universe(date):
    hs300 = read_index_weight('000300.SH', date)  # 最近月度快照
    zz500 = read_index_weight('000905.SH', date)
    union = hs300 + zz500
    # 过滤：date 时点已上市未退市
    return [s for s in union if is_listed_at(s, date)]
```

**当年存在但今天已退市的票**：回测期间正常买入，退市日按当日最后一价（Tushare `daily.close` 最后一条）清仓。差额计入回测成本。

## 4. 引擎层

### 4.1 simulator.py 时钟与路径改造

现有签名（节选）：

```python
# 改造前
def execute_due_orders():
    today = datetime.now().date()
    # 读写 data/<agent>/state.json / data/shared/cache/
    ...
```

改造后：

```python
def execute_due_orders(
    *,
    as_of: date | None = None,
    data_root: Path | None = None,          # simulator 自身状态根
    market_data_root: Path | None = None,   # 只读市场数据根
):
    today = as_of or datetime.now().date()
    data_root = data_root or default_agent_data_root()
    market_data_root = market_data_root or default_shared_cache_root()
    ...
```

**前向调用零改动**（三个参数全 None 时走原行为）。

`update_nav` / `generate_rebalance_orders` 同样接受这三个参数。

### 4.2 主循环（伪代码）

`state.json` / `daily_nav.csv` 等运行状态的存储位置由 `data_root` 路由：前向走 `data/<agent>/`，回测走 `<out_dir>/`。simulator 不需要新增 state 参数 — 它继续从 `data_root` 路径读写，只是被传入不同的 root。

```python
def run_backtest(overlay, start, end, universe, data_root, out_dir, *, in_memory=False):
    trade_cal = load_trade_cal(start, end)
    init_backtest_state_files(overlay, out_dir)  # 初始化 state.json 等

    for date in trade_cal:
        # T+1 执行上一交易日生成的 pending 订单
        simulator.execute_due_orders(as_of=date, data_root=out_dir, market_data_root=data_root)

        # 更新当日 NAV
        simulator.update_nav(as_of=date, data_root=out_dir, market_data_root=data_root)

        # 周五（或顺延的最后交易日）：信号日，生成下个交易日要执行的订单
        if is_signal_day(date):
            simulator.generate_rebalance_orders(as_of=date, data_root=out_dir, market_data_root=data_root)

        if not in_memory:
            # state.json / daily_nav.csv 已在每个 simulator 调用内部增量写
            pass

    return BacktestResult(out_dir=out_dir)
```

`data_root` 参数语义：**simulator 自身状态的根**（state.json / orders / nav / trades）。
`market_data_root` 参数语义：**只读市场数据的根**（前向用 `data/shared/cache/`，回测用 `data/shared/backtest_cache/`）。

### 4.3 速度估算

| 阶段 | 估算 |
|---|---|
| 4 年 × 250 交易日 | 1000 次循环 |
| 每日 NAV 更新 | ~5ms |
| 每周（200 次）调仓 | ~1.5s |
| 总计（disk 模式） | ~5-8 分钟 |
| 总计（in_memory 模式） | ~2-3 分钟 |

**Gate 默认 `in_memory=True`**（结果只需要 5 个总结指标，不需要日级明细）。
**Research 默认 `in_memory=False`**（需要全部明细供报告渲染）。

## 5. 时间窗口纪律

```
2021-01-01 ──── 2024-12-31 │ 2025-01-01 ── 2026-04-30 │ 2026-05-18 ──→
    训练窗口（48 个月）       │   验证窗口（16 个月）      │   live OOS
```

| 窗口 | 信息可见度 | 用途 |
|---|---|---|
| 训练 | LLM 可读：月度明细 / 因子贡献 / 单股贡献 / 调仓历史 | 自由探索假设 |
| 验证 | LLM 只能读：累计 / 年化 / Sharpe / 最大回撤 / IR 五个数字 | Gate 准入判定 |
| live OOS | 数据尚未发生 | 真实裁判 |

**信息隔离的工程实施**：

`agent_briefing.py::build_monthly_briefing` 渲染 `# 数据快照` 段时区分两种数据源：

```python
training_metrics = read_backtest_summary(
    out_dir=data/<agent>/backtest/training/<latest_run>/,
    detail_level='full',  # 月度明细全显示
)
validation_metrics = read_backtest_summary(
    out_dir=data/<agent>/backtest/validation/<latest_run>/,
    detail_level='aggregate_only',  # 仅 5 个数字
)
```

**这两个 backtest 由谁触发**：

- **训练窗口回测**：在月度 briefing 生成时（ECS `competition-monthly-review` 完成后）自动跑一次，用当前 live overlay 跑 2021-2024 完整训练窗口。结果落 `data/<agent>/backtest/training/<YYYY-MM>/`，供 briefing 读取。
- **验证窗口回测**：在 LLM 跑 `/monthly-strategy` 时由 `evolution_writer.write_evolution` 内部 gate 自动跑，用 LLM 刚写的新 overlay 跑验证窗口。结果落 `data/<agent>/backtest/validation/<YYYY-MM>/`。

两个回测都由系统自动跑，操作员不直接触发。Research CLI（`python3 -m stock_analyze backtest ...`）是另一条独立路径，与这两个自动回测互不干扰，输出到 `data/<agent>/backtest/<run_id>/`（不同子目录）。

**文档约束**（写进 `CLAUDE.md §10` / `AGENTS.md §10`）：

> 验证窗口的回测结果只用于"是否通过 gate"。不允许针对验证窗口的失败反复迭代 overlay — 应基于训练窗口的发现重新设计。这是软约束，工程上无法强制，但通过 briefing 信息密度控制降低噪声拟合风险。

## 6. Gate 准入逻辑

### 6.1 集成到 evolution_writer

```python
# stock_analyze/evolution_writer.py

def write_evolution(agent_id, old_overlay, new_overlay, reasoning_md):
    # 现有步骤
    overlay_guard.validate(agent_id, new_overlay, repo_root)

    # 新增：backtest gate
    try:
        metrics = backtest.gate.validate_overlay_via_backtest(new_overlay)
    except BacktestFloorBreach as exc:
        # 写失败报告，不动 yaml
        write_floor_breach_log(agent_id, month, exc, reasoning_md)
        raise  # 中止 commit，LLM 必须重新设计

    # 现有步骤（在 metrics 附带的情况下）
    backup_history(old_overlay, repo_root)
    write_yaml(new_overlay)
    write_evolution_log(agent_id, month, reasoning_md, metrics=metrics)  # 注入 metrics
    write_evolution_diff(agent_id, month, old_overlay, new_overlay, metrics=metrics)
    append_config_evolution_csv(agent_id, month, old_hash, new_hash, ...)
```

### 6.2 底线阈值

读自 `configs/competition.yaml`：

```yaml
backtest:
  floor:
    max_drawdown: 0.25        # 验证窗口最大回撤 ≤ 25%
    sharpe_floor: -0.5        # 验证窗口 Sharpe ≥ -0.5
    cum_return_floor: -0.15   # 验证窗口累计收益 ≥ -15%
```

**非锁字段** — 操作员可调，agent overlay 不能覆盖（overlay_guard 阻止）。

### 6.3 底线设计哲学

只拦"灾难"，不拦"次优"：

| 场景 | 是否拦 |
|---|---|
| 该 overlay 在验证窗口跌 -50%，最大回撤 -45% | 拦（明显灾难） |
| 该 overlay 在验证窗口涨 +2%，跑输沪深300 -3pp | 不拦（次优但不灾难） |
| 该 overlay 在验证窗口完全持平基准 | 不拦 |
| 该 overlay 在验证窗口 Sharpe = 0.1 | 不拦（弱但非负） |

理由：硬性"必须比上个月强"会诱导 LLM 反复对验证窗口调参，反而过拟合。

### 6.4 失败时的产物

`BacktestFloorBreach` 抛出时，evolution_writer 写一份 `<month>-floor-breach.md`：

```markdown
# <agent> 回测准入失败 · <YYYY-MM>

## 失败原因
- 类型: max_dd_exceeded
- 验证窗口最大回撤: -32.1%（阈值 -25%）

## 失败时的 overlay 摘要
（拒绝的 overlay 内容）

## 验证窗口 5 个总结指标
- 累计: -18.2%
- 年化: -13.8%
- Sharpe: -0.71
- 最大回撤: -32.1%
- IR vs 沪深300: -1.4

## LLM 的原始 reasoning
（LLM 写的 evolution_log markdown 内容）
```

LLM 看到该文件 + 下一次跑 `/monthly-strategy` 时必须基于训练窗口重新设计。

## 7. Research CLI

### 7.1 命令签名

```bash
python3 -m stock_analyze backtest \
  --agent <claude|codex> \
  --start <YYYY-MM-DD> \
  --end <YYYY-MM-DD> \
  --overlay <path-to-yaml> \
  --output <out-dir> \
  [--in-memory] \
  [--universe hs300|zz500|both] \
  [--report-format markdown|json|csv]
```

### 7.2 输出结构

```
data/<agent>/backtest/<run_id>/
├── daily_nav.csv                # 与 forward 同 schema
├── trades.csv                   # 与 forward 同 schema
├── signals.csv                  # 每周 top50 选股
├── factor_runs/                 # 每周因子明细，与 forward 同 schema
│   └── <YYYY-MM-DD>.csv
├── performance_summary.json     # 全套绩效指标
├── report.md                    # 人类可读分析报告
└── meta.json                    # overlay snapshot / cmd / git_sha / duration
```

### 7.3 报告样例

```markdown
# 回测报告 · claude · 2023-01-01 → 2024-12-31

## 总结
- 累计收益: +18.3% (沪深300: +12.1%, 超额 +6.2pp)
- 年化收益: +8.7%
- Sharpe: 1.4
- 最大回撤: -8.7%（发生于 2024-02-05 ~ 2024-02-20）
- 信息比率 vs 沪深300: 0.92
- 周换手率: 23%
- 累计成本: 1.8% (含佣金 + 印花税 + 滑点)

## 因子贡献分解
| 因子 | 累计贡献 | 标注 |
|---|---|---|
| ROE | +4.2pp | 主要贡献 |
| momentum_60 | +3.1pp | 主要贡献 |
| gross_margin | +2.0pp | |
| pe | -0.8pp | 主要拖累 |
| ... | | |

## 月度热力图
（每月相对沪深300 超额，色块）

## 风险归因
- 单月最差: -7.3%（2024-02）
- 单月最佳: +9.1%（2023-11）
- 行业最大暴露: 计算机 28%（接近上限 30%）
```

## 8. Dashboard 集成

### 8.1 新面板：历史回测 vs 真实运行

**仅在专业版**（`reports/<agent>/dashboard.html` + 聚合页 Claude / Codex tab）。

布局：

```
┌─ 历史回测 vs 真实运行 ─────────────────────────────────────┐
│  [双线图]                                                   │
│    · 浅色实线（淡蓝）：当前 overlay 在 2021-2026-04 回测 NAV│
│    · 深色实线（蓝）：当前 overlay 在 2026-05+ 真实 NAV      │
│    · 灰色虚线：沪深 300 同窗口基准                          │
│                                                             │
│  历史回测（4+1 年）：年化 +8.7% / Sharpe 1.4 / 最大回撤 -8.7%│
│  真实运行（截至今天）：年化 +X.X% / Sharpe Y.Y               │
│  差异: +/- N pp                                              │
│                                                             │
│  [小提示] 差异 > ±5pp 时显示橙色警示                         │
└────────────────────────────────────────────────────────────┘
```

### 8.2 策略演进时间线扩展

现有列：月份 / 状态 / from→to hash / diff 摘要 / 思考摘要 / evolution_log / 当月收益 / 次月收益

**新增列**：**该月验证回测指标**（累计 / Sharpe / 最大回撤 三个数字）

让操作员能看到"LLM 当月选这个 overlay 时回测说会赚 X，实际跑出 Y"。

### 8.3 新手 dashboard

**不显示**回测相关内容。`≤80KB` anti-goal 不能破。

## 9. 工程边界（YAGNI）

**MVP 范围**：
- ✅ 单 overlay 回测
- ✅ hs300 ∪ zz500 双指数股票池
- ✅ 训练 / 验证 / live OOS 三段窗口
- ✅ Gate 三条底线（max DD / Sharpe / 累计）
- ✅ Research CLI 基础参数（start / end / overlay / output / in-memory / universe）
- ✅ 数据预热 CLI（幂等）
- ✅ Dashboard 双线对比面板
- ✅ 策略演进时间线扩展列

**MVP 不做**（明确列入后续 change）：
- ❌ Walk-forward CV
- ❌ 多场景压力测试（`--scenario stress`）
- ❌ 双 overlay 同窗口对决（`--compare`）
- ❌ 因子分位 IC（quintile portfolio）
- ❌ 自动重拉 Tushare 数据（需手动跑 prepare-backtest-data）
- ❌ 行业 / 风格暴露归因
- ❌ 单股级别贡献分析报告
- ❌ 回测期间的 forward IC 重算（保留与 forward 一致的实现）

## 10. 风险与限制

### 10.1 过拟合到验证窗口

- 风险：LLM 通过反复演化反向探出验证窗口的最优 overlay
- 缓解 1：信息隔离（briefing 只显示 5 个聚合指标）
- 缓解 2：底线只拦灾难（不诱导 LLM 卡线）
- 残余风险：验证窗口越用越脏，~12 个月后可能需要重新切分

### 10.2 数据 regime 变化

- 风险：2021-2024 训练窗口的市场风格可能与 2026+ 不同（A 股监管 / 北上资金动向 / 行业轮动）
- 立场：回测不是预言，只是排查"明显糟糕"的工具。底线设置宽松（max DD 25% 而不是 10%）正是为此。

### 10.3 历史数据完整性

- Tushare 在退市股的最后几天有时数据不完整
- 缓解：退市日按可得最后一价清仓，差异计入成本而非报错

### 10.4 调仓日历差异

- 历史的某些周五是节假日（清明 / 国庆等），调仓日要顺延到下一交易日
- `is_signal_day(date)` 内部封装该规则，与 forward 同实现

### 10.5 Tushare 配额

- 5 年预热预估 ~3000 次 API 调用
- Tushare 2000 积分上限 500 次/分钟 — 配额充裕
- 预热分批跑，约 15 分钟完成
- 一次性投资，后续无网络压力

### 10.6 in_memory 模式内存占用

- 4 年回测 × 1000 票 × 多字段 ≈ 估算 500MB 峰值内存
- 在常规开发机 / ECS 上都不是问题
- 如果未来扩大到 10 年 / 全 A 股，考虑 chunked 处理

## 11. 不在本设计范围

- 不改 daily / weekly 流程
- 不改 factor pipeline / portfolio controls / performance 模块（这些被复用，但不修改）
- 不引入新数据源（仅 Tushare Pro + Baostock fallback）
- 不修改 forward simulation 的输出 schema
- 不调整 baseline 锁字段
- 不动 codex.yaml（codex 用自己的 overlay 跑回测，但代码改动是双方共享的）

## 12. 实施顺序建议

详见 `tasks.md`。粗粒度：

1. 数据预热模块 + CLI（先把数据准备好）
2. simulator 时钟参数化（小重构）
3. engine.run_backtest 主循环
4. Research CLI（先有手动可用版本）
5. gate.validate_overlay_via_backtest + evolution_writer 集成
6. Dashboard 面板 + 演进时间线列
7. 文档（CLAUDE.md / AGENTS.md / system-overview.md / 新增 historical-backtest-flow.md）
8. 端到端测试
