# tasks · add-historical-backtest-engine

## 1. OpenSpec foundation

- [ ] 1.1 proposal.md / design.md / tasks.md 落
- [ ] 1.2 加 specs/ 子目录，每个新 capability 一份 spec：
  - [ ] specs/historical-backtest-engine/spec.md
  - [ ] specs/backtest-floor-gate/spec.md
  - [ ] specs/backtest-research-cli/spec.md
  - [ ] specs/train-validation-window-discipline/spec.md
- [ ] 1.3 `openspec validate add-historical-backtest-engine --strict` 通过
- [ ] 1.4 human operator confirm

## 2. 数据预热模块

- [ ] 2.1 新文件 `stock_analyze/backtest/__init__.py`
- [ ] 2.2 新文件 `stock_analyze/backtest/data_prep.py`，exposing `prepare_backtest_data(start, end, force=False) -> None`
- [ ] 2.3 实现 7 个 Tushare 接口的批量拉取：
  - [ ] daily / daily_basic（按日批量）
  - [ ] fina_indicator / adj_factor（按股批量）
  - [ ] index_weight（hs300 + zz500，按月）
  - [ ] stock_basic / trade_cal（单次）
- [ ] 2.4 写入 `data/shared/backtest_cache/` 各子目录
- [ ] 2.5 维护 `_meta.json` 进度记录，幂等续跑
- [ ] 2.6 CLI 子命令 `prepare-backtest-data --start --end [--force]`
- [ ] 2.7 单元测试：模拟 Tushare 响应，验证写盘结构 + 幂等性

## 3. simulator.py 时钟参数化

- [ ] 3.1 给 `execute_due_orders` / `update_nav` / `generate_rebalance_orders` 加 `as_of` / `data_root` 可选参数（默认 None 走原行为）
- [ ] 3.2 内部 `datetime.now()` 调用替换为 `as_of or datetime.now().date()`
- [ ] 3.3 内部 `data/shared/cache/` 路径替换为参数化 `data_root`
- [ ] 3.4 现有 forward 调用零改动（默认参数走旧行为）
- [ ] 3.5 单元测试：用 fake date 驱动 simulator，验证 NAV 序列正确

## 4. point-in-time 数据访问层

- [ ] 4.1 新文件 `stock_analyze/backtest/data_view.py`，exposing `PointInTimeView(date, data_root)`
- [ ] 4.2 实现 `daily(date)` / `daily_basic(date)` / `fina(ts_code, as_of)` / `industry(ts_code, as_of)` / `universe(date)`
- [ ] 4.3 财报数据按 `ann_date <= date` 过滤
- [ ] 4.4 指数成分按月度快照取最近一份
- [ ] 4.5 退市股按 `list_date <= date < (delist_date or +inf)`
- [ ] 4.6 单元测试：构造历史 mock 数据，验证不会泄漏未来信息

## 5. 回测引擎主循环

