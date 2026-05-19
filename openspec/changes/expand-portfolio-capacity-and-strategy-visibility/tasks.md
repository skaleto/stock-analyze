## 1. OpenSpec Foundation

- [x] 1.1 `openspec new change expand-portfolio-capacity-and-strategy-visibility`。
- [x] 1.2 `proposal.md`、`design.md` 写就。
- [x] 1.3 2 个 capability `specs/<capability>/spec.md`：`competition-portfolio-capacity`、`agent-strategy-evolution-view`。
- [x] 1.4 `openspec validate --strict` 通过。

**Quality Gate:**
- [x] OpenSpec 校验通过。

---

## 2. 提升组合容量（competition-portfolio-capacity）

- [x] 2.1 `configs/competition.yaml`：`accounts.*.top_n` 改为 `50`、`trading.max_single_weight` 改为 `0.05`。
- [x] 2.2 `configs/agents/claude.yaml` `filters.max_fetch_candidates` 250；`configs/agents/codex.yaml` 同。
- [x] 2.3 `configs/strategy_v1.yaml` 不动，保留 `top_n: 10` 给单 agent 入口。
- [x] 2.4 既有 61 个测试不依赖具体 `top_n` 值，确认全绿。
- [x] 2.5 `competition.load("claude")["accounts"]` 各账户 `top_n` 现为 50；`trading.max_single_weight=0.05`；`filters.max_fetch_candidates=250`。

**Quality Gate:**
- [x] `python3 -m unittest discover -s tests` 全绿。
- [x] 在本机验证：`from stock_analyze.competition import load; load('claude')['accounts'][0]['top_n'] == 50`。

---

## 3. 策略演进可视化（agent-strategy-evolution-view）

- [x] 3.1 `reporting.read_agent_proposals(data_dir)`：读 `data/<agent>/proposals/*-strategy.json`，按月份倒序，容错 JSON 解析失败。
- [x] 3.2 `reporting.render_strategy_evolution_panel(data_dir, leaderboard_path=None)`：7 列表格（月份/状态/rationale 摘要/改了哪些键/风险/当月收益/次月收益）+ proposal-no-change CSS 区分。
- [x] 3.3 `reporting.render_latest_briefing_panel(data_dir)`：折叠展示 briefings/ 中最新 weekly + 最新 monthly 各一份。
- [x] 3.4 `generate_dashboard` 在"近期 agent 笔记"之后新增 `<h2>策略演进时间线</h2>` 与 `<h2>本期分析任务包</h2>`，page 与 fragment 同步生效。
- [x] 3.5 `dashboard_aggregator._render_observation_pairing(agents, data_dirs)`：side-by-side 拉两侧最新 `*-weekly-review.md`。
- [x] 3.6 对比 tab 末尾追加 `<h2>本周双方观察对照</h2>` + `observation-grid` 容器。
- [x] 3.7 所有新面板对 HTML 内容 escape；超长（≥16KB）截断 + `…(truncated)` 标记。
- [x] 3.8 缺数据时占位文案；不抛异常。

**Quality Gate:**
- [x] `tests/test_reporting_panels.py` 7 个用例（proposals 空态/降序/leaderboard 配对/HTML escape/briefing 空态/weekly+monthly/截断/malformed JSON 跳过）。
- [x] `tests/test_dashboard_aggregator.py` 扩展 3 个用例覆盖观察对照（双侧 / 单侧 / 空态）。
- [x] 既有 `reports/<agent>/dashboard.html` 含 "策略演进时间线" 与 "本期分析任务包" 标记（实测 grep -c = 2，page + fragment 同步）。
- [x] `reports/competition/dashboard.html` 含 "本周双方观察对照" 标记。

---

## 4. 系统总览文档

- [x] 4.1 `docs/system-overview.md` 写就，18 节，约 4000 字中文。
- [x] 4.2 README 顶部增加 "新人入门：先读 docs/system-overview.md"。
- [x] 4.3 `docs/competition-runbook.md` 顶部加 "更概括的视角见 docs/system-overview.md" 一行。

**Quality Gate:**
- [x] 总览文档单页能从 "项目目标" 一路读到 "后续路线图"。

---

## 5. 验证与发布

- [x] 5.1 `python3 -m py_compile stock_analyze/*.py tests/*.py` 通过。
- [x] 5.2 `python3 -m unittest discover -s tests`：72/72 通过（61 既有 + 11 新增）。
- [x] 5.3 `openspec validate expand-portfolio-capacity-and-strategy-visibility --strict` 通过。
- [x] 5.4 烟囱：`competition-init` + `--agent claude dashboard` + `competition-dashboard` 全部跑通；新 panel 在 dashboard.html 与 dashboard_fragment.html 中可 grep。
- [ ] 5.5 `git commit` + `git push origin HEAD:main` 直推 main。

**Quality Gate:**
- [x] 三件套通过。
- [x] 烟囱产物可访问，三个新 panel 出现。
- [ ] 推送成功，origin/main 推进到新 commit。（commit + push 步骤本任务执行）

---

## Completion Checklist

- [x] 所有 Phase 完成并通过 Quality Gate（5.5 在 push 步骤完成）。
- [x] `docs/system-overview.md` 落地，README 顶部指向。
- [x] 既有单 agent / 既有竞赛回归路径不受影响。
