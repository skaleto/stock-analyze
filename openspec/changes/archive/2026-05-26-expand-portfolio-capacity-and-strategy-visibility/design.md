## Context

竞赛已经能跑，但用户反馈的两个体感问题是直接的：

1. **`top_n=10` 太集中**：等权 10% 单股权重让单只异常拖累整组合；同时 10 只候选缺乏统计意义，因子诊断（覆盖率/前向 IC）信号被噪音盖。
2. **优化链路在 dashboard 上断了一段**：用户能看到信号、成交、净值、笔记，但**看不到 "agent 提了什么 patch / 是否应用 / 下个月效果"** 的演进。Proposals 和 briefings 当前只有人工 cat 才看得到。

加上"一篇能读完就懂的中文文档"补足新人 onboarding，三件事一并做。

## Goals / Non-Goals

**Goals**

- 把 baseline `top_n` 升到 50，并同步调整与之搭配的 `max_single_weight` 和候选池大小。
- 既有 `state.json` 不重置；从下一次 `run-weekly` 自动按新规模运作。
- Dashboard 加三个新面板，让 "策略演进" 和 "下一阶段在看什么" 直接看见。
- 一份 3000-5000 字中文总览文档，作为唯一入口。

**Non-Goals**

- 不自动应用 proposal（保留给下一 change）。
- 不调整因子集合或基准。
- 不引入新前端框架；新面板继续走纯 HTML/CSS + 内联 JS。
- 不破坏单 agent 模式（`strategy_v1.yaml` 路径不动）。

## Decisions

### 1. `top_n=50`、`max_single_weight=0.05`、`max_fetch_candidates=250`

- 50 只单账户 → 等权目标 2%，比 10 只的 10% 风险贡献低 5×。
- `max_single_weight=0.05`（5%）= 等权 × 2.5 倍封顶；这是为因子驱动的超配留少量空间，又避免 "一只 ROE 极高的 spike 把整个账户拉爆"。
- 候选池 250 给 50 头部 5× 漏斗深度；既能容忍因子覆盖损失，又不会把 cold-cache 拉得太慢（180 → 250 大约 +40% history fetch 量，可接受）。
- 也评估了 `top_n=100`：等权 1%，对小账户来说每只交易额 ~5000 元会逼近最小整数倍 lot（100 股 × 50 元/股 = 5000，临界）；50 是更稳的中点。

### 2. 既有 state 不重置

`state.json` 只记录 cash 和现存 positions；不记录 `top_n`。`top_n` 每次跑时从 config 读。所以升级后第一次 `run-weekly` 会：

- 把候选池筛到 50 名内入选
- 对比当前持仓（最多 10 只）→ 生成 ~40 单买入 + 0 单卖出（除非有些当前持仓掉出 50 名）
- 单股目标值 = `total_value / 50`，远小于现有 10% 的目标

`build_target_orders` + portfolio_controls 已经处理"目标 vs 当前"diff，不需要新逻辑。

### 3. 策略演进面板的数据源与字段

- 数据源：`data/<agent>/proposals/*-strategy.json`（agent 自己写的 JSON）+ `data/competition/leaderboard.csv`（按月战绩）。
- 显示字段：
  - 月份（文件名 stem）
  - `proposed_at`
  - `no_change` 标记（true 时整行染灰）
  - `rationale` 截断 200 字
  - `expected_effect`
  - `risks`（最多 3 行）
  - `patch` 摘要：列出修改的键路径（不展开值，避免长度爆炸）
  - 实际结果：从 leaderboard 拉提案月的 `<agent>_return` 与 `<agent>_ir`，标注"当月" / "次月"
- 不做"已审核"标记，因为审批流（decisions/）属下一 change。

### 4. 最新任务包面板

- 拉 `data/<agent>/notes/briefings/` 下按 mtime 最新的 `.md`。
- 同时存在 `*-weekly.md` 和 `*-monthly.md` 时，分别选最新两份，月度（如果在 7 天内）排前。
- 折叠 `<details>` 默认收起；展开后显示完整 markdown 文本 `<pre>`。
- 缺失时占位 "ECS 还没生成 briefing；下次 `run-weekly` 跑完就有"。

