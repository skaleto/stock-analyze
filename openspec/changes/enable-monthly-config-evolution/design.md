## Design

本 change 把月度策略提案从"人手复制 patch"升级为"裁判审查 + 自动应用"。
裁判不是收益预测模型，而是一组确定性护栏：它只回答 proposal 是否小步、
合规、可解释、风险可控。这样用户不需要判断策略好坏，只需要查看裁判结论。

核心流程：

1. Claude / Codex 在本地生成 `data/<agent>/proposals/<month>-strategy.json`。
2. ECS 或本地运行 `agent-judge-proposals --month <month>`。
3. 裁判读取 proposal、当前 overlay、月度 review、baseline locked fields。
4. 裁判写 `data/competition/decisions/<month>-<agent>.json`。
5. `agent-apply-approved-proposals --month <month>` 只应用 `decision=approved` 的 patch。
6. 应用前把当前 overlay 备份到 `configs/agents/_history/<config_hash>.yaml`。
7. 应用或回滚都写 `data/<agent>/config_evolution.csv`。

裁判结果分三类：

- `approved`：可自动应用。必须无锁字段、patch 小、因子权重不过度集中、
  有 rationale / risks / expected_effect，且月度 review 已存在。
- `needs_human`：不危险但不适合自动应用，例如权重变化过大、缺少月度 review、
  config hash 不匹配、理由不足。
- `rejected`：违反硬规则，例如改 baseline locked 字段、未知因子、patch schema 错误、
  合并后 overlay 无法通过 competition loader。

systemd 月度链路在 `competition-monthly-review` 后追加：

```bash
agent-judge-proposals
agent-apply-approved-proposals
competition-dashboard
```

因此云端不需要运行大模型，也能自动完成第一层策略变更确认。
