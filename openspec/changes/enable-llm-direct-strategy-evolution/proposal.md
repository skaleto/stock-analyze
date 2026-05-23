## Why

当前月度策略演化是三步流程,由 OpenSpec change `enable-monthly-config-evolution` + `tighten-audit-findings` 落地:

```
1. LLM(claude / codex) 写 proposal JSON  → data/<agent>/proposals/<month>-strategy.json
2. 确定性 referee 判 approved/rejected/needs_human → data/competition/decisions/<month>-<agent>.json
3. agent-apply-approved-proposals 只应用 approved 的 patch → 写 config_evolution.csv
```

2026-05-23 与 human operator 重新明确了**竞赛目的**:

> "比较两个 LLM 各自的策略,在长期来看哪一个收益更高。所有优化的内容全部由 LLM 全权代理,授权所有数据访问,可以自己定义策略并自己修改,不需要审核 — 只要让用户看到修改了什么。"

→ **competition 的本质是"两个 LLM 长期决策能力对比"**,referee 当前实现已经变成多余的"看护人",而且实测中(2026-05-19 那次)对 `no_change=true` 的合理提案也判 `needs_human`,**阻碍了竞赛节奏**。

按当前协议:
- 我必须先写 JSON proposal(结构化但表达力有限)
- 再等下一次 cron tick 触发 referee
- 再再等下一次 cron tick 触发 apply
- 三轮才能让我的策略生效

按本 change 协议:
- 我直接读 monthly briefing → 直接改 `configs/agents/claude.yaml` → 写一份 markdown evolution log + JSON diff → guard 检查锁字段 → 落 git commit
- 一轮搞定,且对比维度完全保留(diff + reasoning 全留痕)

## What Changes

### 1. Referee 退化为"锁字段守卫"(guard)

`stock_analyze/proposal_judge.py` 重命名为 `overlay_guard.py`,职责窄化到:

| 检查项 | 行为 |
|---|---|
| YAML schema 合法(7 个顶层键之内:agent_id / strategy_id / name / factors / factor_processing / portfolio_controls / filters) | 不符合 → raise `OverlaySchemaError` |
| baseline 锁字段无侵入(initial_cash / accounts.* / schedule.* / trading.*) | 触碰 → raise `OverlayBaselineLocked` |
| factor 名称在 `data_provider.AVAILABLE_FACTORS` 集合内 | 不在 → raise `OverlayUnknownFactor` |
| factor weight ∈ [0, 1] | 越界 → raise `OverlayInvalidWeight` |
| **strategy quality**(回报、IR、风格漂移、过拟合) | **不判断**。这是 LLM 自己的责任 |

→ **referee 不再有 "approved / rejected / needs_human" 三态**,只有 "valid / raises exception"。

### 2. LLM 直接改 yaml(单步,不再走 proposal)

LLM(claude / codex)在月度评估完后:

```python
# 月度 slash command 触发 LLM 行为(伪代码)
read('data/<agent>/notes/briefings/<month>-monthly.md')
analysis = think_about_market_and_competition()
new_overlay = compute_new_yaml(current_overlay, analysis)  # 直接生成

# Write yaml + 守卫
overlay_guard.validate(new_overlay)  # raise on schema/lock violation
yaml_path = 'configs/agents/<agent>.yaml'
write(yaml_path, new_overlay)

# Audit trail
diff = compute_diff(old_overlay, new_overlay)
log_path = f'data/<agent>/evolution_log/<month>.md'
write(log_path, format_log(diff, analysis))

# config_evolution.csv 一行
append_csv('data/<agent>/config_evolution.csv', {
    'month': month, 'from_hash': old_hash, 'to_hash': new_hash,
    'diff_summary': summarize(diff), 'reasoning_file': log_path,
})

# Commit
git_add([yaml_path, log_path, 'data/<agent>/config_evolution.csv'])
git_commit(f"[{agent}] {month} strategy evolution: {summary}")
```

### 3. 对手信息透明度规则

**LLM 在做月度优化时,可以读取以下对手信息:**
- ✅ `configs/agents/<other-agent>.yaml`(对手当前 overlay,**新增**)
- ✅ `data/competition/monthly_reviews/<month>.json`(月度对比,已有)
- ✅ `reports/competition/monthly_review_<month>.md`(已有)
- ✅ `data/<other-agent>/config_evolution.csv`(对手历史改动摘要,**新增**)

**LLM 不能读取:**
- ❌ `data/<other-agent>/evolution_log/*.md`(对手的优化思考,**新增禁止**)
- ❌ `data/<other-agent>/notes/*.md`(对手的周笔记)
- ❌ `data/<other-agent>/state.json`、`positions.csv` 等(对手的实时持仓)

