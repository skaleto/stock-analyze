# Design · add-llm-sentiment-alpha-factor

## 1. 目标与范围

把"市场新闻情感"作为一个 alpha 因子加入策略，**以最小成本**先把通路打通；后续按真实证据决定要不要升级。

**MVP 明确选择的妥协**：
- 颗粒度：市场级（每周 1 个数），不是 per-stock
- 数据：操作员客户端 LLM 自带 web search 即时产出，不预存
- 持续性：仅 live，不进回测
- 操作员动作：每周末 ~10 分钟手动跑

**MVP 不立刻产生 alpha** — 因为单一市场级因子被广播到所有股票，不直接影响选股相对排名。MVP 的实质是"**建数据通路 + 形成行为习惯**"，为 Phase 2 升到 per-stock 颗粒度铺路。这一点必须在 README + CLAUDE.md §10 写明，避免误期待。

## 2. 整体架构

```
┌────────────────────────────────────────────────────────────────┐
│  Layer 1：操作员每周末手动动作（10 分钟/agent）                  │
│                                                                │
│  Step 1: 开 Claude.ai 或 Claude 桌面版（claude side）           │
│  Step 2: 发标准 prompt（见 §3）                                 │
│           → LLM 自带 web search 拉本周财经新闻                  │
│           → LLM 输出严格 JSON                                   │
│  Step 3: 跑 `python3 -m stock_analyze record-sentiment ...`     │
│           → 落 data/claude/alt_factors/market_sentiment.csv     │
│  Step 4: 对 codex 同样做一遍（开 ChatGPT）                       │
└────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  Layer 2：Python 数据层                                          │
│                                                                │
│  stock_analyze/alt_factors/sentiment.py                        │
│    · CSV 读写                                                   │
│    · `record_market_sentiment(agent, week_end, score, ...)`    │
│    · `load_latest_market_sentiment(agent, as_of) -> float`     │
│    · sanity check（score ∈ [-1,1], confidence ∈ [0,1]）          │
│    · 防重复（同一 week_end 不能两次写入除非 force）              │
└────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  Layer 3：factor_pipeline 集成                                   │
│                                                                │
│  factor_pipeline.py 加入 broadcast factor 概念：                │
│    · 该列对所有候选股相同（广播）                                │
│    · winsorize / z-score / 行业中性化全部跳过                    │
│    · 直接用原 sentiment_score × overlay 给的 weight              │
│    · NaN 时（操作员漏跑） → 按现有缺失因子分摊逻辑               │
└────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  Layer 4：Dashboard 可见                                         │
│                                                                │
│  专业版 "市场情感时间线" 面板：                                  │
│    · claude 和 codex 各 26 周 sentiment_score 双线              │
│    · 本周配对柱状对比                                            │
│    · 本周双方 key_drivers 文字表                                 │
│    · 若 >2 周未更新：橙色 "已 N 周未更新" 警示                   │
└────────────────────────────────────────────────────────────────┘
```

## 3. 操作员每周动作详解

### 3.1 标准 prompt 模板

存放：`stock_analyze/alt_factors/prompts/market_sentiment_v1.md`

```
你是 {agent_id} 的市场情感分析师。

任务：判断 A 股市场在 **{week_start_date} ~ {week_end_date}** 这 7 天的整体情感倾向。

要求：
1. 使用你自带的 web search 工具，搜索本周中国 A 股市场的重要新闻。优先来源：
   - 财联社、新浪财经、同花顺、东方财富
   - 央视新闻、新华社、证券时报
   - 不优先：自媒体、营销号
2. 关注以下维度：
   - 政策面：货币政策、产业政策、监管新规
   - 资金面：北上资金流向、新发基金、IPO 节奏
   - 板块面：本周热点 / 资金流出板块
   - 风险事件：商品价格异动、企业暴雷、地缘政治
   - 海外：美股、美联储、汇率
3. 综合判断本周市场情感，输出严格 JSON 如下（不要任何解释文字）：

{
  "sentiment_score": <-1.0 到 1.0 的小数；-1 = 极度负面（如大幅下跌伴随系统性利空），0 = 中性，1 = 极度正面（如重大利好集中）>,
  "confidence": <0.0 到 1.0；信息充分一致 → 0.8+，信息分歧大 → 0.5 以下>,
  "key_drivers": [<3 个最重要驱动事件，每个 ≤ 15 字>],
  "search_sources_used": [<本次主要参考的 5 个新闻链接 URL>]
}

参考样例：
{
  "sentiment_score": 0.32,
  "confidence": 0.78,
  "key_drivers": ["AI 算力链情绪回暖", "央行 MLF 续作偏鸽", "地产新政预期反复"],
  "search_sources_used": ["https://www.cls.cn/...", "..."]
}
```

