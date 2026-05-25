## Why

当前竞赛系统**没有任何回测能力**：

- `stock_analyze/` 下无 backtest 模块、无 CLI 子命令
- 所有诊断指标（NAV / IR / forward IC）都来自前向真实运行的累积，最早只到竞赛起跑日 `2026-05-18`
- 截至 2026-05-25，可用样本约 5 个交易日

LLM 月度演化目前在以下条件下决策：

| 维度 | 现状 |
|---|---|
| 历史样本 | ~20 个交易日 |
| 决策自由度 | 完全自由（守卫只挡 schema + 锁字段） |
| 反馈周期 | 1 个月（必须等下个月真实跑出来才知道好坏） |
| 准入门控 | 无（任意通过 guard 的 overlay 都能 commit） |

→ **20 个样本 + 完全自由 + 1 个月反馈周期 = 系统性裸奔**。LLM 的策略好坏只能等"未来运行结果"裁定。

历史上有过一次回测尝试（`add-historical-backtest-baseline`，commit `3a6b25e`），基于 Baostock，被 `bb05f0f` 删除，理由："Tushare Pro 已经提供完整历史 OHLCV + peTTM + pbMRQ，应该基于 Tushare 重起一份"。这正是本 change 的位置。

## What Changes

### 1. 新增 `stock_analyze/backtest/` 模块

五个文件：

- `engine.py` — 核心：`run_backtest(overlay, start, end, universe, data_root, out_dir) -> BacktestResult`。复用 `simulator.py` 驱动按交易日循环
- `data_prep.py` — `prepare-backtest-data` CLI 子命令，一次性预热 Tushare 5 年历史到 `data/shared/backtest_cache/`
- `data_view.py` — `PointInTimeView` 数据访问层，封装"时间 t 时刻只能看 t 之前数据"的规则
- `gate.py` — `validate_overlay_via_backtest(overlay)` 在验证窗口跑回测 + 检查硬底线
- `report.py` — 渲染 markdown 研究报告 + dashboard fragment

### 2. simulator.py 时钟参数化

现有 `simulator.execute_due_orders()` / `update_nav()` / `generate_rebalance_orders()` 内部用 `datetime.now()`。改为可选参数 `as_of`：

- 前向调用：默认 `as_of=date.today()`，**行为不变**
- 回测调用：`as_of=<历史日期>`，按驱动序列传入

这是个**最小化改造**（~3 处文件 I/O 路径参数化），不引入新逻辑。

### 3. evolution_writer 集成 backtest gate

`evolution_writer.write_evolution()` 当前流程：

```
guard.validate(new_overlay) → backup _history → write yaml → write log + diff → append csv
```

新流程：

```
guard.validate(new_overlay)
  → backtest.gate.validate(new_overlay)      # 新增
  → backup _history → write yaml → ...
```

底线（三选一，可在 `competition.yaml` 调整）：
- 验证窗口最大回撤 ≤ 25%
- 验证窗口 Sharpe ≥ -0.5
- 验证窗口累计收益 ≥ -15%

任一跌穿 → raise `BacktestFloorBreach` → yaml 不写 → LLM 必须重新设计。

### 4. CLI 子命令

```bash
# 一次性数据预热
python3 -m stock_analyze prepare-backtest-data \
  --start 2021-01-01 --end 2026-04-30

# Research 前端
python3 -m stock_analyze backtest \
  --agent claude \
  --start 2023-01-01 --end 2024-12-31 \
  --overlay configs/agents/claude.yaml \
  --output data/claude/backtest/<run_id>/ \
  [--in-memory] [--universe hs300|zz500|both]
```

### 5. 时间窗口纪律

```
2021-01-01 ──── 2024-12-31 │ 2025-01-01 ── 2026-04-30 │ 2026-05-18 ──→
    训练窗口（48 个月）       │   验证窗口（16 个月）      │   live OOS
    LLM 自由探索              │   gate 准入判定用          │   真实竞赛
```

**信息隔离软约束**（写入 `CLAUDE.md` / `AGENTS.md`）：
- 训练窗口：LLM 可读全部明细
- 验证窗口：briefing 只显示 5 个总结指标，不显示月度明细、不显示因子分解
- live OOS：不存在数据可读

### 6. Dashboard 集成

专业版 dashboard 新增 **"历史回测 vs 真实运行"** 面板：
- 双线图：浅色历史回测 NAV / 深色 live NAV / 灰色 benchmark
- 数字对比：历史 vs live 的累计/年化/Sharpe/最大回撤
- 用途：操作员判断"该 overlay 的回测预测力"

