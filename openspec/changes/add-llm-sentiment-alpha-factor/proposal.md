## Why

当前策略**完全脱离市场情绪与新闻输入**：

- factor_pipeline 只有量价 + 财报因子（PE/PB/ROE/毛利/负债/利润增长/动量/波动率/股息）
- 没有任何新闻、情感、市场情绪、宏观事件维度
- 5/15-5/22 实测中，外部突发利好（如新能源补贴政策）发生时，模型完全感知不到，只能等下次财报数据更新才能体现

**这与成熟量化的做法明显脱节**：

- Renaissance / Two Sigma / Citadel：alt-data 占 alpha 半壁江山（新闻 NLP、情绪、卫星、信用卡）
- 中国头部（幻方 / 衍复 / 明汯 / 九坤）：北上资金、龙虎榜、雪球股吧情绪、新闻 NLP 是标配
- 学术（Fama-French 后）：情感指数、投资者关注度、EPU 写进了多因子论文

**经典量价因子容量有限**，市场套利后超额收益持续衰减。alt-data 是稀缺信息源。

2026-05-25 与 human operator 重新明确了**这次 change 的特殊形态**：

> "LLM 分析就用对应的模型，比如 claude 跑就用 claude，codex 跑就用 codex"
> "LLM 跑用各自的 cli 或者客户端来跑，这样我是订阅制不用担心 token 问题"

→ 本 change 把 alt-data 落成"**双 agent 各自用自家 LLM 分析同一份新闻**"的形态。这同时实现三件事：
1. 引入情感因子（解决脱离市场情绪的核心问题）
2. 增加竞赛差异化维度（同样新闻，Claude vs Codex 模型读出来可能不同）
3. 绕过 §7.0 "Python 内禁调 LLM API" 约束（LLM 已在 CLI 会话内，给它"再多做一件事"是免费的）

## What Changes

### 1. 新数据层：新闻 fetch + 缓存

**新增** `stock_analyze/news/`：

- `fetch.py` — `prepare-news-data` CLI 子命令，从 Tushare `pro.news` / `pro.major_news` 拉指定时间窗口的财经新闻到 `data/shared/news_cache/<YYYY-MM-DD>/<source>.json`
- `ner.py` — Python 关键词匹配（基于 `stock_basic.name` + 简称 + cnspell）做新闻↔股票预匹配；不做 LLM NER，那一步留给 LLM 自己

数据流：

```
ECS daily 17:25 (现有 prepare-market-data 服务) 扩展：
  + prepare-news-data --as-of <today>
    → data/shared/news_cache/2026-05-25/sina.json
    → data/shared/news_cache/2026-05-25/cls.json
    → data/shared/news_cache/2026-05-25/ths.json
```

### 2. LLM 分析层（操作员驱动）

**新增** `stock_analyze/news/llm_analyzer.py` — **不调 LLM API**，是个分发器/调度器：

- 维护 `data/<agent>/alt_factors/_progress.json` 记录"哪些周已经分析完"
- 提供给 LLM CLI 会话调用的辅助方法：`next_pending_week()`、`get_news_for_stock_week(ts_code, week_end_date)`、`save_sentiment(...)`

**新增** slash command `/analyze-historical-news <agent> [--from YYYY-MM] [--to YYYY-MM]`：

- 操作员在 Claude Code（claude）或 Codex CLI（codex）里触发
- LLM 自己读 `next_pending_week()`，循环：
  1. 取该周需要分析的股票列表（universe ∩ 至少有 1 条新闻）
  2. 对每只股票，调 `get_news_for_stock_week(ts_code, week_end)` 拿到该股 7 天新闻
  3. LLM 用结构化 prompt 给出 `{sentiment_score, confidence, key_drivers}`
  4. 调 `save_sentiment(ts_code, week_end, score, confidence, drivers)` 写盘
  5. 更新 `_progress.json`
- 会话可随时中断；下次启动从 `_progress.json` 续跑

**新增** `/analyze-current-week-news <agent>` — 每周 review 时跑（短会话，~15 分钟）

