# 实施报告 · add-llm-sentiment-alpha-factor (MVP / Path 2)

**实施日期**: 2026-05-26
**状态**: ✅ **全链路代码完成**，测试 324/324 通过
**模式**: 主 session inline 执行（Opus 4.7）

---

## 1. 完成度

| Task | 状态 | Commit | 描述 |
|------|------|--------|------|
| 1. OpenSpec specs scaffolding | ✅ | `09e8edb` | 2 capability spec（Requirements + Scenarios）|
| 2. alt_factors/sentiment.py 核心 | ✅ | `1567f73` | `record_market_sentiment` + `load_latest_market_sentiment` + `load_sentiment_history` + `remove_sentiment` + `DuplicateSentimentEntry`，原子写、严格校验 |
| 3. record-sentiment CLI | ✅ | `fea8dba` | 新子命令；exit 0 成功 / 1 重复或校验失败 |
| 4. sentiment-log CLI | ✅ | `fea8dba` | 同 commit；列表 / `--last N` / `--remove` |
| 5. factor_pipeline broadcast | ✅ | `950573d` | `is_broadcast_factor` + `load_broadcast_factor` + `process_factors(broadcast_values=)` 均匀广播 sign×weight×value |
| 6. overlay_guard cross-agent 隔离 | ✅ | `df3b500` | `CLASSIC_FACTORS` + `AGENT_ALT_FACTOR_PATTERN` + `OverlayCrossAgentFactor` + `validate_factor_name(name, agent_id)` |
| 7. Prompt template | ✅ | `9ec1451` | `stock_analyze/alt_factors/prompts/market_sentiment_v1.md` |
| 8. Dashboard 面板 | ✅ | `1f38e48` | `render_market_sentiment_panel` + `render_sentiment_comparison_panel` |
| 9. CLAUDE.md / AGENTS.md 更新 | ⏸️ | 操作员手工 | 锁定文件 |
| 10. docs/ 系统文档 | ⏸️ | 操作员手工 | 同上 |
| 11. E2E 验证 | ✅ | `b301699` | 3 个端到端测试：in-process 全链路 + CLI subprocess + 重复拒绝 |

**9/11 task 代码完成，剩 2 个文档 task 留给操作员手工合入。**

---

## 2. 测试结果

```
Ran 324 tests in 14.872s
OK
```

| 测试文件 | 数量 |
|---|---|
| `test_alt_factors_sentiment.py` | 15 |
| `test_cli_record_sentiment.py` | 5 |
| `test_cli_sentiment_log.py` | 6 |
| `test_factor_pipeline_broadcast.py` | 10 |
| `test_overlay_guard_alt_factors.py` | 8 |
| `test_reporting_sentiment_panel.py` | 8 |
| `test_e2e_sentiment_pipeline.py` | 3 |
| **本 change 新增** | **55** |
| **既有（无回归）** | **269** |
| **合计** | **324 ✅** |

---

## 3. End-to-End 证据

### 3.1 全链路 in-process

```
record_market_sentiment(score=0.50)
  ↓
load_broadcast_factor() → 0.50
  ↓
process_factors(broadcast_values={"claude_market_sentiment_1w": 0.50})
  ↓
每个 candidate 的 score 都加 +0.05 (sign × weight × value = +1 × 0.10 × 0.50)
```

✓ 通过

### 3.2 CLI subprocess

```bash
$ python3 -m stock_analyze record-sentiment --agent claude --week-end 2026-05-22 \
    --score 0.32 --confidence 0.78 --drivers "AI 算力链回暖,央行 MLF 偏鸽" \
    --llm-model claude-sonnet-4.5
✓ recorded claude 2026-05-22 score=+0.32 confidence=0.78; csv now has 1 weeks

$ python3 -m stock_analyze sentiment-log --agent claude
2026-05-22  score=+0.32  conf=0.78  drivers="..."  (claude-sonnet-4.5)
```

✓ 通过

### 3.3 重复拒绝

第二次 record-sentiment 同一 week_end 不带 `--force` → exit 1 + stderr 含 `already`

✓ 通过

---

## 4. ECS 部署

5 个修改/新增文件已 scp 到 ECS（`alt_factors/`、`factor_pipeline.py`、`overlay_guard.py`、`cli.py`、`reporting.py`、`dashboard_aggregator.py`），模块导入测试通过：

```
$ ssh ai_baby 'cd /opt/stock-analyze/app && python3 -c "
from stock_analyze.alt_factors import sentiment
from stock_analyze import factor_pipeline, overlay_guard
print(factor_pipeline.is_broadcast_factor(\"claude_market_sentiment_1w\"))
"'
True
```

⚠️ ECS 上还没有 sentiment.csv 数据 — 等操作员第一次跑 prompt 才会写。

---

## 5. 关键设计决策

### 5.1 Broadcast factor = 纯加性常数