### 3.2 操作员的 CLI 命令

```bash
python3 -m stock_analyze record-sentiment \
  --agent claude \
  --week-end 2026-05-25 \
  --score 0.32 \
  --confidence 0.78 \
  --drivers "AI 算力链情绪回暖,央行 MLF 续作偏鸽,地产新政预期反复" \
  --llm-model claude-sonnet-4.5 \
  --sources "https://www.cls.cn/...|https://finance.sina.com.cn/..."

# 成功 → echo "✓ recorded; file now has 12 weeks"
# 重复 → exit 1, "✗ 2026-05-25 already recorded; use --force"
# 范围错误 → exit 1, "✗ score must be in [-1, 1]"
```

### 3.3 失败补救

如果 LLM web search 失败/拒绝回答 → 操作员可以：

1. 自己在网上找 5-10 条本周代表性新闻
2. 粘贴 headlines 给 LLM："基于这些新闻打分"
3. 同样跑 record-sentiment 落盘

CSV 不区分 LLM 来源 — `sources` 字段记录实际参考的 URL，事后可以审计。

## 4. 数据 schema

`data/<agent>/alt_factors/market_sentiment.csv`：

```
week_end_date,sentiment_score,confidence,key_drivers,sources,llm_model,prompt_version,recorded_at
2026-05-04,0.15,0.65,"...","https://...|https://...",claude-sonnet-4.5,v1,2026-05-04T20:15:23
2026-05-11,-0.08,0.72,"...","...",claude-sonnet-4.5,v1,2026-05-11T19:48:01
2026-05-18,0.22,0.80,"...","...",claude-sonnet-4.5,v1,2026-05-18T20:03:55
2026-05-25,0.32,0.78,"AI 算力链情绪回暖,央行 MLF 续作偏鸽,地产新政预期反复","https://www.cls.cn/...",claude-sonnet-4.5,v1,2026-05-25T20:30:00
```

字段说明：
- `week_end_date`：周末日期（A 股周五收盘后）
- `sentiment_score`：[-1.0, 1.0]
- `confidence`：[0.0, 1.0]
- `key_drivers`：`,` 分隔的 3 个短语
- `sources`：`|` 分隔的 URL
- `llm_model`：用了哪个 LLM
- `prompt_version`：prompt 版本号（改 prompt 时升）
- `recorded_at`：操作员落盘时间

## 5. factor_pipeline 集成

### 5.1 "广播因子" 概念

现有 factor 都是 per-stock 矩阵：`factor_value(ts_code, factor_name)`。

新增 broadcast factor：每个 factor_name 对应一个**标量值**，应用时广播到所有 ts_code。

```python
# stock_analyze/factor_pipeline.py

def is_broadcast_factor(factor_name: str) -> bool:
    return factor_name.endswith('_market_sentiment_1w')

def load_broadcast_factor(agent_id: str, factor_name: str, as_of: date) -> float:
    if factor_name == f'{agent_id}_market_sentiment_1w':
        return load_latest_market_sentiment(agent_id, as_of)
    raise ValueError(f"Unknown broadcast factor: {factor_name}")
```

### 5.2 跳过 winsorize / z-score / 行业中性化

广播因子在横截面上是常数 — 这三个步骤对它无意义。直接用原值。

```python
def compute_composite_score(candidates, overlay, as_of, agent_id):
    score = pd.Series(0.0, index=[c.ts_code for c in candidates])

    # 经典因子流水线（已有，不动）
    for factor_name, conf in overlay['factors'].items():
        if is_broadcast_factor(factor_name):
            value = load_broadcast_factor(agent_id, factor_name, as_of)
            # 广播到所有股票
            if conf['direction'] == 'high':
                score += value * conf['weight']
            else:
                score -= value * conf['weight']
        else:
            # 现有 per-stock 处理
            ...

    return score
```

