# LLM 直接策略演化流程

由 OpenSpec change `enable-llm-direct-strategy-evolution` 实施。
Human operator 在 2026-05-23 明确授权:
"所有优化的内容全部由 LLM 全权代理执行,不需要审核,只要让我看到修改了什么。"

本文描述 **LLM 直接改 `configs/agents/<agent>.yaml`** 的边界、产物、与安全网。

## 1. 背景:为什么不再走 referee?

旧三步流程(`enable-monthly-config-evolution` archive):

```
LLM 写 proposal JSON
  → 确定性 referee 判 approved/rejected/needs_human
  → agent-apply-approved-proposals 应用 approved 的 patch
```

实测中(2026-05-19)对合理的 `no_change=true` 提案也判 `needs_human`,阻碍了
竞赛节奏。本次 change 把 referee 退化为 **锁字段守卫**(`overlay_guard.py`),
不再评判策略好坏。

## 2. 新流程边界

| 谁做 | 做什么 |
|---|---|
| LLM(claude / codex) | 读 briefing、读对手 overlay、直接改自己 yaml、写 evolution_log + evolution_diff、追加 config_evolution.csv |
| `overlay_guard.validate` | 只校验:7 个顶层键白名单 / baseline 锁字段不被侵入 / factor 名在 AVAILABLE_FACTORS / weight ∈ `[0, 1]` |
| `evolution_writer.write_evolution` | 原子化执行:guard → backup → 写 yaml → 写 log + diff → 追加 csv |
| 人类操作员 | `sync-from-ecs.sh` → 触发 slash command → 看 git diff → `sync-to-ecs.sh` |

## 3. 产物清单

每月一次 `/monthly-strategy <agent>` 跑完后,以下 6 个产物全部落地:

| 路径 | 谁写 | 内容 |
|---|---|---|
| `data/<agent>/notes/<YYYY-MM>-monthly-review.md` | LLM | 月度高层笔记(≤1500 字) |
| `data/<agent>/evolution_log/<YYYY-MM>.md` | LLM | 演化思考记录(≤2000 字,6 段结构) |
| `data/<agent>/evolution_diff/<YYYY-MM>.json` | evolution_writer | 机器可读 diff(键 → from/to) |
| `data/<agent>/config_evolution.csv`(新一行) | evolution_writer | 审计行(month / from_hash / to_hash / diff_summary / reasoning_file / diff_file) |
| `configs/agents/<agent>.yaml` | LLM | 新 overlay(JSON 语法) |
| `configs/agents/_history/<old_hash>.yaml` | evolution_writer | 上版 overlay 备份(每次演化前) |

## 4. evolution_log markdown 结构(6 段)

```markdown
# {agent} 策略演化 · {YYYY-MM}

## 月度复盘(数据驱动)

- 本月累计:+X.XX%,跑赢沪深300:+Y.YY%
- 主要贡献因子:roe(+Z.ZZ pp)、momentum_60(+...)
- 主要拖累因子:pe(-...)
- 与对手差异:对方本月跑赢/输 ...%,持仓重叠 Jaccard ...

## 与对手差异化分析(读 codex.yaml + monthly_reviews)

- 对手 yaml diff: ...
- 共同因子:roe / gross_margin / debt_ratio / momentum_60
- 我独有:pe / pb / momentum_20
- 对手独有:low_volatility_60 / dividend_yield

## 改动列表

| 字段 | 旧值 | 新值 | 理由 |
|---|---|---|---|
| factors.pe.weight | 0.17 | 0.20 | 本月低 PE 风格领涨,加权 |
| factors.momentum_60.weight | 0.11 | 0.08 | 60 日动量 IC = -0.03,衰减 |

## 改动理由(展开)

(自由叙述,把"改动列表"的逻辑链补全)

## 预期效果

- 单月超额从 +1.32% 提升到 +X.XX%(信心:中)
- 行业暴露:计算机降 N%,银行升 M%
- 风险:本月样本只 5 天,可能过拟合

## 不在范围

- 不改 baseline 锁字段
- 不改 factor_processing
- 不改 portfolio_controls
```

## 5. evolution_diff JSON 结构

```json
{
  "agent_id": "claude",
  "month": "2026-06",
  "evolved_at": "2026-06-01T10:23:00",
  "from_config_hash": "128cadd70473",
  "to_config_hash": "9f4be23b2c1a",
  "diff": {
    "factors.pe.weight": {"from": 0.17, "to": 0.20},
    "factors.momentum_60.weight": {"from": 0.11, "to": 0.08},
    "portfolio_controls.max_industry_weight": {"from": 0.30, "to": 0.25}
  },
  "reasoning_file": "data/claude/evolution_log/2026-06.md",
  "guard_checks_passed": [
    "schema_valid",
    "no_baseline_lock_violation",
    "factors_in_whitelist",
    "weights_in_range"
  ]
}
```

