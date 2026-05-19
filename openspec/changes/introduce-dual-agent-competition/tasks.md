## 1. OpenSpec Foundation

- [x] 1.1 创建本 change 目录（`openspec new change`）。
- [x] 1.2 写 `proposal.md`、`design.md`，明确范围与决策。
- [x] 1.3 4 个 capability `specs/<capability>/spec.md`，每个 requirement 配齐 scenario。
- [x] 1.4 `openspec validate introduce-dual-agent-competition --strict` 通过。

**Quality Gate:**
- [x] OpenSpec 校验通过。

---

## 2. 公平基线锁定（competition-baseline-fairness）

- [x] 2.1 `configs/competition.yaml`：competition_id、start_date、initial_cash、accounts (hs300+zz500 各 50 万 top_n=10)、schedule、trading、performance。
- [x] 2.2 `configs/agents/claude.yaml`（价值+质量+动量）+ `configs/agents/codex.yaml`（质量+低波+股息）。
- [x] 2.3 `stock_analyze/competition.py`：`BASELINE_LOCKED_PATHS` 常量；`load(agent_id)` 深合并 + lock check；`resolve_agent_paths`；`list_agents`。
- [x] 2.4 锁字段违反时 `raise CompetitionBaselineLocked(field=...)`；CLI 捕获并打印 `error: competition_baseline_locked:...`。
- [x] 2.5 `competition-init` 命令：检查 baseline + overlay 存在 → 创建 `data/{shared,claude,codex,competition}` 与 `reports/{claude,codex,competition}` → 各侧调用 `simulator.initialize(merged_config, store)` → 写 `data/competition/competition_metadata.json`。
- [x] 2.6 `tests/test_competition.py`：8 个用例覆盖 lock/合并/路径/未知 agent/init smoke。

**Quality Gate:**
- [x] `python3 -m unittest tests.test_competition` 8/8 通过。
- [x] overlay 覆盖 `initial_cash` / `trading.commission_rate` / `accounts` 全部抛 `CompetitionBaselineLocked`。

---

## 3. 多 agent 运行时（multi-agent-runtime）

- [x] 3.1 `cli.py` 加 `--agent` 参数；与 `--config/--data-dir/--reports-dir` 共存，显式优先。
- [x] 3.2 `--agent` 出现时 CLI 通过 `competition.resolve_agent_paths` 推导 config/data/reports + 共享 cache `data/shared/cache`。
- [x] 3.3 `init / rebalance / execute / update-nav / report / dashboard / run-daily / run-weekly` 全部兼容 `--agent`。
- [x] 3.4 不带 `--agent` 的旧调用路径继续工作；既有 37 个测试全部通过。
- [x] 3.5 `AkshareProvider` cache_dir 指向 `data/shared/cache`；`provider.persist_health` 写到 `data/shared/data_health.json`（实际写到 `cache_dir.parent / data_health.json`，与 shared 一致）。
- [x] 3.6 资源烟囱：`competition-init` 实际跑过，产出 `data/{shared,claude,codex,competition}` 与各侧 `state.json`。

**Quality Gate:**
- [x] 既有 37 个测试继续全绿。
- [x] `python3 -m stock_analyze competition-init` 实际跑通；`data/competition/competition_metadata.json` 有合理 baseline_hash。

---

## 4. 月度对比 review（monthly-comparison-review）

- [x] 4.1 `stock_analyze/monthly_review.py`：`compute_review(month, agents)` 与 `write_review(payload)`。
- [x] 4.2 对比指标：双方 cumulative_return / annualized_return / sharpe / sortino / max_drawdown / IR / tracking_error / weekly_turnover_avg / cost_bps / round_trip_win_rate / factor_ic_top3 / industry_exposure_top3 / active_factors / config_hash；comparison block 含 winner_cumulative_return / winner_information_ratio / spread_cumulative_return / position_overlap_ratio / daily_return_correlation / shared_factor_drivers / divergent_factor_drivers。
- [x] 4.3 `position_overlap_ratio` = |A ∩ B| / |A ∪ B|（最近一期 positions.csv code 集合）。
- [x] 4.4 `daily_return_correlation` = Pearson(claude_daily_return, codex_daily_return)，时间范围 = 本月日期交集。
- [x] 4.5 因子有效性对比从 `factor_diagnostics/forward_ic.csv` 取本月 IC 均值，每侧 top 3。
- [x] 4.6 `data/competition/monthly_reviews/<month>.json` + `reports/competition/monthly_review_<month>.md`（含 disclaimer + 9 行指标横向对比表 + 自动差异化建议段）。
- [x] 4.7 `data/competition/leaderboard.csv` 一行 upsert：month, claude_return, codex_return, winner_return, claude_ir, codex_ir, winner_ir, generated_at。
- [x] 4.8 `competition-monthly-review --month YYYY-MM` CLI 子命令；月份缺省取上个月。
- [x] 4.9 `tests/test_monthly_review.py`：6 个用例（默认月份/字段完整/胜方与差/Jaccard/Leaderboard upsert/Markdown disclaimer）。