策略演进时间线表新增 "该月回测验证指标" 列。

新手 dashboard **不显示**回测内容（≤80KB anti-goal）。

## Capabilities

### New Capabilities

- `historical-backtest-engine` — `run_backtest()` 库函数 + 数据预热 + 输出 schema 与 forward 同
- `backtest-floor-gate` — `evolution_writer` 集成回测底线检查 + `BacktestFloorBreach` 异常 + 失败时的 yaml 回滚
- `backtest-research-cli` — `backtest` / `prepare-backtest-data` 两个 CLI 子命令
- `train-validation-window-discipline` — 文档级信息隔离约束（briefing 渲染时实施）

### Modified Capabilities

- `multi-agent-runtime` 中 `simulator` 接口扩展（`as_of` 可选参数），向后兼容
- `competition-baseline-fairness` 中 `competition.yaml` 新增 `backtest.floor.*` 三字段（**不**列入锁字段，操作员可调）
- `dashboard` 专业版新增回测对比面板与演进时间线新列

## Impact

- **代码**：1 新模块（4 文件，~1500 行）+ simulator 接口参数化（~3 处）+ evolution_writer 集成（~50 行）+ CLI 加 2 子命令 + 1 dashboard 面板。
- **配置**：`competition.yaml` 新增 `backtest.floor.{max_drawdown, sharpe_floor, cum_return_floor}` 三字段，默认值 `{0.25, -0.5, -0.15}`。
- **数据 / 产物**：
  - 新增 `data/shared/backtest_cache/`（一次性预热，~200MB）
  - 新增 `data/<agent>/backtest/<run_id>/`（每次 research 跑一次）
  - 新增 `configs/competition.yaml` 三个字段
- **网络**：一次性 ~3000 次 Tushare 调用（预热 5 年历史），分批跑 ~15 分钟。后续无新增网络压力（回测全部基于 backtest_cache）。
- **依赖**：无新增第三方包。复用 pandas / numpy / tushare。
- **失败模式**：
  - 预热中断 → 幂等续跑，已拉日期跳过
  - Gate floor breach → yaml 回滚，写 `<month>-floor-breach.md`
  - 验证窗口数据缺失 → 跑回测时报错，告知操作员先跑 prepare
- **文档**：
  - 新增 `docs/historical-backtest-flow.md` 解释三段窗口纪律
  - 更新 `docs/system-overview.md` §16（移除"不是回测系统"标注）+ §17（移除本 change 自身的路线图条目）
  - `CLAUDE.md` / `AGENTS.md` §9 加信息隔离规则
- **不在范围**：
  - Walk-forward CV / 多场景压力测试 / 双 overlay 对决 / 因子分位 IC / 行业风格暴露归因 / 自动重拉 Tushare（这些都列入"后续 change"）
  - 不引入新的第三方数据源
  - 不改 daily / weekly 现有流程

## 与已有 change 的关系

- `migrate-data-source-to-tushare-pro`（已落地）：本 change 是其下游红利 — Tushare 提供历史 OHLCV + daily_basic + fina_indicator + index_weight，无需新数据源
- `enable-llm-direct-strategy-evolution`（已落地）：本 change 在 `evolution_writer.write_evolution()` 后新增 gate 调用，不改其余流程
- `introduce-shared-market-data-pipeline`（已落地）：本 change 独立的 `backtest_cache/` 与 `data/shared/cache/` 平级，互不污染
- `add-beginner-dashboard-view`（已落地）：本 change 不动新手版

## Agent 来源声明

本 change 由 claude agent 在 2026-05-25 brainstorming session 中草拟，基于 human operator 与 claude 的对话决定：

1. 形态：C（共享 library + 两个前端）
2. 引擎：a（复用 simulator.py 驱动日期循环）
3. 股票池：a（真历史成分，避免幸存者偏差）
4. 时间窗口：b（4+1 年，2021-2024 训练 / 2025-2026-04 验证）

改动覆盖 `stock_analyze/`、`configs/competition.yaml`、`CLAUDE.md` / `AGENTS.md`、`docs/*.md`、`.claude/commands/monthly-strategy.md`，均在 `CLAUDE.md §7` 禁地列表 — **必须由 human operator 显式邀请实施**。

**Status：DRAFT，await confirmation。**
