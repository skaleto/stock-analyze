# Design · enable-llm-direct-strategy-evolution

## 1. 决策权回到 LLM

| 决策类型 | 旧 referee | 新 guard |
|---|---|---|
| YAML 是否能 parse | ❌ 不检查 | ✅ 检查 |
| 是否含 7 个顶层键之外的字段 | ❌ 不检查 | ✅ 检查,raise OverlaySchemaError |
| 是否侵入 baseline 锁字段 | ✅ 检查 + 拒绝 | ✅ 检查 + raise OverlayBaselineLocked |
| factor 名称是否在白名单 | ❌ 不检查 | ✅ 检查 |
| factor weight 是否 ∈ [0, 1] | ❌ 不检查 | ✅ 检查 |
| **改动是否"合理"(收益预期 / 风格连续性 / 过拟合)** | ⚠️ 用启发式判 needs_human(本次实测过严) | ❌ **不判,全权 LLM** |
| 单次改动幅度 cap | ❌ 不限 | ❌ **不限**(用户选项 d) |
| 月度回撤熔断 | ❌ 不限 | ❌ **不限** |

→ guard 是"语法检查器",不是"投资委员会"。

## 2. 透明度规则的代码实施

```python
# stock_analyze/transparency.py(新文件)

OPPONENT_READABLE = {  # 当前 agent 可以读对手的这些路径
    "configs/agents/{other}.yaml",
    "data/competition/monthly_reviews/*.json",
    "reports/competition/monthly_review_*.md",
    "data/{other}/config_evolution.csv",
}

OPPONENT_FORBIDDEN = {  # 当前 agent 不能读对手的这些
    "data/{other}/evolution_log/*",
    "data/{other}/notes/*",
    "data/{other}/state.json",
    "data/{other}/positions.csv",
    "data/{other}/trades.csv",
    "data/{other}/daily_nav.csv",
    "data/{other}/factor_runs/*",
    "data/{other}/proposals/*",
}

def validate_read_path(agent_id: str, path: str) -> None:
    """Raise TransparencyViolation if agent_id reads forbidden opponent path."""
    ...
```

这是 **文档级别约束**,不在运行时强制(因为 LLM 通过 Read 工具读 FS,Python 无法拦)。但 `AGENTS.md` / `CLAUDE.md` §7 明文写禁止,且 weekly / monthly briefing 主动**摘录**对手 yaml 内容到 briefing 里,让 LLM 不必越界读也能看到。

## 3. evolution_log markdown 结构

```markdown
# {agent} 策略演化 · {YYYY-MM}

## 月度复盘(数据驱动)

- 本月累计:+X.XX%,跑赢沪深300:+Y.YY%
- 主要贡献因子:roe(+Z.ZZ pp),momentum_60(+...)
- 主要拖累因子:pe(-...)
- 与对手差异:codex 本月跑赢/输 ...%,持仓重叠 Jaccard ...

## 与对手差异化分析(读 codex.yaml + monthly_reviews)

- 对手 yaml diff: ...
- 共同因子(都重视的): roe / gross_margin / debt_ratio / momentum_60
- 我独有: pe / pb / momentum_20
- 对手独有: low_volatility_60 / dividend_yield

## 改动列表

| 字段 | 旧值 | 新值 | 理由 |
|---|---|---|---|
| factors.pe.weight | 0.17 | 0.20 | 本月低 PE 风格领涨,加权 |
| factors.momentum_60.weight | 0.11 | 0.08 | 60 日动量在 5/15-5/22 IC = -0.03,衰减 |
| ... | ... | ... | ... |

## 预期效果

- 单月超额从 +1.32% 提升到 +X.XX% (信心:中)
- 行业暴露:计算机降 N%,银行升 M%
- 风险:本月样本只 5 天,可能过拟合

## 不在范围

- 不改 baseline 锁字段
- 不改 factor_processing
- 不改 portfolio_controls
```

## 4. evolution_diff JSON 结构

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
    "schema_valid", "no_baseline_lock_violation", "factors_in_whitelist", "weights_in_range"
  ]
}
```

## 5. config_evolution.csv schema 升级

```
month, from_hash, to_hash, applied_at, diff_summary, reasoning_file, diff_file, agent_id
2026-05, 128cadd70473, 9f4be23b2c1a, 2026-06-01T10:23:00, "pe +0.03; mom_60 -0.03; ind_cap -0.05", data/claude/evolution_log/2026-06.md, data/claude/evolution_diff/2026-06.json, claude
```

## 6. CLI 子命令

```bash
# 新
python3 -m stock_analyze validate-overlay --agent claude
  # 纯 guard 检查;exit 0 = ok, exit 1 = schema error, exit 2 = lock violation

# 保留
python3 -m stock_analyze agent-rollback --agent claude --to <config_hash>
  # 紧急回滚,通过 _history/ 备份恢复

# 删
python3 -m stock_analyze agent-judge-proposals       # 不再有
python3 -m stock_analyze agent-apply-approved-proposals  # 不再有
```

## 7. 月度流程时序图

```
每月 1 号 09:00 ECS systemd
  competition-monthly-review --month <prev>
    └─ 同当前实现:写 monthly_reviews/<month>.json + .md + leaderboard.csv
    └─ build_monthly_briefing for each agent(briefing 内含对手 overlay 摘要)

每月 1 号 (any time) human operator on local
  $ ./scripts/sync-from-ecs.sh
  $ /monthly-strategy claude
    LLM 自主:
      read briefing → think → write configs/agents/claude.yaml + evolution_log + evolution_diff
      git add → guard check → git commit
  $ ./scripts/sync-to-ecs.sh
    远端 ExecStartPost:无操作(无 referee 无 apply)

每月 1 号 之后 第一个交易日 17:25 ECS
  prepare-market-data → run-daily --offline (新 overlay 已生效)
```

## 8. 不在范围

- 不改 daily / weekly 流程
- 不改 factor pipeline / portfolio controls / simulator / performance
- 不引入 LLM API 远程调用(LLM 仍是 Claude Code / Codex CLI 本机会话)
- 不改 codex 一侧的"用户操作 codex"模式(用户自己驱动 codex 改 codex.yaml)