- [ ] 5.1 新文件 `stock_analyze/backtest/engine.py`，exposing `run_backtest(...) -> BacktestResult`
- [ ] 5.2 实现交易日循环：`execute_due_orders → update_nav → (Friday: generate_rebalance_orders)`
- [ ] 5.3 实现 `in_memory` 模式（不每日写盘）
- [ ] 5.4 输出 schema 与 forward 完全一致：
  - [ ] daily_nav.csv
  - [ ] trades.csv
  - [ ] signals.csv
  - [ ] performance_summary.json
  - [ ] factor_runs/*.csv
- [ ] 5.5 meta.json 记录 overlay snapshot / cmd / git_sha / duration
- [ ] 5.6 单元测试：跑一段 mock 数据（1 周交易日），验证输出文件齐全 + NAV 演化正确

## 6. Research CLI

- [ ] 6.1 `cli.py` 加 `backtest` 子命令
- [ ] 6.2 参数：`--agent / --start / --end / --overlay / --output / --in-memory / --universe / --report-format`
- [ ] 6.3 调用 `engine.run_backtest` + 渲染 markdown 报告
- [ ] 6.4 输出落在 `data/<agent>/backtest/<run_id>/`
- [ ] 6.5 单元测试：CLI 解析 + 调用链路（不真跑回测）

## 7. Markdown 报告渲染

- [ ] 7.1 新文件 `stock_analyze/backtest/report.py`，exposing `render_markdown_report(result) -> str`
- [ ] 7.2 实现 4 段：总结 / 因子贡献分解 / 月度热力图 / 风险归因
- [ ] 7.3 月度热力图用 markdown 表格（不依赖 HTML/SVG）
- [ ] 7.4 单元测试：给定固定 BacktestResult，渲染输出快照对比

## 8. Gate 实现

- [ ] 8.1 新文件 `stock_analyze/backtest/gate.py`，exposing `validate_overlay_via_backtest(overlay) -> Metrics`
- [ ] 8.2 调 `engine.run_backtest` with `in_memory=True` 跑验证窗口
- [ ] 8.3 检查三条底线，breach 时 raise `BacktestFloorBreach(breach_type, metrics)`
- [ ] 8.4 阈值读自 `competition.yaml.backtest.floor.*`
- [ ] 8.5 单元测试：3 种 breach + 1 个 happy

## 9. competition.yaml 加 backtest.floor 字段

- [ ] 9.1 加 `backtest.floor.max_drawdown` (default 0.25)
- [ ] 9.2 加 `backtest.floor.sharpe_floor` (default -0.5)
- [ ] 9.3 加 `backtest.floor.cum_return_floor` (default -0.15)
- [ ] 9.4 加载逻辑 `competition.py` 同步：可读，但不列入锁字段集合
- [ ] 9.5 单元测试：overlay 试图覆盖 backtest.floor → 不报错（非锁字段），但实际不生效（gate 仍读 competition.yaml）

## 10. evolution_writer 集成

- [ ] 10.1 在 `overlay_guard.validate` 之后插入 `gate.validate_overlay_via_backtest` 调用
- [ ] 10.2 BacktestFloorBreach 时 → 写 `data/<agent>/evolution_log/<month>-floor-breach.md`
- [ ] 10.3 BacktestFloorBreach 时 → yaml 不写，不动 _history
- [ ] 10.4 happy path → metrics 注入到 evolution_log + evolution_diff
- [ ] 10.5 单元测试：3 种 breach 场景 + 1 个 happy

## 11. agent_briefing 信息隔离

- [ ] 11.1 `monthly_review.py` 月度 review 完成后，自动跑一次训练窗口回测（用当前 live overlay），落 `data/<agent>/backtest/training/<YYYY-MM>/`
- [ ] 11.2 monthly briefing 加 `## 训练窗口表现` 段（读上面那次回测，detail_level=full）
- [ ] 11.3 monthly briefing 加 `## 验证窗口表现` 段（读 evolution_writer 上次跑的 `data/<agent>/backtest/validation/<latest>/`，detail_level=aggregate_only，只 5 个数字）
- [ ] 11.4 单元测试：验证 aggregate_only 模式不泄漏月度明细

## 12. Dashboard 集成

- [ ] 12.1 `reporting.py` 加 `render_backtest_vs_live_panel(agent_id) -> html`
  - [ ] 双线图：历史回测 NAV / live NAV / 沪深300
  - [ ] 数字对比表：累计/年化/Sharpe/最大回撤
  - [ ] 差异 > ±5pp 时橙色高亮
- [ ] 12.2 `reporting.render_strategy_evolution_panel` 新增 "验证回测指标" 列
- [ ] 12.3 dashboard_aggregator 把新面板嵌入 Claude / Codex tab（专业版）
- [ ] 12.4 新手版 dashboard 不动
- [ ] 12.5 单元测试：渲染输出快照对比

## 13. CLAUDE.md / AGENTS.md 更新

- [ ] 13.1 CLAUDE.md / AGENTS.md §9 加 "三段窗口纪律" 子节：训练全可见、验证仅聚合、live OOS 未来
- [ ] 13.2 CLAUDE.md / AGENTS.md §10 加 "回测信息隔离软约束" 说明
- [ ] 13.3 CLAUDE.md / AGENTS.md §5b（月度演化）流程更新：commit 前增加 backtest gate 步骤

## 14. 系统文档

- [ ] 14.1 新增 `docs/historical-backtest-flow.md` 完整流程说明（含三段窗口 + gate 集成 + research CLI 用法 + 数据预热）
- [ ] 14.2 `docs/system-overview.md` §1 移除 "不是回测系统" 标注
- [ ] 14.3 `docs/system-overview.md` §16（限制与不在范围）移除"历史回测留给后续 change"
- [ ] 14.4 `docs/system-overview.md` §17（路线图）移除 `add-historical-backtest-baseline` 条目，加 walk-forward CV / 多场景压力测试 等后续 change

## 15. slash command 更新

- [ ] 15.1 `.claude/commands/monthly-strategy.md` 加入说明：commit 前会自动跑 backtest gate，若 breach 会 yaml 回滚
- [ ] 15.2 加入说明：可以提前手动跑 `python3 -m stock_analyze backtest` 验证新 overlay 想法

## 16. 测试

- [ ] 16.1 单元测试覆盖：data_prep / data_view / engine / report / gate
- [ ] 16.2 端到端测试：模拟一个完整月度演化（LLM 改 yaml → guard pass → gate pass → write_evolution → assert 6 个产物落盘）
- [ ] 16.3 端到端测试：gate breach 场景（assert yaml 未变、breach log 已写）
- [ ] 16.4 全部 unittest 通过 + pyflakes 0 + openspec validate --strict 通过

## 17. e2e 验证（手动）

- [ ] 17.1 跑 `prepare-backtest-data --start 2021-01-01 --end 2026-04-30` 完整预热（~15 分钟）
- [ ] 17.2 跑 `backtest --agent claude --start 2023-01-01 --end 2024-12-31 --overlay configs/agents/claude.yaml`（research 模式），检查报告合理
- [ ] 17.3 用 **测试 fixture overlay**（不动 live `configs/agents/claude.yaml`）实测 gate 集成：故意构造一个会 breach 的 overlay，调用 `evolution_writer.write_evolution` 时传入 `--dry-run` 或写到 fixture 目录，验证 yaml 不变 + breach log 写出。**禁止使用真实 LLM `/monthly-strategy` 流程做 breach 实验**（会污染 live audit trail）
- [ ] 17.4 dashboard 渲染：检查新面板显示正常

## 18. 不在范围

- ❌ Walk-forward CV
- ❌ 多场景压力测试（`--scenario stress`）
- ❌ 双 overlay 同窗口对决（`--compare`）
- ❌ 因子分位 IC（quintile portfolio）
- ❌ 自动重拉 Tushare 数据
- ❌ 行业 / 风格暴露归因
- ❌ 单股级别贡献分析报告
- ❌ 不动 daily / weekly 流程
- ❌ 不动 factor pipeline / portfolio controls / performance（复用，不修改）
- ❌ 不动 forward simulation 输出 schema