### 3. 因子集成

**修改** `stock_analyze/factor_pipeline.py`：

- 新增 `load_agent_alt_factor(agent_id, factor_name, as_of)` 读 `data/<agent>/alt_factors/sentiment/<YYYY-MM>.csv`
- 与 shared factors（PE/ROE 等）合并到同一张 candidate × factor 表
- NaN（该股该周无新闻）按现有"重新归一权重"逻辑处理

**修改** `stock_analyze/overlay_guard.py`：

- `AVAILABLE_FACTORS` 白名单加规则：除现有 10 个固定名外，允许 `<agent_id>_*` 前缀（如 `claude_news_sentiment_1w`、`claude_news_sentiment_4w`）
- **跨 agent 隔离**：claude.yaml 只能用 `claude_*` 前缀因子，不能引用 `codex_*`（守卫 raise `OverlayCrossAgentFactor`）

**修改** `configs/agents/claude.yaml` 示例（仅作 demo，实际值由 LLM 月度演化决定。注意 repo 约定 `.yaml` 文件用 JSON 语法）：

```json
{
  "factors": {
    "pe": { "weight": 0.10, "direction": "low" },
    "roe": { "weight": 0.15, "direction": "high" },
    "...": "...",
    "claude_news_sentiment_1w": { "weight": 0.10, "direction": "high" }
  }
}
```

### 4. 回测集成

**修改** `stock_analyze/backtest/data_view.py`（来自 change `add-historical-backtest-engine`）：

- `PointInTimeView` 新增 `agent_alt_factor(agent_id, factor_name, as_of)` — 读 `data/<agent>/alt_factors/sentiment/<YYYY-MM>.csv`，过滤 `week_end_date <= as_of`
- 点状时间约束：回测在 t 时点只能看 t 之前已经分析完的 sentiment（操作员历史预热的产物）

**修改** `stock_analyze/backtest/engine.py`：

- `run_backtest(overlay, ..., agent_id)` 新增 `agent_id` 参数（之前无）
- 引擎根据 `agent_id` 决定该次回测能否读 `<agent_id>_*` 因子
- 如果 overlay 引用了 `<agent>_*` 因子但历史数据未预热完整 → raise `BacktestAltFactorMissing`，告知操作员先跑 `/analyze-historical-news`

**修改** `stock_analyze/backtest/gate.py`：

- 验证窗口回测前，先检查 alt-factor 历史覆盖完整性
- 不完整 → 提示操作员先预热，gate 暂时只验证经典因子部分
- 完整 → 走完整 gate 流程

### 5. Dashboard 集成

**修改** `stock_analyze/reporting.py`：

- 单 agent dashboard 加 **"情感因子时间线"** 面板：
  - 折线图：当前持仓的 Top10 股票，过去 26 周的 sentiment score 时序
  - 表格：本周持仓的 sentiment 分布（高/中/低 各几只）
- 因子贡献分解里把 `<agent>_news_sentiment` 也纳入归因

**修改** `stock_analyze/dashboard_aggregator.py`：

- 对比 tab 加 **"双方 LLM 对同一新闻的判断对比"** 面板：
  - 抽样：本周新闻量 Top 5 的股票
  - 列：股票 / claude 给的 score / codex 给的 score / 差值
  - 用途：让操作员看到"两个 LLM 模型在哪些股票上判断分歧最大"

新手 dashboard **不显示**（≤80KB anti-goal）。

### 6. CLAUDE.md / AGENTS.md 更新

- §4 加 `<agent>_*` 前缀因子的说明（属于 agent 私有因子，跨 agent 不可引用）
- §8 加 `data/shared/news_cache/`（共享，两 agent 都可读）+ `data/<other>/alt_factors/`（**禁读** — 对手的 LLM 分析产物属于对手"思考过程"）
- §10 加新的操作员动作："每月初 / 每周末 跑 `/analyze-historical-news` 或 `/analyze-current-week-news` 让 LLM 分析新闻"