**Quality Gate:**
- [x] `python3 -m unittest tests.test_monthly_review` 6/6 通过。
- [x] 月度报告含 winner_cumulative_return 与 winner_information_ratio 两个口径。

---

## 5. 聚合 dashboard（multi-agent-dashboard）

- [x] 5.1 `reporting.generate_dashboard(mode="page"|"fragment")`；fragment 模式输出 `dashboard_fragment.html`，含 `<section class="agent-dashboard" data-agent="...">` 与重命名后的元素 ID。
- [x] 5.2 单 agent 默认 `mode="page"`，行为完全不变。
- [x] 5.3 `stock_analyze/dashboard_aggregator.py`：`generate_competition_dashboard(agents, repo_root)` 输出三 tab。
- [x] 5.4 顶部 4 张卡片（双方累计收益、累计差、最近一月胜方）。
- [x] 5.5 对比 tab 含双线 NAV、9 行横向指标对比表、最近持仓重叠条、滚动战绩条、月度报告链接列表。
- [x] 5.6 三 tab 用 CSS `:target` 切换，无第三方前端框架。
- [x] 5.7 `competition-dashboard` CLI 子命令；`run-daily / run-weekly / dashboard` 现在同时产出 `dashboard.html` 与 `dashboard_fragment.html`。
- [x] 5.8 `--agent <id> run-weekly` 不再生成聚合 dashboard；要刷聚合页需单独 `competition-dashboard`。
- [x] 5.9 `tests/test_dashboard_aggregator.py`：3 个用例（三 tab 存在/Codex fragment 缺失 placeholder/Leaderboard 渲染）。

**Quality Gate:**
- [x] `reports/competition/dashboard.html` 含 `tab-claude` / `tab-codex` / `tab-compare` 三个锚点。
- [x] 各侧 fragment 中 sentinel 字符串能在聚合页里 grep 到。

---

## 6. 文档与运维

- [x] 6.1 `AGENTS.md`（Codex 入口）写就：身份、写入边界、不可改字段、周/月工作流、禁止动作、升级通道。
- [x] 6.2 `docs/competition-runbook.md` 写就：目录布局、CLI 速查、systemd 部署、月度流程、dashboard 访问、故障排查。
- [x] 6.3 README 顶部加 "双 agent 竞赛模式" 一段，指向 `docs/competition-runbook.md` 与 `AGENTS.md`。
- [x] 6.4 `deploy/systemd/` 新增 8 个文件：claude/codex × daily/weekly × service/timer，加 `monthly-review.service` + `monthly-review.timer`；现有 `stock-analyze-dashboard.service` 指向 `reports/competition`。

**Quality Gate:**
- [x] `AGENTS.md` 第一段明确身份与边界。
- [x] runbook 含可复制粘贴的 systemd 部署命令。

---

## 7. 验证与发布

- [x] 7.1 `python3 -m py_compile stock_analyze/*.py tests/*.py` 通过。
- [x] 7.2 `python3 -m unittest discover -s tests` 全绿（54 测试：37 既有 + 17 新增）。
- [x] 7.3 `openspec validate introduce-dual-agent-competition --strict` 通过。
- [x] 7.4 烟囱：本地跑 `competition-init` 与 `competition-dashboard` 全部通过；`competition-monthly-review` 由单元测试代验证（需要至少一周 NAV 数据才有实质内容）。
- [x] 7.5 PR-quality 总结：参见会话最末输出。

**Quality Gate:**
- [x] 三件套（编译、测试、openspec validate）通过。
- [x] 烟囱产物可访问；dashboard 三 tab 存在。

---

## Completion Checklist

- [x] 所有 Phase 完成并通过 Quality Gate。
- [x] `AGENTS.md` 与 `docs/competition-runbook.md` 落地。
- [x] 既有单 agent 模式回归测试通过。
- [ ] 准备好下一 change `enable-monthly-config-evolution` 的 proposal stub（学习模式 + patch 协议）。**留给下一次启动。**
