## Why

当前策略**完全脱离市场情绪与新闻输入**：

- factor_pipeline 只有量价 + 财报因子（PE/PB/ROE/毛利/负债/利润增长/动量/波动率/股息）
- 没有任何新闻、情感、宏观事件维度
- 突发利好/利空发生时模型只能等下次财报数据才能感知

**这与成熟量化做法明显脱节**：Renaissance / Two Sigma / 中国头部（幻方 / 衍复 / 明汯）alt-data 都是标配。经典量价因子容量有限，市场套利后超额收益持续衰减。

2026-05-25 与 human operator 的 brainstorming 确认：

> "LLM 分析就用对应的模型，比如 claude 跑就用 claude，codex 跑就用 codex"
> "LLM 跑用各自的 cli 或者客户端来跑，订阅制不用担心 token 问题"

详细推演中发现"per-stock 周度 LLM 分析" 的颗粒度 Z 在历史回填上需要 ~500+ 小时/agent，不现实。

经过 5 个决策点的 brainstorming 收敛，落到 **MVP（Path 2）**：

- 颗粒度从"股票 × 周"降到"**市场整体 × 周**"
- 数据源从"Tushare 新闻包 ¥1000/年" 改为 "**LLM 客户端自带 web search**"
- 历史回填从"4+1 年预热"改为"**不做，live-only**"

→ 用户的核心 ask "纳入市场情绪与新闻" 在 MVP 里仍然满足，但**前期投入降到接近零**。如果跑 6 个月真有 alpha，再 confirm 后续 Phase 升级。

## What Changes

### 1. 新数据：每周 1 行的 sentiment.csv

操作员每周末手动跑：

```
打开 Claude.ai / Claude 桌面版（claude side）
  发 prompt（见 design.md §3 的模板）
  → LLM 自带 web search 拉本周 A 股市场新闻
  → 输出严格 JSON：{sentiment_score, confidence, key_drivers, ...}
操作员把 JSON 粘贴到本地文件:
  data/claude/alt_factors/market_sentiment.csv（追加 1 行）

同样对 codex 跑（操作员开 ChatGPT / ChatGPT 桌面版）
```

CSV schema：

```
week_end_date, sentiment_score, confidence, key_drivers, llm_model, recorded_at
2026-05-25, 0.32, 0.78, "AI 算力链上涨|地产持续承压|外资流入回暖", claude-sonnet-4.5, 2026-05-25T20:30:00
```

### 2. 新 CLI 子命令：`record-sentiment`

```bash
python3 -m stock_analyze record-sentiment \
  --agent claude --week-end 2026-05-25 \
  --score 0.32 --confidence 0.78 \
  --drivers "AI 算力链上涨,地产持续承压,外资流入回暖" \
  --llm-model claude-sonnet-4.5
```

把操作员的"粘贴动作"封装为可追溯的命令，自动写盘 + sanity check（score / confidence 范围）+ 防重复（同一 week_end 不能写两次，除非 `--force`）。

### 3. factor_pipeline 集成

`stock_analyze/factor_pipeline.py` 加 `load_agent_market_sentiment(agent_id, as_of) -> float`：

- 读 `data/<agent>/alt_factors/market_sentiment.csv`
- 取最近 `week_end_date <= as_of` 的一行
- 返回 sentiment_score（float in [-1, 1]）
- 该值**广播**到所有候选股 — 因子矩阵中这一列对所有股相同

在因子流水线中：

```
原值 → winsorize → z-score → 行业中性化 → 加权
```

market_sentiment 是市场级（横截面同值），所以 winsorize / z-score / 行业中性化对它**全部跳过**（这一列没有横截面差异）。**等同于"按 sentiment_score × weight 整体上下平移所有股票的综合分"**。

这意味着 market_sentiment 因子**不直接影响选股相对排名**（所有股票被平移同样数值）。它影响的是当系统使用 sentiment 作为某种 gate / 调节器时（如：sentiment < -0.5 时减仓）—— 但 MVP 不实现 gate，先把数据通路打通。

→ **MVP 实质是：先让数据进来、Dashboard 可见、操作员每周写一行**。把"sentiment 如何真正影响选股"留给 Phase 2 — 那时候 per-stock 颗粒度的因子才让横截面 z-score 真正有意义。

**这是个有意识的设计选择，必须在文档里说清楚**：MVP 是"通路建好 + 行为习惯建立"，不是"立刻产生 alpha"。

### 4. overlay_guard 扩展

`AVAILABLE_FACTORS` 白名单加 `<agent_id>_*` 前缀规则，跨 agent 引用拒绝。

MVP 阶段只实现 `<agent>_market_sentiment_1w` 一个因子名。

### 5. Dashboard 集成

专业版 dashboard 加 **"市场情感时间线"** 面板：

- 双线图：claude 和 codex 各自的 market_sentiment_1w 过去 26 周时序
- 配对柱状图：本周双方对市场的判断对比
- 文字表：本周双方的 key_drivers（让操作员一眼看出"两个 LLM 模型在关注什么不同的事情"）

新手版不动。

### 6. CLAUDE.md / AGENTS.md 更新

- §4 加 `<agent>_market_sentiment_1w` 因子说明
- §8 加 "可读 data/shared/news_cache/"（虽然 MVP 不实施这个目录，先把规则定好为 Phase 2 铺路）
- §10 加新动作 "每周末用客户端跑 market_sentiment 分析 + record-sentiment 命令落盘"

### 7. 演进路线写入设计文档

`design.md §11` 用一节明文写入：