→ 等于"我能看到对手的阵型,但看不到对手的思考过程"。

### 4. 删除已废弃流程

| 文件 / 命令 | 操作 |
|---|---|
| `agent-judge-proposals` CLI 子命令 | 删 |
| `agent-apply-approved-proposals` CLI 子命令 | 删 |
| `agent-rollback` CLI 子命令 | 保留(用于人类操作员紧急回滚) |
| `stock_analyze/proposal_judge.py` | 重写为 `overlay_guard.py`,只剩 validate() |
| `stock_analyze/proposal_apply.py` | 删 |
| `data/<agent>/proposals/` 目录 | 不再写新文件,旧的留作历史 |
| `data/competition/decisions/` 目录 | 不再写新文件,旧的留作历史 |
| `_history/<config_hash>.yaml` 备份机制 | 保留(每次直接改 yaml 前自动备份当前 hash) |

### 5. 新增产出

| 路径 | 谁写 | 内容 |
|---|---|---|
| `data/<agent>/evolution_log/<YYYY-MM>.md` | LLM | 月度优化思路(≤2000 字,markdown,中文),含: 月度复盘 / 与对手差异 / 改动列表 / 改动理由 / 预期效果 / 风险 |
| `data/<agent>/evolution_diff/<YYYY-MM>.json` | LLM | 改动前后 yaml 的结构化 diff(机器可读) |
| `data/<agent>/config_evolution.csv` 新增列 | LLM | 加 `reasoning_file`、`diff_file` 字段 |
| `configs/agents/_history/<config_hash>.yaml` | `overlay_guard` | 改 yaml 前自动备份(原已有) |

### 6. Slash command 更新

`.claude/commands/monthly-strategy.md` 重写,提示 LLM:
- 读 briefing(原已有)
- 读对手 yaml(新)
- 直接改 yaml(新)
- 写 evolution_log markdown(新)
- 写 evolution_diff JSON(新)
- 跑 `python3 -m stock_analyze validate-overlay --agent claude`(新,纯 guard 检查)
- 自动 commit + 提示人类 push(新)

### 7. dashboard 集成

- 新增 "策略演进时间线" 面板已有(`reporting.render_strategy_evolution_panel`)
- 改造为读取新的 `evolution_log` markdown + `evolution_diff` JSON,而非旧 `proposals/` + `decisions/`
- 显示列:月份 / from_hash → to_hash / diff 摘要 / 阅读 evolution_log 链接 / 当月与次月实际收益

## 验收

- `python3 -m stock_analyze` CLI 不再列出 `agent-judge-proposals` 与 `agent-apply-approved-proposals`
- `python3 -m stock_analyze validate-overlay --agent claude` 对合法 overlay 退出码 0,对侵入 baseline 锁字段的 overlay 退出码 ≠ 0 + 明确错误
- LLM 直接改 `configs/agents/claude.yaml` 后,`data/claude/evolution_log/<YYYY-MM>.md` 与 `data/claude/evolution_diff/<YYYY-MM>.json` 同时落
- `data/claude/config_evolution.csv` 新增一行,引用 reasoning_file 与 diff_file
- dashboard "策略演进时间线" 面板能渲染新内容
- 同样的 6 条对 codex 也成立

## 与已有 change 的关系

- `enable-monthly-config-evolution`(已落地,在 archive): 它定义了 referee + apply 三步;本 change **取代** 它,但保留 `_history/<hash>.yaml` 备份 + `config_evolution.csv` 审计的精神。
- `tighten-audit-findings`(已落地):F1 引入的 `validate_overlay()` 函数在本 change 中升级为 `overlay_guard.validate()`,扩充检查项;F5 / F10 的 dashboard 改造继续保留,读新文件源。
- `migrate-data-source-to-tushare-pro`(draft,本会话同时提案): 数据底座层面的迁移,**与本 change 正交**。先后顺序:数据迁移先稳,再改演化协议。

## Agent 来源声明

本 change 由 claude agent 在 2026-05-23 撰写,基于 human operator 显式授权 "所有优化的内容全部由 LLM 全权代理执行"。改动覆盖 `stock_analyze/*.py`(改 / 删 / 重命名)、`AGENTS.md` / `CLAUDE.md`(补充 §6 流程)、`.claude/commands/monthly-strategy.md`(重写)、`docs/competition-runbook.md`(更新操作流程),均在 CLAUDE.md §7 禁地列表;由 human operator session 中 explicit 邀请。当前 status = **DRAFT,await confirmation**。
