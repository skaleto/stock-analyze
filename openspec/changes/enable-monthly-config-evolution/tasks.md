## 1. Stub Foundation

- [x] 1.1 `openspec new change enable-monthly-config-evolution`。
- [x] 1.2 写一份 stub `proposal.md` 说明范围与触发条件，标 `Status: STUB`。
- [x] 1.3 启动该 change 时再补 `design.md`、`tasks.md` 细节与 capability specs。

## 2. 设计与实施（等启动）

- [x] 2.1 决定 `data/competition/decisions/<month>-<agent>.json` schema，并支持 `approved/rejected/needs_human`。
- [x] 2.2 `stock_analyze/proposal_judge.py`：确定性裁判与 patch guardrails。
- [x] 2.3 `stock_analyze/proposal_apply.py`：apply 命令与 patch lock 校验。
- [x] 2.4 `stock_analyze/agent_rollback.py`：rollback 命令。
- [x] 2.5 Dashboard 策略演进表展示裁判状态。
- [x] 2.6 systemd 月度链路与 `sync-to-ecs.sh` 回传链路加入 judge/apply。
- [x] 2.7 单元测试、AGENTS.md / runbook 更新。

## 3. 启动条件

- [x] 3.1 启动条件由用户确认：需要裁判 agent 降低人工判断难度。
- [x] 3.2 使用单元测试 fixture 覆盖 approved / rejected / needs_human / apply / rollback。