### 5.3 NaN 处理

如果 `load_latest_market_sentiment(agent_id, as_of)` 找不到值（操作员漏跑）：

- 现有缺失因子逻辑：把这个因子的 weight 重分配给其他因子
- 即"等同于该周没有这个因子"

## 6. overlay_guard 扩展

```python
# stock_analyze/overlay_guard.py

CLASSIC_FACTORS = {
    'pe', 'pb', 'roe', 'gross_margin', 'debt_ratio',
    'net_profit_growth', 'momentum_20', 'momentum_60',
    'low_volatility_60', 'dividend_yield'
}

AGENT_FACTOR_PATTERN = re.compile(r'^(claude|codex)_market_sentiment_1w$')

def validate_factor_name(name: str, agent_id: str) -> None:
    if name in CLASSIC_FACTORS:
        return
    m = AGENT_FACTOR_PATTERN.match(name)
    if not m:
        raise OverlayUnknownFactor(name)
    if m.group(1) != agent_id:
        raise OverlayCrossAgentFactor(
            f"agent {agent_id} cannot reference {name} (cross-agent factor)"
        )
```

MVP 只允许一个 alt-factor 名：`<agent>_market_sentiment_1w`。后续 Phase 加因子时扩展正则。

## 7. Dashboard 集成

### 7.1 单 agent 面板：市场情感时间线

`stock_analyze/reporting.py::render_market_sentiment_panel(agent_id)`：

```
┌─ 市场情感（claude）──────────────────────────────────┐
│                                                      │
│ [折线图，过去 26 周]                                  │
│                                                      │
│ 最新（2026-05-25）：+0.32（信心 0.78）                │
│ 4 周均值：+0.18                                       │
│ 8 周均值：+0.05                                       │
│                                                      │
│ 本周关键驱动：                                        │
│   · AI 算力链情绪回暖                                 │
│   · 央行 MLF 续作偏鸽                                 │
│   · 地产新政预期反复                                  │
│                                                      │
│ 参考新闻源（5 个 URL，点击展开）                       │
└─────────────────────────────────────────────────────┘
```

### 7.2 对比 tab 面板：双方 LLM 判断对比

```
┌─ claude vs codex 市场情感（过去 26 周）─────────────┐
│                                                     │
│ [双折线图：claude 蓝 / codex 橙]                     │
│                                                     │
│ 本周（2026-05-25）：                                 │
│   claude  +0.32（"AI 算力链回暖|央行 MLF 偏鸽|..."）  │
│   codex   +0.18（"科技股震荡|地产承压|外资流入"）     │
│   差值    +0.14（小分歧）                            │
│                                                     │
│ 26 周相关性：0.62（中等正相关）                       │
│ 26 周差值标准差：0.21                                │
└─────────────────────────────────────────────────────┘
```

### 7.3 "未更新警示"

如果某 agent 已经 >2 周没有新数据：

```
⚠️ claude 已 3 周未更新市场情感（最近 2026-05-04）
  → 操作员请跑 /weekly-review claude 时记录本周
```

## 8. 双 agent 隔离

| 路径 | claude 可读 | codex 可读 |
|---|---|---|
| `data/claude/alt_factors/market_sentiment.csv` | ✅ | ❌ |
| `data/codex/alt_factors/market_sentiment.csv` | ❌ | ✅ |

CLAUDE.md / AGENTS.md §8 加：

> `data/<other>/alt_factors/*` — ❌ 不可读。对手的 LLM 情感判断属于对手"思考过程"。
> dashboard 上的"双方对比"展示是给操作员看的；agent 自身从 CLI 读 FS 时不应跨边界。

Dashboard 渲染时**可以**聚合两 agent 的数据做对比（同 §7.2）— 但那是给操作员看，不是给 agent。

## 9. CLI 命令清单（MVP）

```bash
# 1. 操作员每周末用
python3 -m stock_analyze record-sentiment \
  --agent <claude|codex> --week-end <YYYY-MM-DD> \
  --score <-1.0..1.0> --confidence <0.0..1.0> \
  --drivers <comma-separated> --llm-model <name> \
  --sources <pipe-separated-urls> [--force]

# 2. 查看历史（任意时刻）
python3 -m stock_analyze sentiment-log --agent <claude|codex> [--last N]

# 3. 重置某周（仅手工修正用）
python3 -m stock_analyze sentiment-log --agent claude --remove --week-end 2026-05-25
```