## 6. config_evolution.csv schema

```
event, event_at, agent_id, month, from_hash, to_hash,
diff_summary, reasoning_file, diff_file, reviewer
```

旧 schema 的列(`source_proposal` / `decision_path` / `patch_paths`)在第一次新
演化运行时会被 `evolution_writer` 原地迁移成新 schema,旧行的新增列留空。

## 7. 对手透明度

由 `CLAUDE.md §7` / `AGENTS.md §7` 文档约束(运行时不强制——LLM 通过 Read
工具读 FS, Python 无法拦)。规则:

**可以读**对手:

- `configs/agents/<other>.yaml`(对手当前 overlay)
- `data/<other>/config_evolution.csv`(对手历史改动摘要)
- `data/competition/monthly_reviews/*.json`
- `reports/competition/monthly_review_*.md`

**不可以读**对手:

- `data/<other>/evolution_log/*.md`(对手的思考过程)
- `data/<other>/notes/*.md`(对手的周笔记)
- `data/<other>/state.json` / `positions.csv` / `daily_nav.csv` / `trades.csv`(实时持仓)
- `data/<other>/factor_runs/*`
- `data/<other>/proposals/*`(旧 proposal 文件)
- `reports/<other>/*`

→ "你能看到对手的阵型(yaml),看不到对手的思考(evolution_log)。"

briefing(`agent_briefing.py`)会主动把对手 overlay 摘要 + config_evolution.csv
最近 3 行写进 agent 的 monthly 待办,避免 LLM 越界读取。

## 8. 安全网与回滚

只有两条硬约束:

1. **schema 合法**——顶层键 ⊂ 7 项白名单;factor 名 ⊂ AVAILABLE_FACTORS;
   factor weight ∈ `[0, 1]`。
2. **baseline 锁字段不被侵入**——`initial_cash` / `accounts.*` / `schedule.*` /
   `trading.*` 全部禁动。

策略好坏 LLM 自负。LLM 可以把 `factors.pe.weight` 改成 `0.95`,守卫不阻拦。

应急回滚:

```bash
python3 -m stock_analyze agent-rollback --agent claude --to <old_config_hash>
```

回滚后会:

1. 从 `configs/agents/_history/<old_config_hash>.yaml` 恢复内容到 live overlay。
2. 在 `data/claude/config_evolution.csv` 追加 `event=rollback` 行(reviewer=`operator`)。
3. `_history/` 不变,旧 hash 仍可再次回滚。

## 9. 月度时序图

```
每月 1 号 09:00 ECS systemd
  competition-monthly-review --month <prev>
    └─ monthly_reviews/<month>.json + .md + leaderboard.csv
    └─ build_monthly_briefing for each agent(含对手 overlay 摘要)
  competition-dashboard
    └─ reports/competition/dashboard.html 刷新

每月 1 号(任意时刻) human operator on local
  ./scripts/sync-from-ecs.sh
  /monthly-strategy claude     # slash command 内 LLM 自主完成:
    · read briefing
    · think
    · write configs/agents/claude.yaml
    · call evolution_writer.write_evolution(...)
    · python3 -m stock_analyze validate-overlay --agent claude → exit 0
  /monthly-strategy codex     # 同理(或在 Codex CLI 内 do monthly strategy)
  ./scripts/sync-to-ecs.sh
    └─ rsync configs/agents/*.yaml、_history/、data/<agent>/evolution_{log,diff}/、
       data/<agent>/config_evolution.csv 回 ECS
    └─ 远端跑 competition-dashboard,无 referee/apply 步骤

每月 1 号之后第一个交易日 17:25 ECS
  prepare-market-data → run-daily --offline(新 overlay 已生效)
```

## 10. dashboard 展示

`reporting.render_strategy_evolution_panel` 读 `evolution_diff/*.json` +
`evolution_log/*.md` + `config_evolution.csv`,渲染为:

| 月份 | 状态 | from → to hash | diff 摘要 | 思考摘要 | evolution_log | 当月收益 | 次月收益 |

`tighten-audit-findings` F5 引入的 proposal-drift 红高亮被保留——现在的口径是:
若 `config_evolution.csv` 最新行的 `to_hash` 与 live `configs/agents/<agent>.yaml`
重新 hash 后的 actual 值不一致,该行会染红显示 "overlay 已变"。

## 11. 删除的命令

| 命令 | 状态 |
|---|---|
| `agent-judge-proposals` | ❌ 删除 |
| `agent-apply-approved-proposals` | ❌ 删除 |
| `agent-rollback` | ✅ 保留(读 `_history/<hash>.yaml` 恢复) |
| `validate-overlay` | 🆕 新增,纯 guard 检查 |
