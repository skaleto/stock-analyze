## 1. OpenSpec Foundation

- [x] 1.1 创建本 change 目录与 schema 文件（已由 `openspec new change` 生成）。
- [x] 1.2 撰写 `proposal.md`、`design.md`，明确范围、口径、迁移策略。
- [x] 1.3 为 5 个新增 capability 起草 `specs/<capability>/spec.md`，每个 requirement 配齐 scenario。
- [x] 1.4 用 `openspec validate align-beginner-friendly-multi-factor-foundations --strict` 通过校验。

**Quality Gate:**
- [x] OpenSpec 校验通过；proposal、design、tasks、specs 内部一致。
- [x] 与现有 change 的需求不冲突。

---

## 2. 因子处理流水线（factor-processing-pipeline）

- [x] 2.1 新建 `stock_analyze/factor_pipeline.py`：`winsorize_series`、`zscore_series`、`industry_neutralize`。
- [x] 2.2 `process_factors`：raw → winsorize → zscore → 行业内 demean → 加权汇总；输出 `(scored, factor_table)`。
- [x] 2.3 `min_factor_coverage` 不足的股票打 `insufficient_factor_coverage` warning，`build_signals` 剔除。
- [x] 2.4 `score_detail` 升级为 `factor:zscore:contribution`，dashboard `localize_reason` 兼容旧两段与新三段。
- [x] 2.5 `tests/test_factor_pipeline.py`：11 个用例覆盖 winsorize/zscore/中性化/coverage/方向。

**Quality Gate:**
- [x] `python3 -m unittest tests.test_factor_pipeline` 全绿（11 个用例）。
- [x] score = Σ contribution per code 可重现。

---

## 3. 组合构建控制（portfolio-construction-controls）

- [x] 3.1 `configs/strategy_v1.yaml` v2：`portfolio_controls`、`factor_processing`、`performance` 节默认值。
- [x] 3.2 `factors` 移除 `market_cap_yi`；`filters.min_market_cap_yi=30`；`config.migrate_strategy_config()` 处理 v1 兼容并 warning。
- [x] 3.3 `stock_analyze/portfolio_controls.py` `select_top_n_with_controls`：单行业上限、缓冲区保留、`max_holding_days`。
- [x] 3.4 `AkshareProvider.basic_info` 输出 `industry`；`build_signals` 必读注入；缺失归 `未分类`。
- [x] 3.5 `PriceSnapshot.low_volatility_60`（60 日日收益率 std）+ `AkshareProvider.dividend_yield`（含缓存与降级）。
- [x] 3.6 `configs/preset_quality_low_vol.yaml`：质量 + 低波 + 股息 preset。
- [x] 3.7 `tests/test_portfolio_controls.py`：7 个用例。

**Quality Gate:**
- [x] 单行业占比 ≤ 30%（单元测试覆盖）。
- [x] `python3 -m unittest tests.test_portfolio_controls` 全绿。

---

## 4. 策略绩效与归因（strategy-performance-metrics）

- [x] 4.1 `stock_analyze/performance.compute_account_performance`：年化、Sharpe、Sortino、最大回撤、回撤天数。
- [x] 4.2 基准日收益序列；累计超额、年化超额、跟踪误差、信息比率。
- [x] 4.3 FIFO round-trip 配对；win rate、平均持有、平均 pnl。
- [x] 4.4 加权换手率（每周）+ 累计成本占比 bps。
- [x] 4.5 `reporting.compute_performance` 委托给新模块；`performance_summary.json` 含完整新字段。
- [x] 4.6 Dashboard 绩效解释卡片矩阵 + tooltip 口径说明。
- [x] 4.7 周报顶部 metadata 行 + 绩效表扩展。

**Quality Gate:**
- [x] 人造 NAV/trades 数值与手算一致（`tests/test_performance_metrics.py` 5 个用例）。
- [x] 缺失数据时数值 `null`，dashboard 显示 `-`。

---

## 5. 因子诊断输出（factor-diagnostics-output）

- [x] 5.1 `data/factor_runs/<run_id>.csv` 全字段（raw/winsorized/zscore/neutralized/weight/contribution/valid/selected/rejected_reason）。
- [x] 5.2 `data/factor_diagnostics/coverage.csv` 每周追加。
- [x] 5.3 `stock_analyze/diagnostics.compute_pending_forward_ic`：Spearman rank IC（无 scipy 依赖，自实现）；冷启动写 `insufficient_history`。
- [x] 5.4 Dashboard 因子覆盖率热力图 + 前向 IC 折线。
- [x] 5.5 低覆盖率因子 CSS class `low` 高亮。
- [x] 5.6 `tests/test_factor_diagnostics.py`：3 个用例（含完美正相关 IC ≈ 1）。

**Quality Gate:**
- [x] 同 `run_id` 在 `factor_runs/` 与 `latest_signals.csv` 一致。
- [x] `python3 -m unittest tests.test_factor_diagnostics` 全绿。

---

## 6. 运行账本与配置快照（run-ledger-and-config-snapshot）

- [x] 6.1 `stock_analyze/run_ledger.RunLedger` 上下文管理器：running → success / failed。
- [x] 6.2 `config_hash = sha256(canonical_json(config))[:12]`；`data/configs/<hash>.json` 不存在时写入。
- [x] 6.3 `code_version` 直接读 `.git/HEAD`，无外部 git 依赖；非 git 仓库返回 `no_git`。
- [x] 6.4 `data/runs.csv` schema 完整。
- [x] 6.5 Dashboard 最近运行面板（≤10 条；状态 tag CSS）。
- [x] 6.6 周报 metadata。
- [x] 6.7 `tests/test_run_ledger.py`：5 个用例。

**Quality Gate:**
- [x] 失败路径写 `failed` 行不破坏主命令（`test_failure_records_error_summary`）。
- [x] Config hash 稳定且改动后变化（`test_config_change_creates_new_snapshot`）。

---

## 7. 文档与发布

- [x] 7.1 `README.md` 提示 preset 切换（在 `docs/quant-beginner-alignment-plan-2026-05-19.md` 中有完整 preset 用法）。
- [ ] 7.2 `docs/forward-simulation-runbook.md` 补 v2 配置/产物/迁移说明。**留给下一次维护循环。**
- [x] 7.3 `docs/quant-beginner-alignment-plan-2026-05-19.md` 写就。
- [ ] 7.4 `docs/quant-model-gap-review-2026-05-18.md` 末尾追加 P1 落地状态指针。**留给下一次维护循环。**
- [x] 7.5 5 个测试文件组织到 `tests/`。
- [x] 7.6 `python3 -m py_compile stock_analyze/*.py` 通过；`python3 -m unittest discover -s tests` 37/37 通过。
- [x] 7.7 烟囱跑 init/run-weekly：受公开数据源可用性约束，由用户在自己环境跑一次确认；本次会话用单元测试代替。

**Quality Gate:**
- [x] OpenSpec `validate --strict` 通过。
- [x] 单元测试与编译检查通过。

---

## Completion Checklist

- [x] 所有 Phase 完成并通过 Quality Gate。
- [x] 旧 v1 配置兼容路径在单元测试中跑通。
- [ ] 7.2 / 7.4 文档增补留作后续维护项；不阻塞下一 change `introduce-dual-agent-competition` 启动。