## 10. 安全 / 复现性

| 项 | 处理 |
|---|---|
| LLM 输出不可重放 | 接受 — 每次 record 都是一次性事件，csv 记录就是 ground truth |
| prompt 升级 | 升 `prompt_version`；旧版本数据保留；新版本数据新一列从那时起 |
| LLM 模型升级 | 同上，记 `llm_model` 字段；不强制重跑历史 |
| 操作员粘错数字 | `sentiment-log --remove` + 重新跑 record-sentiment |
| 操作员忘记跑 | NaN → 缺失因子分摊；dashboard 警示 |

## 11. 演进路线（一等公民）

```
┌────────────────────────────────────────────────────────────────┐
│  Phase 1 — 本 change MVP（now）                                  │
│  · 颗粒度：市场 × 周                                              │
│  · 数据：客户端 LLM + web search                                 │
│  · 历史：不做                                                    │
│  · 回测：不集成                                                  │
│  · 操作员/周：~10 分钟/agent                                     │
│  · 因子：1 个（market_sentiment_1w）                            │
│  · 实质：建通路 + 形成行为习惯，不期待立即产生 alpha             │
└────────────────────────────────────────────────────────────────┘
                            │
              触发条件: 6 个月后看以下信号 ≥ 1 个
              · sentiment 因子 IC > 0.05 持续两个月
              · 操作员每周流程稳定，无遗漏
              · 升级所需 ¥1000/年 可接受
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  Phase 2 — 升级到 "Path 1" 结构（新 change：add-news-data-and-  │
│  volume-factor）                                                │
│                                                                │
│  · 订阅 Tushare 新闻包 ¥1000/年                                 │
│  · 加 prepare-news-data CLI（ECS daily 拉新闻）                 │
│  · 加 Python NER（关键词匹配新闻↔股票）                          │
│  · 新因子 `<agent>_news_volume_1w`（每股每周被新闻提及次数）     │
│  · MVP 的 market_sentiment 改成 Python 从 Tushare 新闻产生        │
│    （LLM 可选，提供更稳定基线）                                  │
│  · 历史回填 2-3 小时/agent（market_sentiment 每周 1 次）         │
│  · 集成进回测 gate                                              │
│  · 新增代码量：~600 行                                          │
└────────────────────────────────────────────────────────────────┘
                            │
              触发条件: 12 个月后看以下信号
              · news_volume 因子有 alpha
              · 操作员愿意每周投入 1-2 小时
              · 团队/资源能负担 per-stock LLM 批量
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  Phase 3 — per-stock LLM sentiment（新 change：add-per-stock-  │
│  llm-sentiment-factor）                                         │
│                                                                │
│  · 颗粒度：股票 × 周（原 Z）                                     │
│  · LLM 跑各自 CLI（Claude Code / Codex CLI）                    │
│  · 历史回填：分多次会话，~500+ 小时/agent（用 CLI 自驱循环可降低 │
│    到 ~100 小时）                                                │
│  · 新因子 `<agent>_news_sentiment_1w`（per-stock）              │
│  · 因子真正进入横截面 z-score（不是广播），影响选股相对排名      │
│  · 新增代码量：~1500 行                                         │
└────────────────────────────────────────────────────────────────┘
                            │
              触发条件: 18+ 个月后再判断
                            ▼
┌────────────────────────────────────────────────────────────────┐
│  Phase 4 — 事件型 / 跨市场 / 其它 alt-data                       │
│                                                                │
│  候选方向（按优先级）：                                          │
│  a. 北上资金流向因子（Tushare moneyflow_hsgt，2000 积分内已有） │
│  b. 龙虎榜机构席位画像                                          │
│  c. 涨跌停 / 涨跌家数比 等市场广度指标                          │
│  d. 事件 dummy（M&A 公告 / 业绩预增 / 限售解禁）                │
│  e. 行业事件分类（"AI 算力"、"医药集采"）                       │
│  f. 跨市场信号（美股隔夜涨跌 → A 股预测）                       │
│                                                                │
│  每个候选作为独立 change 讨论                                    │
└────────────────────────────────────────────────────────────────┘
```

