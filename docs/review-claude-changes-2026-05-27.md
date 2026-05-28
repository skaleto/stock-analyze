# Claude 最近改动 Review（2026-05-27）

## 背景

本次 review 针对 2026-05-26 到 2026-05-27 期间 Claude 侧提交的一批改动。主要改动方向包括：

- 历史回测引擎与 backtest floor gate
- LLM 市场情绪 alt-factor
- pipeline 状态面板、失败告警、sanity-check
- `data_provider` / `reporting` 拆分重构
- dtype 修复、weekly 脚本和 ECS 同步脚本调整

Review 过程中遵守当前 `AGENTS.md` 的竞赛隔离规则：没有读取 `data/claude/notes/*`、`data/claude/alt_factors/*`、`reports/claude/*`、Claude 持仓/净值/交易等禁区内容。

## 总体结论

这些改动的方向是对的，但现在有两个问题会明显影响“策略评估是否可信”：

1. 回测门禁没有真正验证候选 overlay / 真实因子策略。
2. 历史回测的指数成分股缓存可能缺月份或空表。

真实 forward paper-trading 的持仓/NAV 大概率不直接受这两个问题影响；但如果用当前 backtest gate 去判断月度 LLM 自动调参是否安全，结论不可靠。

## Findings

### P1：回测门禁没有验证真实候选策略

影响：

- `backtest --overlay` 参数是必填，但 `_command_backtest` 实际没有读取 `args.overlay`，而是直接 `competition.load(args.agent)`。
- `backtest/engine.py` 目前仍是低 PE top-N 简化选股，不是 forward 模拟使用的 `build_signals` / `factor_pipeline`。
- `evolution_writer.write_evolution` 调用 backtest gate 时，看起来会 gate 新 overlay，但底层回测逻辑并没有完整反映新 overlay 的因子权重、过滤条件和组合约束。

相关位置：

- `stock_analyze/cli.py:136`
- `stock_analyze/cli.py:609`
- `stock_analyze/backtest/engine.py:9`
- `stock_analyze/backtest/engine.py:240`
- `stock_analyze/evolution_writer.py:130`

建议：

1. 让 `backtest --overlay` 真实读取传入的 overlay 文件，并与 competition baseline 合并/校验。
2. backtest 信号生成应复用 forward 链路的 `build_signals` / `factor_pipeline`，或实现等价的 point-in-time provider。
3. 加回归测试：两个 overlay 因子方向或权重大幅相反时，backtest 的 signals/trades/metrics 应出现差异。

### P1：指数成分股月度快照可能漏数或空表

影响：

- `prepare-backtest-data` 用每月 1 号调用 `pro.index_weight(trade_date=YYYYMM01)`。
- 月初经常不是交易日，也不一定是指数权重发布日期；Tushare 返回空表时，当前代码仍会把该月写入 `_meta.json` 的 `index_weight_months_done`。
- 这会让历史回测 universe 偏小，甚至导致某些月份没有候选股，进而污染收益、回撤、Sharpe 等回测指标。

相关位置：

- `stock_analyze/backtest/data_prep.py:200`
- `stock_analyze/backtest/data_prep.py:318`

建议：

1. 用月度窗口查询最近可用权重快照，而不是固定月初 `trade_date`。
2. 如果返回空表，不应标记该月 done；至少写 warning，并在 gate 前做 cache completeness check。
3. 给真实/模拟空响应补测试：空 index_weight 文件不得被视为成功缓存。

### P2：sanity-check 还没有真正接入自动链路

影响：

- `sanity_check.py` 注释写的是 weekly 后运行、写 `logs/sanity_check.log`、严重问题触发 `PIPELINE_FAILURES.log`。
- 但 systemd weekly service 只执行 `run-weekly --offline`，`OnSuccess` 只是刷新 dashboard。
- `scripts/weekly.sh` 也没有调用 `sanity-check`。
- 因此当前 sanity-check 更像手动工具，不能自动兜住 NAV 跳变、持仓数量异常、IC 覆盖异常。

相关位置：

- `stock_analyze/sanity_check.py:22`
- `deploy/systemd/stock-analyze-codex-weekly.service:5`
- `deploy/systemd/stock-analyze-claude-weekly.service:5`

