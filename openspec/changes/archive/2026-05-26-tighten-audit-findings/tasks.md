## 1. OpenSpec Foundation

- [x] 1.1 `openspec new change tighten-audit-findings`。
- [x] 1.2 写 proposal.md 与 design.md，说明 10 项审计发现中的处置（F4 撤销）。
- [x] 1.3 2 capability specs + 1 modified capability spec。
- [x] 1.4 `openspec validate --strict` 通过。

## 2. F1 — Pure-memory overlay validation

- [x] 2.1 `competition.validate_overlay(agent_id, overlay, repo_root, baseline=None)` 新增。
- [x] 2.2 `proposal_judge._validate_merged_overlay` 退化为一行调用 `competition.validate_overlay`。
- [x] 2.3 `tests/test_competition.py` 加 2 个用例：`test_validate_overlay_does_not_touch_disk`、`test_validate_overlay_rejects_locked_field`。
- [x] 2.4 既有 proposal_judge / apply 测试继续通过。

## 3. F2 — `.gitignore` 精确化

- [x] 3.1 `.gitignore`：`.claude/` → `.claude/worktrees/`、`.claude/cache/`、`.claude/agents-cache/`。
- [x] 3.2 `.claude/commands/*.md` 不再受 ignore 规则约束。

## 4. F3/F8 — 文档

- [x] 4.1 README 顶部加 "**systemd timer 二选一**" 提示。
- [x] 4.2 `docs/forward-simulation-runbook.md` systemd 章节加 "二选一" 警示。
- [x] 4.3 `docs/competition-runbook.md` systemd 章节加 "二选一" 警示 + 老 timer disable 提示。
- [x] 4.4 `docs/competition-runbook.md` 增 `configs/agents/_history/` 是审计目录的说明段。

## 5. F5/F10 — Dashboard 加列 + drift 标记

- [x] 5.1 `render_strategy_evolution_panel`：表头从 8 列扩为 9 列（加 "预期效果"）。
- [x] 5.2 行渲染：在 `rationale` 后插入 `<td>{expected_html}</td>`。
- [x] 5.3 `_proposal_hash_drift(proposal, decision)`：用 `proposal_judge._hash_mapping` 算当前 hash，与 decision.proposal_hash 比对。
- [x] 5.4 drift 时在 `decision_status` 后追加 ` · 提案已变`，`<tr>` 加 `proposal-drift` class，`<td class="decision-cell">` 加红色。
- [x] 5.5 CSS `tr.proposal-drift td.decision-cell { color: var(--red); font-weight: 600 }`。
- [x] 5.6 `tests/test_reporting_panels.py` 新增 `test_expected_effect_column_is_rendered` 与 `test_proposal_hash_drift_is_flagged`。

## 6. F9 — systemd ExecStartPost 容错

- [x] 6.1 `deploy/systemd/stock-analyze-monthly-review.service`：把 `agent-judge-proposals` 与 `agent-apply-approved-proposals` 改为 `ExecStartPost=-...`。
- [x] 6.2 `competition-dashboard` 保持无 `-`（严格）。
- [x] 6.3 注释解释意图。

## 7. F6/F7 — 代码卫生

- [x] 7.1 删 `tests/test_performance_metrics.py:31` 未用 `ann_ret_zero_rf`、移除未用 `math`。
- [x] 7.2 `stock_analyze/agent_briefing.py`：去掉 `Iterable`、`safe_float`。
- [x] 7.3 `stock_analyze/dashboard_aggregator.py`：去掉 `format_money`。
- [x] 7.4 `stock_analyze/diagnostics.py`：去掉 `numpy as np`。
- [x] 7.5 `stock_analyze/monthly_review.py`：去掉 `fmt_money` 与 `format_money` import。
- [x] 7.6 `stock_analyze/portfolio_controls.py`：去掉 `date`、`timedelta`。
- [x] 7.7 `stock_analyze/run_ledger.py`：去掉 `json`、`os`、`traceback`。
- [x] 7.8 `stock_analyze/simulator.py`：去掉 `write_json`。
- [x] 7.9 `tests/test_agent_briefing.py` / `test_competition.py` / `test_factor_diagnostics.py` / `test_monthly_review.py`：去掉未用 import。
- [x] 7.10 `python3 -m pyflakes stock_analyze/*.py tests/*.py` 输出干净（0 警告）。

## 8. 验证

- [x] 8.1 `python3 -m py_compile stock_analyze/*.py tests/*.py`。
- [x] 8.2 `python3 -m unittest discover -s tests`：85/85 通过（81 既有 + 4 新）。
- [x] 8.3 `openspec validate tighten-audit-findings --strict`。
- [x] 8.4 pyflakes 输出 0 警告。
- [x] 8.5 `git status` 与 `.gitignore` 改动后没暴露意外文件。

## 9. 发布

- [ ] 9.1 `git commit` + `git push origin HEAD:main`。
- [ ] 9.2 parent worktree `git pull --ff-only`。

## Completion Checklist

- [x] 全部 8 项 Phase 通过。
- [x] 85 测试全绿（含 4 个新用例）。
- [x] pyflakes 0 警告。
- [x] 5 文档（README + 2 runbook + dashboard 说明 + _history 段）一致。
- [ ] push 完成（task 9）。