### 5. 双方观察对照面板

- 拉 `data/{claude,codex}/notes/` 下按 mtime 最新的非-briefing `.md`。
- 渲染左右两栏 `<div class="grid">`；每栏 `<details>` 折叠 + `<pre>` 内容。
- 缺一方时另一方仍渲染；两方都缺则占位整段说明。

### 6. 系统总览文档骨架

```
1. 这是什么 + 不是什么（边界一句话）
2. 整体架构图（ECS + 本地 + GitHub）
3. 目录结构（树形 + 注释）
4. 数据流（每周 / 每日 / 每月）
5. 公平基线与 overlay
6. 因子流水线四步骤
7. 组合构建控制
8. 绩效与归因
9. 因子诊断
10. 运行账本与配置快照
11. Dashboard 三 tab 速读
12. Agent CLI 分析闭环
13. 关键产物清单
14. 一周一月一年的工作节奏
15. 安全边界
16. 限制与不在范围
17. 后续 change 路线图
18. 术语表
```

### 7. 测试策略

- `tests/test_reporting_panels.py`：覆盖 `render_strategy_evolution_panel` / `render_latest_briefing_panel` 在有数据 / 无数据时的行为。
- `tests/test_dashboard_aggregator.py` 扩展：增加 "本周双方观察对照" 在两份 notes 都存在 / 只存在一份 / 都缺失三种情形下的渲染断言。
- 不改 `competition.yaml` 的硬编码值测试（既有 `test_competition.py` 用的是 fixture）。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 升 top_n 后首次 run-weekly 大量买入 → 滑点/佣金集中 | 单周成本飙升 | 一次性事件；下周开始换手率回落到正常。月度对比会显示这一周的 cost_bps 异常，文档中说明 |
| 候选池 250 在冷缓存下 history fetch 慢 | 首次跑可能 20-30 分钟 | 缓存命中后恢复；systemd timer 留够时长 |
| 策略演进面板 + briefing 面板拖长 dashboard | 加载慢、滚动累 | 全部 `<details>` 默认收起；面板尾置不影响首屏 |
| 双方观察对照泄露对方笔记 → 信息边界破坏 | agent 通过 dashboard 间接偷看对方 | 这是给"人类"看的 dashboard，不是给 agent 的；agent 仍只能通过 monthly_reviews 看对手。dashboard 是用户视角 |
| top_n=50 让 max_single_weight 不再 binding | 单股集中度回归到等权 | 故意：等权 2% 已经够分散；max_single_weight 只是上限不是目标 |

## Migration Plan

1. 合入本 change。
2. ECS 拉新代码、`systemctl daemon-reload`。
3. ECS 跑一次手动 `python3 -m stock_analyze --agent claude run-weekly` + codex 同样：会基于新 `top_n` 生成约 40+ 单买入；observed cost 一次性偏高是正常。
4. 等下次月度 timer 触发或手动跑 `competition-monthly-review` → `competition-dashboard`，新面板可见。
5. 旧 `data/<agent>/state.json` 与历史 `daily_nav.csv` 不动；首次大调仓后 NAV 会出现一个"换手期"特征，月度报告会自动归因到 cost_bps。
6. 回滚：把 `top_n` 改回 10、`max_single_weight` 改回 0.10 即可；下次 run-weekly 自动按 10 选股。新增的 panels 即使 proposals/briefings 缺失也会显示占位，不会报错。

## Open Questions

- 50 的具体数值是否最优？50 是经验值；后续如果发现 NAV 的 IR 不增反降，可以试 30 或 80。这一 change 把数值提到 50 作为初始上限，不锁未来。
- "双方观察对照"是否每周一次也太频繁？可以在 dashboard 加 "只显示当周笔记"过滤；本 change 显示最新一份不论日期。
- 策略演进面板要不要直接渲染 `patch` 完整 JSON？倾向不渲染——容易页面爆炸；用户要看细节可以 cat 文件。摘要够了。