```
Phase 1 (本 change MVP)
Phase 2: 加 Tushare 新闻包 + news_volume 因子 + 历史回填 + 回测集成
Phase 3: per-stock LLM sentiment（颗粒度 Z）
Phase 4: 事件型因子 / 跨市场信号 / 其它 alt-data
```

每个 Phase 列出：触发条件（什么时候该上）、新增产物、依赖前一 Phase 的什么、估算工作量。

## Capabilities

### New Capabilities

- `weekly-market-sentiment-recording` — 操作员每周手动从客户端 LLM 拿到结果 + CLI 命令落盘
- `agent-specific-broadcast-alt-factor` — factor_pipeline 支持"市场级横截面同值"因子（与现有 per-stock 因子的处理路径区分）

### Modified Capabilities

- `competition-baseline-fairness`：`AVAILABLE_FACTORS` 加 `<agent_id>_*` 前缀规则 + 跨 agent 拒绝
- `multi-agent-runtime`：CLAUDE.md / AGENTS.md §4 / §8 / §10 更新
- `dashboard`：专业版加市场情感时间线面板

## Impact

- **代码**：~200 行新增（`stock_analyze/alt_factors/sentiment.py` ~80 行 + factor_pipeline / overlay_guard / cli.py / reporting.py 各 ~30 行 + 测试 ~100 行）
- **配置**：无新增字段
- **数据 / 产物**：
  - `data/<agent>/alt_factors/market_sentiment.csv` — 每周 1 行追加，全年 ~52 行
- **网络**：零（不调任何外部 API）
- **依赖**：无新增第三方包
- **LLM 调用**：操作员客户端订阅覆盖，**零 token 成本**
- **操作员时间**：~10 分钟/agent/周
- **历史回填**：**不做**（明确取舍）
- **文档**：
  - 新增 `docs/llm-sentiment-factor-flow.md`（含 MVP 流程 + 演进路线）
  - 更新 `docs/system-overview.md` §4d / §6 / §13
  - CLAUDE.md / AGENTS.md §4 / §8 / §10
- **不在范围**（明确列入 Phase 2+）：
  - Tushare 新闻包订阅
  - Python 内新闻 fetch / 缓存 / NER
  - per-stock 颗粒度的情感因子
  - 历史回填
  - 回测引擎的 alt-factor 集成
  - 事件型 / 社交媒体 / 卫星等其它 alt-data

## 与已有 / 计划中 change 的关系

- **完全正交** — 不依赖 `add-historical-backtest-engine`（因为本 change 不进回测）
- `enable-llm-direct-strategy-evolution`（已落地）：LLM 月度演化时新增"看 market_sentiment 时间线"的输入维度
- `migrate-data-source-to-tushare-pro`（已落地）：MVP 不动 Tushare 数据通路
- `introduce-shared-market-data-pipeline`（已落地）：MVP 不动 ECS pipeline

**两个 change（A 回测 + B 情感）可以并行开发，互不阻塞。**

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| Claude.ai / ChatGPT web search 找的新闻质量参差 | prompt 明确指向"权威财经媒体（财联社/新浪财经/同花顺/澎湃财经）"，让 LLM 偏向高质量来源 |
| 同一周操作员重复跑 → 不同结果 | CLI 默认拒绝重复写入（要 `--force`），且 csv 记录 `recorded_at` 时间戳 |
| 操作员忘记跑（某周漏掉）| factor_pipeline 找不到当周值时使用最近一周值（sticky），并在 dashboard 标注"已 N 周未更新"提醒 |
| MVP 没有 alpha → 浪费操作员时间 | 6 个月后 review，看 sentiment 因子与持仓收益的相关性。无 alpha 就关掉该因子，没有沉没成本 |
| 双 LLM 偏见不同 → "公平性"质疑 | 这是**有意设计**。两个 LLM 各自世界观/训练语料/搜索引擎不同 → 各自的 market sentiment 不同 → 这正是竞赛的差异化维度 |
| 操作员 prompt 微调 → 不可复现 | prompt 模板锁定在 `stock_analyze/alt_factors/prompts/market_sentiment_v1.md`，版本化管理；改 prompt 升 epoch |
| LLM web search 偶尔搜不到内容 / 拒绝回答 | 操作员可补救：自己粘 5-10 条新闻给 LLM 让它评分。同一 csv schema |

## Agent 来源声明

本 change 由 claude agent 在 2026-05-25/26 brainstorming session 中草拟。Brainstorming 路径：

| 决策点 | 选择 | 备注 |
|---|---|---|
| 1. 角色 | a — alpha 因子 | 与现有因子框架同构 |
| 2. 深度 | 3 → 3'（pivot） | 原本选 3，发现历史回填成本远超预算后 pivot 到轻量版重档 |
| 3. §7.0 处理 | 操作员驱动（订阅制） | 避免 Python 内 API 调用 |
| 4. 颗粒度 | Z → 市场级（再 pivot） | Z 在历史回填上不可行；MVP 退到市场级 |
| 5. 数据源 | Path 2（LLM web search） | 不付 Tushare ¥1000/年，跳过新闻 fetch 层 |

改动覆盖 `stock_analyze/*.py`、`configs/agents/*.yaml`（示例性，不锁字段）、`CLAUDE.md` / `AGENTS.md`、`docs/*.md`、`.claude/commands/`，均在 `CLAUDE.md §7` 禁地 — **必须由 human operator 显式邀请实施**。

**Status：DRAFT，await confirmation。MVP 阶段不依赖任何其它 change。**