建议：

1. 增加 `stock-analyze-<agent>-sanity.service`，在 weekly success 后触发。
2. 或在 weekly service 的 `ExecStartPost` 调用 sanity-check，并将 critical 结果写入失败告警日志。
3. dashboard 的 pipeline panel 可以展示最近 sanity-check 结果。

### P2：sync-to-ecs.sh 对 agent 隔离不安全

影响：

- `scripts/sync-to-ecs.sh` 遍历 `data/*`，会同步所有 agent 的 notes、evolution_log、evolution_diff、config_evolution、alt_factors 和 overlay。
- 当前 `AGENTS.md` 明确限制 Codex 不应读写 Claude 的 notes、alt_factors、reports、持仓/净值等内部路径。
- 如果 Codex 侧直接执行这个脚本，容易越过公平边界，且可能误推对手数据。

相关位置：

- `scripts/sync-to-ecs.sh:47`
- `scripts/sync-to-ecs.sh:68`
- `scripts/sync-to-ecs.sh:84`

建议：

1. 拆成 operator-only 全量同步脚本和 agent-scoped 同步脚本。
2. agent 运行时默认只允许同步自己的 `data/<agent>/...` 和 `configs/agents/<agent>.yaml`。
3. 增加 `SA_AGENT_SCOPE=codex|claude|all`，其中 `all` 必须显式声明为 operator mode。

### P3：sentiment broadcast 因子目前不产生选股 alpha

影响：

- 当前 broadcast factor 是给所有候选股票加同一个常数，股票之间的相对排序不变。
- 因此它对 top-N 选股没有实际影响，更多是 dashboard 叙事/记录。
- docstring 说 broadcast contribution 会出现在 `factor_table` 的 `code='__broadcast__'` 行，但实际 `factor_table` 只包含 classic factor rows。

相关位置：

- `stock_analyze/factor_pipeline.py:135`
- `stock_analyze/factor_pipeline.py:258`
- `stock_analyze/factor_pipeline.py:277`

建议：

1. 如果只是做市场温度叙事，明确标注“不影响排序”。
2. 如果希望影响策略，应接到仓位、风险开关、行业偏好、现金比例或 max exposure，而不是 uniform score shift。
3. 修正文档/注释，或真的把 broadcast contribution 写入 factor table。

## 对结果的影响判断

- 对当前真实 forward paper-trading 的已生成持仓/NAV：影响有限，主要不直接改历史 ledger。
- 对历史回测、月度自动调参、backtest gate：影响较大，当前结论不能完全信。
- 对运维告警：有改善，但 sanity-check 自动化还未闭环。
- 对 LLM sentiment alpha：当前更多是记录和展示，不是有效选股因子。

## 建议修复顺序

1. 修 `backtest --overlay`，确保研究 CLI 和 gate 使用真实候选 overlay。
2. 将 backtest scoring 接入 forward `build_signals` / `factor_pipeline`，并补“不同 overlay 产生不同 signals”的测试。
3. 修 index_weight 月度缓存逻辑，空表不得标记 done。
4. 给 `sync-to-ecs.sh` 增加 agent scope / operator mode。
5. 接入 sanity-check 到 weekly systemd 或 weekly.sh。
6. 明确 sentiment broadcast 的定位：展示因子，或改造成真正影响风险暴露的策略输入。

## 本次验证证据

本地验证命令：

```bash
python3 -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile stock_analyze/*.py stock_analyze/backtest/*.py stock_analyze/data_provider/*.py tests/*.py
git diff --check HEAD~20..HEAD -- . ':(exclude)data/claude/**' ':(exclude)reports/claude/**'
openspec validate add-historical-backtest-engine --strict
openspec validate add-llm-sentiment-alpha-factor --strict
openspec validate bridge-factor-pipeline-into-backtest --strict
python3 -m stock_analyze validate-overlay --agent codex
```

结果：

- Unit tests：`Ran 362 tests ... OK`
- Python compile：OK
- `git diff --check`：OK
- 三个 OpenSpec change：valid
- Codex overlay guard：OK