### 7. 数据 schema

```
data/shared/news_cache/
├── 2021-01-04/
│   ├── sina.json        # 新浪财经
│   ├── cls.json         # 财联社
│   └── ths.json         # 同花顺
├── 2021-01-05/
│   └── ...
└── _meta.json           # fetch 进度 / 错误统计

data/<agent>/alt_factors/
├── sentiment/
│   ├── 2021-01.csv      # 按月分文件
│   ├── 2021-02.csv
│   └── ...
├── _progress.json       # LLM 分析进度（按周）
└── _epoch.json          # 分析 epoch 标记（LLM 版本 / prompt 版本变化时升 epoch，触发重跑）
```

`sentiment/<YYYY-MM>.csv` schema：

```
week_end_date, ts_code, sentiment_score, confidence, key_drivers, news_count, analysis_epoch
2021-01-08, 000001.SZ, 0.42, 0.75, "稳健股息预期上调|不良率改善", 5, 1
2021-01-08, 000002.SZ, -0.31, 0.60, "三道红线压力", 3, 1
...
```

## Capabilities

### New Capabilities

- `news-data-fetch` — `prepare-news-data` CLI + Tushare news 多源接入 + 缓存写盘
- `news-stock-ner` — Python 关键词匹配做新闻 ↔ 股票预匹配，不依赖 LLM
- `llm-sentiment-analysis-workflow` — slash command + progress tracking + epoch management，跨 CLI 会话续跑
- `agent-specific-alt-factor-pipeline` — factor_pipeline 支持 `<agent>_*` 前缀因子，跨 agent 隔离
- `sentiment-factor-backtest-integration` — backtest engine + gate 支持 agent-specific alt-factor
- `cross-llm-comparison-dashboard` — dashboard 对比 claude/codex LLM 对同一新闻的判断分歧

### Modified Capabilities

- `competition-baseline-fairness`：`AVAILABLE_FACTORS` 白名单扩展规则
- `historical-backtest-engine`（待 `add-historical-backtest-engine` 落地后）：`run_backtest` + `gate.validate` 加 agent_id 参数 + 缺失数据处理
- `multi-agent-runtime`：CLAUDE.md / AGENTS.md §4 / §8 / §10 更新
- `dashboard`：专业版加情感因子时间线 + LLM 判断对比面板

## Impact

- **代码**：1 新模块 `stock_analyze/news/`（~600 行）+ factor_pipeline / overlay_guard / backtest / reporting 多处集成（~400 行）+ 2 个新 slash command + 2 个新 CLI 子命令 + ~500 行测试 = **~1500 行新增**
- **配置**：无新增配置字段。agent overlay 可选加入 `<agent>_*` 因子。
- **数据 / 产物**：
  - `data/shared/news_cache/` — 一次性预热 5 年历史 ~500MB，后续每日增量 ~1MB
  - `data/<agent>/alt_factors/sentiment/` — 双方各 ~50MB（4 年 × 800 票 × ~52 周 × ~80 bytes）
- **网络**：Tushare news 接口属 2000 积分包内（需 confirm）。预估每日 ~50 次调用（按源 + 按时间段分批），完全在配额内
- **LLM 调用**：
  - 一次性预热：双方各 ~166K 次（4+1 年 × 208 周 × 800 票），分多次 ~6-12 小时会话完成
  - 增量：每周末 / 每月初 ~800 次（~15 分钟会话）
  - **Token 成本**：0（操作员订阅制覆盖）
- **文档**：
  - 新增 `docs/llm-sentiment-factor-flow.md` 完整流程
  - 更新 `docs/system-overview.md` §4d（agent CLI 分析闭环）+ §6（因子流水线）
  - CLAUDE.md / AGENTS.md §4 / §8 / §10 更新
