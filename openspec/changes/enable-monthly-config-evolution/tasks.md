## 1. Stub Foundation

- [x] 1.1 `openspec new change enable-monthly-config-evolution`。
- [x] 1.2 写一份 stub `proposal.md` 说明范围与触发条件，标 `Status: STUB`。
- [ ] 1.3 启动该 change 时再补 `design.md`、`tasks.md` 细节与 capability specs。

## 2. 设计与实施（等启动）

- [ ] 2.1 决定 `data/competition/decisions/<month>-<agent>.json` schema。
- [ ] 2.2 `stock_analyze/proposal_apply.py`：apply 命令与 patch lock 校验。
- [ ] 2.3 `stock_analyze/agent_rollback.py`：rollback 命令。
- [ ] 2.4 `stock_analyze/decision_server.py`：小型 HTTP endpoint 接 dashboard 按钮（也可纯手工置文件）。
- [ ] 2.5 Dashboard `Approve / Reject / Edit` 按钮 + 状态列。
- [ ] 2.6 单元测试、CLAUDE.md / AGENTS.md / runbook 更新。

## 3. 启动条件

- [ ] 3.1 双 agent 在新 `top_n=50` 下跑过至少 2 个完整月度周期，确认手工
      合入的痛点和频率确实存在。
- [ ] 3.2 至少积累 3 份不同月份的 proposal JSON 作为测试样本。