**关键纪律**：

1. **每个 Phase 必须独立 OpenSpec change**，独立 confirm，独立实施
2. **不在前一个 Phase 落地前同时开始下一个**（避免 scope creep）
3. **每个 Phase 跑稳 ≥ 6 个月后再决定是否升级**
4. **6 个月 review 必须基于真实数据**：sentiment 因子在 live 期间是否真的有 alpha（IC、超额收益相关性）

如果 Phase 1 跑半年没看到 alpha 迹象，**可能根本不进 Phase 2**，把整套 alt-factor 系统关掉。这是一个有意识的"先验证后投资"路径。

## 12. 工程边界（YAGNI）

**MVP 范围**：
- ✅ 1 个广播因子 `<agent>_market_sentiment_1w`
- ✅ Python broadcast factor 概念
- ✅ overlay_guard 白名单扩展
- ✅ `record-sentiment` / `sentiment-log` CLI 命令
- ✅ Dashboard 市场情感面板（单 agent 时间线 + 对比 panel）
- ✅ prompt 模板版本化
- ✅ "未更新警示"提示

**MVP 不做**：
- ❌ Python 内 LLM API 调用
- ❌ 任何新闻数据 fetch / 缓存
- ❌ NER / per-stock 关联
- ❌ 历史回填
- ❌ 回测引擎集成
- ❌ 事件型因子 / 社交媒体 / 卫星
- ❌ 自动检测 prompt 升级 / LLM 升级
- ❌ Slash command（操作员直接跑 CLI 命令；无需复杂会话编排）
- ❌ Tushare 新闻包订阅

## 13. 风险与限制

### 13.1 MVP 不产生 alpha 的可能性

**很大**。广播因子对横截面排名无影响。MVP 真正能"影响选股"只有间接路径（操作员/LLM 看着 sentiment 时间线决定要不要月度演化时调权重）。

**这是有意接受的**：MVP 是"建通路 + 习惯"，不是"立刻赚钱"。文档要写明，避免误期待。

如果你希望 sentiment **立刻**影响选股，必须直接跳到 Phase 3（per-stock）— 那是另一个 OpenSpec change，工程量大 10 倍。

### 13.2 web search 质量参差

不同 LLM 客户端的搜索结果可能差异大：
- Claude.ai 用 Anthropic 自己的搜索后端
- ChatGPT 用 Bing
- 搜索引擎随时调整算法

接受这是"差异化维度的一部分"。两个 LLM 对市场的判断本来就该不同，搜索引擎影响是其中一层。

### 13.3 prompt 偏见

我设计的 prompt 倾向"权威财经媒体"，可能错过自媒体先发的信息。这是有意为之 — 保证数据质量。后续 prompt_version 可以调。

### 13.4 操作员流程稳定性

如果操作员某周漏跑，sentiment 是 NaN，因子被分摊掉。短期影响小，长期数据有缺口。

dashboard "已 N 周未更新" 警示是软提醒，没有强制力。

### 13.5 "复现性"vs "成熟量化"标准

成熟量化要求数据可复现（同一时点同一输入 = 同一输出）。MVP 不满足 — 每次 record-sentiment 是一次性事件，无法重跑。

这是 MVP 的明确取舍。Phase 2 接 Tushare 后才能做到复现。如果短期内你对"科研复现性"有刚需，应该跳过 MVP 直接做 Phase 2。

## 14. 不在本设计范围

- 不调任何 LLM API
- 不引入第三方 NLP 库
- 不引入新数据源（不订阅 Tushare 新闻包，不爬虫）
- 不改 daily / weekly 现有流程
- 不改 baseline 锁字段
- 不动新手 dashboard

## 15. 实施顺序

详见 `tasks.md`。粗粒度：

1. broadcast factor 概念 + factor_pipeline 集成（~30 行）
2. `record-sentiment` / `sentiment-log` CLI（~50 行）
3. overlay_guard 白名单扩展（~30 行）
4. Dashboard 面板（~100 行）
5. prompt 模板 + 文档
6. 端到端测试（手动 record → factor_pipeline 读 → dashboard 渲染）