广播因子的值是跨股票常数。它走 winsorize/z-score 没意义（一个常数没有横截面方差）。决定：classic pipeline 算完后，broadcast 作为 `sign×weight×value` 加在每个 candidate 的 score 上。

**实践含义**：MVP 阶段 broadcast factor 对**横截面排名零影响**（所有股票被同样数值上下平移）。MVP 不立即产生 alpha；它的作用是**建数据通路 + 形成 operator 周度行为习惯**。等 Phase 3 升级到 per-stock 颗粒度后才真正影响选股。

### 5.2 Cross-agent 隔离：异常区分

`OverlayUnknownFactor`（不在白名单内）vs `OverlayCrossAgentFactor`（借用对方的私有因子）有意区分。后者明确告知 operator："你试图引用 codex 的私有数据"，匹配 CLAUDE.md §7.1 "能看到对手的阵型 (yaml)，看不到对手的思考 (alt_factors)"。

### 5.3 Prompt / 数据版本化

- `prompt_version` 默认 `v1`，模板在 `stock_analyze/alt_factors/prompts/market_sentiment_v1.md`，下次改 wording 时 bump 到 `v2`
- CSV 每行带 `prompt_version` 字段，下游可按 epoch 区分
- `|` 作 drivers/sources 内部分隔（中文文本含 `,` 风险高）

### 5.4 原子写

`record_market_sentiment` 用 `tempfile.mkstemp + os.replace` 原子写，避免半截 CSV。

---

## 6. 留给操作员的 Follow-up

### 6.1 P0 — production-ready 必做

1. ✅ **完成（commit `df0e728`）**: **simulator/strategy 集成 broadcast_values 解析** — 已加 `_resolve_broadcast_values(config, as_of, repo_root)` helper + `build_signals` 加 `repo_root` 可选参数 + simulator.generate_rebalance_orders 透传。**ECS round-trip 实测通过**：record sentiment 0.50 → 三个 candidate score 全部 +0.05 uniform shift。

2. **手工合入 CLAUDE.md / AGENTS.md 改动**（Task 9）：
   - §4 加 `claude_market_sentiment_1w` 因子说明 + "MVP 不立即产生 alpha" 注脚
   - §7.1 加 "不可读 `data/<other>/alt_factors/*`"
   - §10 加新动作 "每周末跑 record-sentiment"

3. **新增 `docs/llm-sentiment-factor-flow.md`**（Task 10）

4. **首次实测**：周六 5/30 之前，在 Claude.ai + ChatGPT 上各跑一次 prompt → record-sentiment → 看 dashboard 面板

### 6.2 P1 — 可推迟

5. `sentiment-log --remove` 加交互确认
6. Dashboard 面板嵌入到聚合页（`generate_competition_dashboard` 内）

### 6.3 P2 — 演进路线（独立 OpenSpec change）

7. **Phase 2**: Tushare 新闻包 ¥1000/年 + news_volume 因子 + 历史回填
8. **Phase 3**: per-stock LLM sentiment（颗粒度 Z），真正影响横截面排名

---

## 7. 全链路状态

```
代码:     9/11 task + P0 集成胶水完成（59 新增测试 + 269 既有 = 328/328 ✓）
ECS:      7 个修改文件部署，import smoke + 实战 round-trip 通过
prompts:  v1 模板在位
CLI:      record-sentiment + sentiment-log live
Dashboard: 2 个面板渲染函数就绪
集成胶水: ✅ build_signals 自动解析 broadcast 因子值（commit df0e728）
数据:     sentiment.csv 0 行（操作员未跑过）

ECS 实战验证（5/26 round-trip）:
  - record_market_sentiment(0.50) →
  - build_signals() 内部自动 load_broadcast_factor →
  - process_factors(broadcast_values={'claude_market_sentiment_1w': 0.50}) →
  - 三个 candidate 的 score 都加 +0.05（uniform, spread=0）
  → 全链路 live ✓

剩余阻塞 / 待操作员:
  · CLAUDE.md / AGENTS.md / docs/ 文档合入（Task 9-10，§7.0 锁）
  · 周六 5/30 第一次实战：在 Claude.ai / ChatGPT 用 v1 prompt 跑 → record-sentiment
    → 等周六 weekly-trigger 自动 generate_rebalance_orders → 看 dashboard 面板变化
```

## 8. 完成证据

```
分支：main (origin/main 同步至 b301699)
commits 范围：09e8edb → b301699
新增文件：13 (stock_analyze/alt_factors/* + tests/* + specs/* + 本报告)
修改文件：5 (factor_pipeline / overlay_guard / cli / reporting / dashboard_aggregator)
锁字段未触：✓
测试：324/324 ✓
端到端：CLI subprocess + in-process 全链路验证通过 ✓
ECS 部署：5 个模块导入测试通过 ✓
```
