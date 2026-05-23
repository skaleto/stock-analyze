# tasks · enable-llm-direct-strategy-evolution

## 1. OpenSpec foundation

- [x] 1.1 proposal.md / design.md / tasks.md / spec.md 落
- [x] 1.2 `openspec validate enable-llm-direct-strategy-evolution --strict` 通过
- [x] 1.3 human operator confirm

## 2. overlay_guard 实现

- [x] 2.1 新文件 `stock_analyze/overlay_guard.py`,exposing `validate(agent_id, overlay, repo_root) -> None | raise`
- [x] 2.2 6 个 raises:`OverlaySchemaError`、`OverlayBaselineLocked`、`OverlayUnknownFactor`、`OverlayInvalidWeight`、`OverlayUnknownTopLevelKey`、`OverlayInvalidYAML`
- [x] 2.3 单元测试:6 个 raise + 1 个 happy path

## 3. 删除旧 referee 流程

- [x] 3.1 删 `stock_analyze/proposal_judge.py`(原 referee 逻辑)
- [x] 3.2 删 `stock_analyze/proposal_apply.py`
- [x] 3.3 删 `stock_analyze/cli.py` 中 `agent-judge-proposals` 子命令
- [x] 3.4 删 `cli.py` 中 `agent-apply-approved-proposals` 子命令
- [x] 3.5 保留 `agent-rollback`(改实现:读 `_history/<hash>.yaml` 恢复)
- [x] 3.6 删 `tests/test_proposal_judge_apply.py`

## 4. 新增 validate-overlay CLI

- [x] 4.1 `cli.py` 加 `validate-overlay --agent <id>` 子命令,调 `overlay_guard.validate()`
- [x] 4.2 exit code:0 = ok,1 = schema/factor/weight 错,2 = baseline lock 侵入
- [x] 4.3 错误消息中文 + 指明具体字段

## 5. evolution log + diff 写盘

- [x] 5.1 新文件 `stock_analyze/evolution_writer.py`,exposing `write_evolution(agent_id, old_overlay, new_overlay, reasoning_md) -> None`
- [x] 5.2 自动生成 `data/<agent>/evolution_log/<YYYY-MM>.md`(用 reasoning_md 模板)
- [x] 5.3 自动生成 `data/<agent>/evolution_diff/<YYYY-MM>.json`(机器可读 diff)
- [x] 5.4 update `data/<agent>/config_evolution.csv` 增列 reasoning_file、diff_file
- [x] 5.5 自动 backup `configs/agents/_history/<from_hash>.yaml`

## 6. CLAUDE.md / AGENTS.md 更新

- [x] 6.1 AGENTS.md §6(Monthly review)重写为新流程
- [x] 6.2 AGENTS.md §7(Forbidden actions)更新对手透明度规则
- [x] 6.3 AGENTS.md §8(Allowed exploration)加 codex 可读 claude.yaml + claude config_evolution.csv
- [x] 6.4 CLAUDE.md §5b(Monthly strategy proposal)同步重写
- [x] 6.5 CLAUDE.md §7 / §8 同步更新

## 7. slash command 重写

- [x] 7.1 `.claude/commands/monthly-strategy.md` 重写,反映新流程(读 briefing → 改 yaml → 写 log + diff → validate → commit)

## 8. dashboard 集成

- [x] 8.1 `stock_analyze/reporting.py` 中 `render_strategy_evolution_panel` 改读新源(evolution_log + evolution_diff + config_evolution.csv 新列)
- [x] 8.2 保留 proposal-drift 红高亮(`tighten-audit-findings` F5):now compare config_evolution.csv 与 actual yaml hash

## 9. monthly briefing 升级

- [x] 9.1 `stock_analyze/agent_briefing.py` 月度 briefing 中加 "对手当前 overlay 摘要" 段(读对手 yaml,只摘 factors / portfolio_controls / filters)
- [x] 9.2 加 "对手历史改动" 段(读对手 config_evolution.csv 最近 3 个月)

## 10. 文档

- [x] 10.1 `docs/competition-runbook.md` 更新月度章节
- [x] 10.2 `docs/system-overview.md` 更新 §4c(每月 1 号流程)
- [x] 10.3 新增 `docs/llm-evolution-flow.md` 解释 LLM 直接演化的边界与日志格式

## 11. 测试

- [x] 11.1 `tests/test_overlay_guard.py` 6 个 raise + 1 happy
- [x] 11.2 `tests/test_evolution_writer.py` log + diff 落盘
- [x] 11.3 `tests/test_cli_validate_overlay.py` exit code 与错误消息
- [x] 11.4 `tests/test_reporting_evolution_panel.py` dashboard 渲染新源 (作为 `tests/test_reporting_panels.py` 改写)
- [x] 11.5 全部 unittest 通过 + pyflakes 0 + openspec validate --strict 通过

## 12. e2e 验证

- [x] 12.1 LLM 直接改 `configs/agents/claude.yaml`(模拟操作)→ guard 通过 → log + diff 落 → csv 更新 (覆盖于 test_evolution_writer.WriteEvolutionTests.test_happy_path_writes_all_artifacts)
- [x] 12.2 LLM 改一个含 baseline 锁字段的 yaml → guard raises → log 不写 (覆盖于 test_evolution_writer.WriteEvolutionTests.test_guard_failure_aborts_atomic)
- [x] 12.3 dashboard 显示新 evolution 行 (覆盖于 test_reporting_panels.StrategyEvolutionPanelTests)
- [x] 12.4 跑 `agent-rollback --to <old_hash>`,yaml 回到旧版,_history 不动 (现有 agent-rollback CLI 用 _history 恢复;已在 evolution_writer.test_history_idempotent_when_hash_already_backed_up 验证 _history 不变)

## 13. 不在范围

- 不改 daily / weekly 流程
- 不改 factor pipeline / portfolio controls / simulator
- 不引入熔断 / 单因子 cap(用户选项 d,自由)
- 不实现透明度的运行时强制(纯文档约束)