- **不在范围**：
  - 不引入卫星图像 / 信用卡刷卡 / 招聘网站等高成本另类数据
  - 不做雪球 / 股吧 / 微博爬虫（反爬 + 法律风险）
  - 不做 LLM 跨 agent 互评（不让 claude 评 codex 的情感判断质量）
  - 不引入第三方 LLM API 客户端到 Python 内（坚守 §7.0）
  - 不实时处理新闻（始终 batch；最快粒度 = 1 周）
  - 不做事件驱动（情感是连续因子，不是事件 dummy）

## 与已有 / 计划中 change 的关系

- **`add-historical-backtest-engine`（DRAFT，本会话同时提案）**：**强依赖**。本 change 的 alt-factor 要进回测 gate，必须等回测引擎先落地。实施顺序：A 先 → B 后。
- `enable-llm-direct-strategy-evolution`（已落地）：本 change 扩展 LLM 在 monthly 之外的另一种 CLI 工作（weekly 分析新闻），不冲突。
- `migrate-data-source-to-tushare-pro`（已落地）：news 数据继续走 Tushare Pro，沿用现有 token 机制。
- `introduce-shared-market-data-pipeline`（已落地）：news 数据走同样的 ECS pipeline 模式，`prepare-news-data` 作为 daily pipeline 的 sibling 任务。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| LLM 输出非完全确定（同一新闻同一模型可能给不同分） | temperature=0 + 结构化 JSON output + `(ts_code, week_end, news_hash)` 缓存（同一输入第二次直接读缓存） |
| LLM 版本升级 / prompt 改动 → 历史结果失效 | `analysis_epoch` 字段；升 epoch 时显式重跑历史 |
| 操作员中途放弃 → 预热不完整 | `_progress.json` 支持断点续跑；gate 处理"部分历史缺失"场景（暂时只验证经典因子） |
| Tushare news API 可能不在 2000 积分包 | 实施前必须 confirm，备选：抓取 sina 财经 RSS（akshare 现有接口但要保留），或买 Tushare 4000 积分（~¥500/年） |
| 双方 LLM 判断差异成为"性能差异"而非"策略差异" | 这是本 change 的**有意设计** — 用户明确要求"用对应模型"。竞赛维度从"策略 + LLM 决策"扩展到"策略 + LLM 决策 + LLM 语言理解"。文档明示这一点 |
| 训练窗口新闻"看穿"未来 | LLM prompt 显式限定"基于 [week_start, week_end] 内的新闻"；不传入更新的新闻；操作员在批量分析时按时间顺序跑（旧的先跑），避免认知混淆 |
| 操作员一次跑几小时容易疲劳/犯错 | slash command 内部 LLM 跑批；操作员只需"启动会话 + 偶尔回车确认"；进度持久化 |
| codex CLI 的工具能力可能与 Claude Code 不对等 | 提供两个 agent 等效的 helper API（`next_pending_week` 等）；prompt 模板尽量与 LLM 工具无关 |

## Agent 来源声明

本 change 由 claude agent 在 2026-05-25 brainstorming session 中草拟，基于 human operator 的核心要求：

> "我们现在的策略都是自顾自的统计分析，好像没有外部的新闻、前沿动向、市场情绪输入...该不该把这些纳入进来"
> "选 A（alpha 因子），LLM 分析就用对应的模型"
> "LLM 跑用各自的 cli 或者客户端来跑，这样我是订阅制不用担心 token 问题"

设计选择路径（brainstorming 决策树）：

| 分支 | 选项 |
|---|---|
| 1. 角色 | a — alpha 因子 |
| 2. 深度 | 3 — 重档（含历史预热） |
| 3. §7.0 处理 | α 变体 — 操作员驱动 CLI，但用订阅消除 token 顾虑 |
| 4. 颗粒度 | Z — 股票 × 周 |

改动覆盖 `stock_analyze/*.py`、`configs/agents/*.yaml`（仅 demo）、`CLAUDE.md` / `AGENTS.md`、`docs/*.md`、`.claude/commands/`，均在 `CLAUDE.md §7` 禁地列表 — **必须由 human operator 显式邀请实施**。

**Status：DRAFT，await confirmation。本 change 实施前必须先完成 `add-historical-backtest-engine`。**
