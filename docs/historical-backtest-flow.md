# 历史回测引擎流程

> 由 OpenSpec change `add-historical-backtest-engine` 实施（已 archived 至
> `openspec/changes/archive/`）。详细设计见该 change 的
> `IMPLEMENTATION_REPORT.md` 与 `DATA_PIPELINE.md`。

回测引擎服务两个独立场景，共享同一个核心引擎（`stock_analyze/backtest/engine.py`）。

---

## 1. 两个场景

### 1a. Gate 场景（自动）

**触发**：LLM 每月跑 `/monthly-strategy` 写完新 overlay 时，`evolution_writer.write_evolution()` 内部自动调
`backtest.gate.validate_overlay_via_backtest()`。

**作用**：在 **验证窗口（2025-01-01 ~ 2026-04-30）** 跑一遍新 overlay 的回测，检查三条硬底线：

| 阈值 | 默认值 | 来源 |
|---|---|---|
| `max_drawdown` ≤ | 25% | `configs/competition.yaml.backtest.floor.max_drawdown` |
| `sharpe` ≥ | −0.5 | `...sharpe_floor` |
| `cum_return` ≥ | −15% | `...cum_return_floor` |

**失败**：抛 `BacktestFloorBreach(breach_type, metrics)` → 写
`data/<agent>/evolution_log/<month>-floor-breach.md` → yaml 不变 → LLM 必须重新设计。

**成功**：metrics 注入 `evolution_diff/<month>.json` 的 `backtest_metrics` 字段，正常 commit。

### 1b. Research 场景（手动）

**触发**：操作员手动跑 CLI。

```bash
python3 -m stock_analyze backtest \
  --agent claude \
  --start 2023-01-01 --end 2024-12-31 \
  --overlay configs/agents/claude.yaml \
  --output data/claude/backtest/<run_id>/ \
  [--in-memory] [--universe hs300|zz500|both]
```

**输出**（与 forward simulator 同 schema）：
- `daily_nav.csv`
- `trades.csv`
- `signals.csv`
- `performance_summary.json`
- `report.md`

---

## 2. 三段窗口纪律

```
2021-01-01 ──── 2024-12-31 │ 2025-01-01 ── 2026-04-30 │ 2026-05-18 ──→
    训练窗口（48 个月）       │   验证窗口（16 个月）      │   Live OOS
    LLM 自由探索              │   gate 准入判定用          │   真实竞赛
```

**信息隔离**（软约束，code 不强制；通过 briefing 渲染密度控制实施）：

- 训练窗口：LLM 可读月度明细 / 因子贡献 / 单股贡献
- 验证窗口：briefing 仅显示 5 个聚合指标（累计 / 年化 / Sharpe / 最大回撤 / IR）
- Live OOS：尚未发生，无数据可读

实施位置：`agent_briefing.render_training_section` / `render_validation_section`。

---

## 3. 数据预热（一次性）

`prepare-backtest-data` 拉 5 年 Tushare 数据到 `data/shared/backtest_cache/`：

```bash
python3 -m stock_analyze prepare-backtest-data \
  --start 2021-01-01 --end 2026-04-30
```

预估：~15 分钟、~3000 次 API 调用、~200MB 缓存。幂等续跑（中断后重跑只补缺失）。

7 个 Tushare endpoint：
- `pro.trade_cal` / `pro.stock_basic`（一次性）
- `pro.daily` / `pro.daily_basic`（每交易日）
- `pro.fina_indicator` / `pro.adj_factor`（每股）
- `pro.index_weight`（每月 hs300 + zz500）

数据落到独立目录 `data/shared/backtest_cache/`，不污染前向 cache。

---

## 4. 引擎实现要点

复用 `simulator.py` 驱动日期循环：
- 参数化 `as_of` / `data_root` / `market_data_root` 三个 kwarg（前向调用默认 None → 行为不变）
- 引擎按交易日序列调 `execute_due_orders` / `update_nav` / `generate_rebalance_orders`
- `BacktestProvider` 是 `DataProvider` 的薄子集（5 个方法），通过 `PointInTimeView` 防未来泄漏

**MVP 简化**：当前回测的信号生成是 low-PE top-N，未走完整 factor_pipeline。完整桥接是
后续 OpenSpec change `bridge-factor-pipeline-into-backtest-engine`。Gate 的灾难底线
仍有效（防 max DD > 25% 等），但"该 overlay 历史会跑出多少 Sharpe" 暂时不准确。

---

## 5. 操作员用法

### 5.1 首次部署
```bash
export TUSHARE_TOKEN=<token>
python3 -m stock_analyze prepare-backtest-data --start 2021-01-01 --end 2026-04-30
```

### 5.2 验证 overlay
```bash
python3 -m stock_analyze backtest \
  --agent claude --start 2023-01-01 --end 2024-12-31 \
  --overlay configs/agents/claude.yaml \
  --output data/claude/backtest/sanity-$(date +%Y%m%d)/
cat data/claude/backtest/sanity-*/report.md
```

### 5.3 应急回滚
若 gate 错杀，调整 `competition.yaml.backtest.floor.*` 阈值或 `agent-rollback --to <hash>`。
