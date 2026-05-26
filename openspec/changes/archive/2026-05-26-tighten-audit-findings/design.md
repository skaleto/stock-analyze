## Design

### F1 — Pure-memory overlay validation

`proposal_judge._validate_merged_overlay` 原本走 "write proposed overlay to disk → competition.load → restore"，原因是当时 `competition.load` 是唯一已有的全验证入口。这条路径有两个问题：

1. 任何在两次 write 之间读 `configs/agents/<agent>.yaml` 的并发进程
   （例如手动触发的 `run-weekly --agent codex`）会看到提议中的 overlay。
2. `try/finally` 如果在 write 临界点崩溃（磁盘满、信号中断），会留下脏文件。

修复方法：把 competition.py 既有的私有校验拼装成一个公共 `validate_overlay`：

```python
def validate_overlay(agent_id: str, overlay: dict, repo_root: ...) -> dict:
    root = Path(repo_root) if repo_root else Path.cwd()
    baseline = load_baseline(root)
    _validate_overlay_top_level(overlay, agent_id)
    _validate_locked_paths(baseline, overlay)
    merged = _deep_merge(baseline, overlay)
    merged.setdefault("agent_id", agent_id)
    merged.setdefault("strategy_id", overlay.get("strategy_id", agent_id))
    migrate_strategy_config(merged)
    return merged
```

`proposal_judge._validate_merged_overlay` 退化成 1 行：
`competition.validate_overlay(agent_id, overlay, root)`。

防回归测试比对调用前后磁盘 mtime。

### F2 — `.gitignore` 精确化

`.claude/` 是过于宽的忽略规则。Claude Code 的实际 worktree 缓存在 `.claude/worktrees/`，
但 `.claude/commands/`、`.claude/agents/`、`.claude/skills/`、`.claude/settings.json`
都属于checked-in artifacts。改成 `.claude/worktrees/`。

实测：`.claude/commands/{weekly-review,monthly-strategy}.md` 已经被 `git ls-files` 列出来
（因为先 add 再 ignore 才保留），但任何 NEW 文件会被 .gitignore 捕获。
改后正常 add 即可。

### F3 — `configs/agents/_history/` 保留 git 跟踪

权衡过两个方案：

- **gitignore**：rollback 只能在 ECS 上做，开发机克隆下来用不了 `agent-rollback`。
- **保留 git 跟踪**（选定）：rollback 可在任何 clone 上做；git log 充当审计；
  每月新增 2 个 ~1KB JSON 文件，年增长 ~24KB，可忽略。

文档里加段说明，避免新人误以为 `_history/` 是垃圾要清理。

### F5 — 预期效果列

策略演进表当前 7 列：月份 / 提案状态 / 裁判结论 / 理由摘要 / 改了哪些键 / 风险 /
当月收益 / 次月收益。在"理由摘要"右侧插一列 "预期效果"，把已经读取但未使用的
`expected = _escape_html(proposal.get("expected_effect"))` 渲染上去。

宽度调优：`max-width: 220px; white-space: normal`。

### F9 — ExecStartPost 容错

systemd `ExecStartPost=cmd` 严格按顺序执行，第一个失败后续都不跑。把
`agent-judge-proposals` 与 `agent-apply-approved-proposals` 前缀 `-`，让它们的
非零退出不阻塞 `competition-dashboard`：

```ini
ExecStart=...competition-monthly-review
ExecStartPost=-...agent-judge-proposals
ExecStartPost=-...agent-apply-approved-proposals
ExecStartPost=...competition-dashboard
```

`competition-dashboard` 保持严格，因为如果它失败就是产物缺失，应当被 timer
status 反映。

裁判 / apply 的失败仍会被 `data/<agent>/runs.csv` 记录（CLI 入口已有 RunLedger 包裹），
不会丢痕迹。

### F10 — Proposal hash drift

decision JSON 已经记录 `proposal_hash`（由 `proposal_judge._hash_mapping`
对 proposal 文件取 sha256[:12]）。dashboard 渲染时：

1. 重新读 `data/<agent>/proposals/<month>-strategy.json`。
2. 用同样的方式计算当前 hash。
3. 与 decision 中记录的 hash 比对。
4. 不一致时：在"裁判结论"单元格末尾追加 ` · 提案已变`，CSS class `proposal-drift`
   红色显示。

这能告诉用户："agent 在裁判完之后又改了提案，但 ECS 仍然按旧 patch 应用了"。

### F6/F7 — 卫生

只删未用 import 与未用 local 变量，不改行为。pyflakes 一次输出 ≈ 20 行，
逐个删除即可。

## Goals / Non-Goals

**Goals**

- 消除已知文件竞态。
- 让 dashboard 不再静默丢字段。
- ECS 上 monthly review 链路对子命令失败更容错。
- git 卫生：精确 ignore、明确 _history 政策。

**Non-Goals**

- 不调整调度时间、不改 baseline、不动 agent overlay 字段集合。
- 不引入新模块、新依赖、新文件类型。
- 不修 F4（历史快照文件扩展名）—— 与项目惯例一致，不属于 bug。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| `validate_overlay` 引入跨私有边界调用 | 私有函数将来重命名会影响 proposal_judge | 公共 `validate_overlay` 屏蔽私有依赖；proposal_judge 只调用公共 API |
| `.gitignore` 改动让旧已忽略文件突然出现在 status | 噪音 | 实测 `.claude/` 下当前仅有 `commands/`，已经 tracked；改动不影响已 tracked 行为 |
| systemd `-` 前缀让真问题被 dashboard 掩盖 | 误以为 healthy | runs.csv 仍记录失败，dashboard "最近运行" 面板会显式标红 |
| dashboard 加列让窄屏布局变挤 | UX 退化 | CSS `font-size:12px; max-width:220px; white-space:normal` 保持可读 |

## Migration Plan

1. 合本 change 后 ECS `git pull && sudo systemctl daemon-reload`。
2. 现有 `data/competition/decisions/*` / `configs/agents/_history/*` 不需要任何转换。
3. 下次 monthly review 自然采用新 systemd 行为。
4. 回滚：单 change 单次提交，`git revert <hash>` 即可。
