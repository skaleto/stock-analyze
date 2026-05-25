# Design · add-llm-sentiment-alpha-factor

## 1. 目标与场景

把"新闻情感"作为一个 alpha 因子加入策略，与 PE/ROE 平级，让 LLM 真正参与到选股决策中（区别于现有"LLM 仅做月度演化"）。

**关键设计选择**：

- LLM 分析在**操作员驱动的 CLI 会话**里完成（不调 Python API，§7.0 保持完整）
- 双 agent 各自用自家 LLM：claude 跑用 Claude Code，codex 跑用 Codex CLI（订阅制覆盖 token 成本）
- 颗粒度：每只候选股 × 每周一次 LLM 调用（输入 = 该股过去 7 天新闻聚合，输出 = `{sentiment_score, confidence, key_drivers}`）
- 双向覆盖：forward（每周增量）+ backtest（一次性预热历史）

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│ Layer 1：数据层（确定性，Python）                                  │
│                                                                  │
│  ECS daily 17:25 (扩展现有 prepare-market-data)                   │
│    └─ prepare-news-data --as-of <today>                          │
│        从 Tushare pro.news / pro.major_news 拉                    │
│        写 data/shared/news_cache/<date>/<source>.json             │
│                                                                  │
│  stock_analyze/news/                                             │
│    ├─ fetch.py    — Tushare 抓取 + 多源合并                      │
│    ├─ ner.py      — Python 关键词匹配做新闻↔股票预匹配           │
│    └─ store.py    — 缓存读写 + meta 维护                         │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ Layer 2：LLM 分析层（操作员驱动 CLI 会话）                          │
│                                                                  │
│  Slash command:                                                  │
│    /analyze-historical-news <agent> [--from] [--to]              │
│      → 一次性预热历史窗口                                          │
│    /analyze-current-week-news <agent>                            │
│      → 每周末 review 时跑                                          │
│                                                                  │
│  stock_analyze/news/llm_analyzer.py（分发器，不调 API）            │
│    提供 helper:                                                   │
│      · next_pending_week(agent_id) -> (week_end_date, tickers)   │
│      · get_news_for_stock_week(ts_code, week_end) -> List[News]  │
│      · save_sentiment(agent_id, ts_code, week_end, score, ...)   │
│      · current_progress(agent_id) -> ProgressSummary             │
│                                                                  │
│  LLM 在 CLI 会话内：                                              │
│    while pending:                                                │
│      week, tickers = next_pending_week(agent_id)                 │
│      for ticker in tickers:                                      │
│        news = get_news_for_stock_week(ticker, week)              │
│        result = LLM 自己读 + 分析 + 出 JSON                       │
│        save_sentiment(agent_id, ticker, week, result)            │
│                                                                  │
│  写盘:                                                            │
│    data/<agent>/alt_factors/sentiment/<YYYY-MM>.csv              │
│    data/<agent>/alt_factors/_progress.json                       │
│    data/<agent>/alt_factors/_epoch.json                          │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ Layer 3：因子流水线集成                                            │
│                                                                  │
│  stock_analyze/factor_pipeline.py 扩展：                          │
│    load_agent_alt_factor(agent_id, factor_name, as_of)           │
│    与 shared factors（PE/ROE）合并到 candidate × factor 表         │
│                                                                  │
│  stock_analyze/overlay_guard.py 扩展：                            │
│    AVAILABLE_FACTORS 允许 <agent_id>_* 前缀                       │
│    跨 agent 引用 → raise OverlayCrossAgentFactor                  │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ Layer 4：回测集成（依赖 add-historical-backtest-engine 先落地）    │
│                                                                  │
│  backtest/data_view.py 扩展：                                     │
│    PointInTimeView.agent_alt_factor(agent_id, factor, as_of)     │
│    过滤 week_end_date < as_of（避免未来函数）                      │
│                                                                  │
│  backtest/engine.py 扩展：                                        │
│    run_backtest(..., agent_id) 必须传 agent_id                    │
│    如果 overlay 用了 <agent>_* 但历史不完整 → raise               │
│      BacktestAltFactorMissing                                    │
│                                                                  │
│  backtest/gate.py 扩展：                                          │
│    检查 alt-factor 历史覆盖；不完整时只跑经典因子部分              │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ Layer 5：Dashboard 集成（专业版）                                  │
│                                                                  │
│  · 情感因子时间线（持仓 Top10，过去 26 周 score）                  │
│  · 本周持仓情感分布（高/中/低 各几只）                              │
│  · 因子贡献分解里加 <agent>_news_sentiment                         │
│  · 对比 tab 新增"双方 LLM 对同一新闻判断对比"                       │
└──────────────────────────────────────────────────────────────────┘
```

## 3. 数据层

### 3.1 新闻源

主源：Tushare `pro.news`（2026-05 时点支持的 sources：`新浪财经` / `财联社` / `同花顺` / `央视新闻` / `凤凰新闻`）

**实施前必须验证**：`pro.news` 是否在 2000 积分包内。如不在，备选：

- 升 4000 积分（~¥500/年）
- 用 akshare 现有新闻接口（已废弃但仍能跑一段时间）
- 缩减覆盖（只用 `pro.major_news`，覆盖更窄）

主任务（每日跑）：

```python
# stock_analyze/news/fetch.py
def fetch_news_for_date(as_of: date, sources: List[str], data_root: Path) -> None:
    for src in sources:
        df = tushare_client.news(src=src, start_date=as_of, end_date=as_of)
        out = data_root / "shared/news_cache" / as_of.isoformat() / f"{src}.json"
        out.write_text(df.to_json(orient='records'))
```

幂等：已拉过的日期跳过；可断点续跑。

### 3.2 NER（新闻↔股票预匹配）

**为什么不让 LLM 做 NER**：避免 LLM context 浪费在"识别这条新闻提到哪几只股票"。Python 用基础关键词匹配预过滤后，LLM 只需读"和股票 X 相关的新闻"，效率高。

```python
# stock_analyze/news/ner.py
def match_news_to_stocks(news_text: str, ticker_index: TickerIndex) -> List[str]:
    """返回新闻里出现的 ts_code 列表（按全名 / 简称 / cnspell / 行业关键词匹配）"""
    ...
```

`TickerIndex` 预构建（启动时一次）：
- 全名匹配（`贵州茅台` → `600519.SH`）
- 简称匹配（`茅台` → `600519.SH`，但要避免歧义如 `贵州` 不映射）
- cnspell 匹配（`mt` → `600519.SH`，可选）
- 行业宽匹配（`白酒板块` → 所有白酒股，作为 fallback）

**输出**：每条新闻一个 `mentioned_tickers: List[str]`，落 `data/shared/news_cache/<date>/<source>.json` 的同一份记录。

### 3.3 Point-in-time 约束

- 新闻 `pub_date <= as_of` 才能被该 as_of 时点的分析使用
- LLM 分析 "week_end_date = 2023-06-30" 时，只能看到 `pub_date in [2023-06-24, 2023-06-30]` 的新闻
- 防泄漏的实施：`get_news_for_stock_week(ticker, week_end)` 内部严格按 `pub_date` 过滤

## 4. LLM 分析层

### 4.1 进度跟踪

`data/<agent>/alt_factors/_progress.json`：

```json
{
  "agent_id": "claude",
  "current_epoch": 1,
  "completed_weeks": [
    "2021-W01", "2021-W02", ...
  ],
  "in_progress_week": "2023-W26",
  "in_progress_tickers_done": ["600519.SH", "000001.SZ"],
  "last_updated": "2026-05-26T11:42:00",
  "total_target_weeks": 260,
  "completion_pct": 0.42
}
```

支持中断后从 `in_progress_week` + `in_progress_tickers_done` 续跑。

### 4.2 Epoch 管理

`data/<agent>/alt_factors/_epoch.json`：

```json
{
  "current_epoch": 1,
  "history": [
    {
      "epoch": 1,
      "started": "2026-06-01",
      "llm_model": "claude-sonnet-4.5",
      "prompt_version": "v1.0",
      "notes": "Initial backfill of 2021-2026 history"
    }
  ]
}
```

当 LLM 模型或 prompt 改变时（如 claude-sonnet-4.5 → claude-sonnet-5），人为升 epoch：
- 新 epoch 的产物写到 `sentiment/<YYYY-MM>.csv` 的新行，`analysis_epoch = 2`
- 同一 (ticker, week) 可能有 epoch=1 + epoch=2 两行
- factor_pipeline 默认读最新 epoch；研究 / 复盘时可指定老 epoch

### 4.3 LLM 调用的 prompt（v1.0）

```
你是 {agent_id} 的新闻情感分析师。

任务：判断股票 **{stock_name}（{ts_code}）** 在 **{week_start_date}** 到
**{week_end_date}** 这 7 天的市场情感。

⚠️ 严格基于以下输入的新闻判断，**不能引用任何超出该日期范围**的信息。

输入：以下是该股票最近 7 天的相关新闻（按时间倒序）：

1. [{pub_date_1}] {title_1}
   摘要：{summary_1}
   来源：{source_1}

2. [{pub_date_2}] {title_2}
   摘要：{summary_2}
   来源：{source_2}

...

输出格式（严格 JSON）：

{
  "sentiment_score": <float in [-1.0, 1.0]>,
  "confidence": <float in [0.0, 1.0]>,
  "key_drivers": [<string>, <string>, <string>],
  "news_count": <int>
}

评分指引：
- sentiment_score: -1=极度负面（如重大利空、暴雷），0=中性（无显著情感），1=极度正面（如重大利好、超预期）
- confidence: 0.5 = 信息有限/分歧大，0.9 = 信号强一致
- key_drivers: 2-3 个最重要的情感驱动事件，每个用 < 10 个字概括

只输出 JSON，不要解释、不要多余文字。
```

### 4.4 LLM 调 helper 的机制

LLM 在 CLI 会话内（Claude Code / Codex CLI），不能直接调 Python 函数。所有 `llm_analyzer.py` 的 helper 同时暴露为 **CLI 子命令**，LLM 通过 Bash 工具调用：

```bash
# LLM 在 CLI 会话内可以跑这些命令：

python3 -m stock_analyze news-analyze next-pending --agent claude
# 输出 JSON：{"week_end": "2023-06-30", "tickers": ["600519.SH", ...]} 或 {"done": true}

python3 -m stock_analyze news-analyze get-news --ticker 600519.SH --week-end 2023-06-30
# 输出 JSON：[{"pub_date": ..., "title": ..., "summary": ..., "source": ...}, ...]

python3 -m stock_analyze news-analyze save-sentiment \
  --agent claude --ticker 600519.SH --week-end 2023-06-30 \
  --score 0.42 --confidence 0.75 \
  --drivers "稳健股息预期上调,不良率改善" --news-count 5
# 写盘 + 更新 _progress.json

python3 -m stock_analyze news-analyze progress --agent claude
# 输出 JSON：{completion_pct: 0.42, remaining_weeks: 152, ...}
```

### 4.5 Slash command UX

`/analyze-historical-news claude --from 2021-01 --to 2024-12`（Claude Code）/ 对应 Codex CLI 的等效模板：

slash command body 内引导 LLM：

1. 跑 `progress` 显示当前完成度
2. 循环：
   - 跑 `next-pending` 拿到下一个待分析周；若 `{"done": true}` 则结束
   - 对每只 ticker：
     - 跑 `get-news` 拿新闻 JSON
     - 自己读 + 输出 sentiment JSON
     - 跑 `save-sentiment` 落盘
   - 每完成 10 只股票，跑一遍 `progress` 显示进度
3. 用户输入 `q` 或会话超时自动停（进度已持久化）

`/analyze-current-week-news claude`：

- 同上，但 `--from` `--to` 自动设为本周
- 短会话（~15 分钟）

### 4.6 Codex CLI 等效

Codex CLI 不一定有完全等价的 slash command 机制。备选三种适配方式：

- 如有 slash command：与 claude side 对称
- 如无：用 prompt 模板（操作员粘贴一段长 prompt 启动 codex 自驱）
- 兜底：操作员手动循环（codex 一次分析一周，操作员复制粘贴跑下一周）

最终方案由操作员实施时按 codex 版本能力选。Python helper API（CLI 子命令）对两边 LLM 是对称的。

### 4.5 中断恢复

- LLM 中途停 → `_progress.json` 记录到最后成功 ticker
- 下次启动 → 从该 ticker 之后续跑
- 同一 (ticker, week_end) 第二次被分析 → 默认读缓存（除非 epoch 升）

## 5. 因子集成

### 5.1 `AVAILABLE_FACTORS` 白名单扩展

```python
# stock_analyze/overlay_guard.py

CLASSIC_FACTORS = {
    'pe', 'pb', 'roe', 'gross_margin', 'debt_ratio',
    'net_profit_growth', 'momentum_20', 'momentum_60',
    'low_volatility_60', 'dividend_yield'
}

AGENT_FACTOR_PREFIX = re.compile(r'^(claude|codex)_[a-z_]+$')

def validate_factor_name(name: str, agent_id: str) -> None:
    if name in CLASSIC_FACTORS:
        return  # shared
    m = AGENT_FACTOR_PREFIX.match(name)
    if not m:
        raise OverlayUnknownFactor(name)
    if m.group(1) != agent_id:
        raise OverlayCrossAgentFactor(name, agent_id)
```

claude.yaml 用 `claude_news_sentiment_1w` → 通过
claude.yaml 用 `codex_news_sentiment_1w` → raise（跨 agent 隔离）

### 5.2 因子 naming convention

| 因子名 | 含义 |
|---|---|
| `{agent}_news_sentiment_1w` | 过去 1 周情感 score（默认） |
| `{agent}_news_sentiment_4w` | 过去 4 周情感均值（趋势） |
| `{agent}_news_confidence_1w` | 过去 1 周 LLM confidence 均值 |
| `{agent}_news_volume_1w` | 过去 1 周新闻数量（关注度代理） |

MVP 只实现 `_1w` 和 `_4w` 两个。其它的留给后续 change。

### 5.3 factor_pipeline 集成

```python
# stock_analyze/factor_pipeline.py

def load_agent_alt_factor(agent_id: str, factor_name: str, as_of: date,
                          candidates: List[str]) -> pd.Series:
    """Returns Series indexed by ts_code, values = factor value at as_of"""
    if factor_name.endswith('_1w'):
        df = read_sentiment_csv(agent_id, as_of.year, as_of.month)
        # 取离 as_of 最近的 week_end_date <= as_of
        df = df[df['week_end_date'] <= as_of]
        df = df.sort_values('week_end_date').groupby('ts_code').tail(1)
        return df.set_index('ts_code')['sentiment_score'].reindex(candidates)
    elif factor_name.endswith('_4w'):
        # 过去 4 周均值
        ...
    elif factor_name.endswith('_confidence_1w'):
        ...
```

NaN 处理：现有 factor_pipeline 已经支持 "缺失因子按比例分摊给其他因子"，alt-factor 自然继承。

## 6. 回测集成

依赖 `add-historical-backtest-engine` 先落地。

### 6.1 backtest engine 加 agent_id 参数

```python
def run_backtest(overlay, start, end, universe, data_root, out_dir, *,
                 agent_id: str, in_memory: bool = False) -> BacktestResult:
    """
    agent_id 用于：
      1. 决定能读哪些 <agent>_* 前缀因子
      2. 检查 alt-factor 历史覆盖是否完整
      3. 错误时给出准确的"请先跑 /analyze-historical-news <agent>" 提示
    """
```

### 6.2 alt-factor 历史完整性检查

回测启动时：

```python
def check_alt_factor_coverage(overlay, agent_id, start, end) -> CoverageReport:
    alt_factor_names = [f for f in overlay['factors'] if f.startswith(f'{agent_id}_')]
    if not alt_factor_names:
        return CoverageReport(complete=True)

    required_weeks = generate_week_ends(start, end)
    progress = read_progress(agent_id)
    missing = [w for w in required_weeks if w not in progress['completed_weeks']]

    return CoverageReport(
        complete=(len(missing) == 0),
        missing_weeks=missing,
        missing_pct=len(missing) / len(required_weeks),
    )
```

### 6.3 Gate 准入处理

```python
# stock_analyze/backtest/gate.py

def validate_overlay_via_backtest(overlay, agent_id):
    coverage = check_alt_factor_coverage(overlay, agent_id, VALIDATION_START, VALIDATION_END)

    if not coverage.complete:
        # 软降级：跑回测但忽略 alt-factor
        warn(f"alt-factor 历史覆盖不全（缺 {coverage.missing_pct:.0%}），本次 gate 仅验证经典因子部分。"
             f"建议跑 /analyze-historical-news {agent_id} --from <missing range> 补全。")
        overlay_classic = strip_agent_factors(overlay, agent_id)
        metrics = run_backtest(overlay_classic, ...)
    else:
        metrics = run_backtest(overlay, ...)

    check_floor(metrics)
    return metrics
```

### 6.4 Research CLI 加 agent_id 参数

```bash
python3 -m stock_analyze backtest \
  --agent claude \    # 显式必需
  --start ... --end ... --overlay ... --output ...
```

## 7. 双 Agent 隔离

### 7.1 数据可见性

| 路径 | claude 可读 | codex 可读 |
|---|---|---|
| `data/shared/news_cache/` | ✅ | ✅ |
| `data/claude/alt_factors/sentiment/` | ✅ | ❌ |
| `data/codex/alt_factors/sentiment/` | ❌ | ✅ |
| `data/claude/alt_factors/_progress.json` | ✅ | ❌ |
| `data/codex/alt_factors/_progress.json` | ❌ | ✅ |

CLAUDE.md / AGENTS.md §8 加：

> data/<other>/alt_factors/* —— ❌ 不可读。对手的 LLM 情感判断属于对手的"思考过程"。

### 7.2 overlay 跨 agent 因子拒绝

`overlay_guard.py` 加 `OverlayCrossAgentFactor` 异常，claude.yaml 试图引用 `codex_*` → guard 拒绝。

### 7.3 dashboard 上的对比（合法暴露）

虽然 agent 不能直接读对手的 sentiment.csv，但 dashboard 渲染时**可以**聚合两个 agent 的判断做对比展示给操作员看：

```
本周新闻量 Top 5 股票 LLM 判断对比：

股票          claude 判断    codex 判断    差值
贵州茅台      +0.42          +0.18         +0.24
中际旭创      +0.71          -0.05         +0.76  ← 大分歧
平安银行      -0.31          -0.28         -0.03
...
```

这给操作员"两个 LLM 在哪些股票上观点最分裂"的洞察，但 agent 自己看不到对方的判断。

## 8. Dashboard 集成

### 8.1 单 agent dashboard（专业版）

新面板 **"情感因子动态"**（嵌在因子诊断 section 后）：

```
┌─ 情感因子动态 ─────────────────────────────────────┐
│ 持仓 Top10 的过去 26 周 sentiment_score 时序：     │
│ [折线图，10 条线，X 轴时间，Y 轴 score]              │
│                                                    │
│ 本周持仓情感分布：                                  │
│   高情感（>0.5）  : 12 只                          │
│   中情感（-0.5~+0.5）: 28 只                       │
│   低情感（<-0.5）: 10 只                           │
│                                                    │
│ 本周 LLM 关键判断驱动词云（聚合 key_drivers）：     │
│   "稳健股息" / "三道红线" / "AI 算力" / ...        │
└────────────────────────────────────────────────────┘
```

因子贡献分解（已有）扩展，把 `<agent>_news_sentiment_*` 也纳入归因。

### 8.2 对比 tab 加 "LLM 判断对比" 面板

如 §7.3 所示。

### 8.3 新手 dashboard

**不动**。保持 ≤80KB anti-goal。

## 9. 工程边界（YAGNI）

**MVP 范围**：
- ✅ Tushare news fetch + 多源合并
- ✅ Python 关键词 NER（不做 LLM NER）
- ✅ Slash command 2 个（historical / current）
- ✅ progress / epoch 管理
- ✅ Sentiment csv schema + factor_pipeline 集成
- ✅ overlay_guard `<agent>_*` 白名单扩展 + 跨 agent 拒绝
- ✅ backtest engine + gate 集成（agent_id 参数 + 软降级）
- ✅ Dashboard 单 agent 时间线 + 对比 LLM 判断面板
- ✅ 文档更新

**MVP 不做**（明确列入后续 change）：
- ❌ `_confidence_1w` / `_volume_1w` 因子（先实现 `_1w` `_4w` 两个）
- ❌ LLM NER（让 LLM 识别新闻提到哪些股票）
- ❌ 事件型 dummy 因子（如"过去 7 天有 M&A 公告"）
- ❌ 雪球 / 股吧 / 微博等社交情绪源
- ❌ 实时新闻处理（始终 batch，最快 1 周）
- ❌ LLM 跨 agent 互评
- ❌ 中文以外语言的新闻
- ❌ 卫星 / 信用卡 / 招聘等高成本另类数据

## 10. 风险与限制

### 10.1 LLM 输出非确定性

- temperature=0 + JSON schema 后 ≈ 95% 确定
- 缓存 by `(ts_code, week_end, news_hash, epoch)` → 同输入第二次直接返回缓存值
- 历史预热完成后，回测可重复

### 10.2 Tushare 新闻 API 可用性

实施前**必须 confirm**：

- `pro.news` 是否在 2000 积分包内（Tushare 历年规则有变化）
- 备选 1：升 4000 积分（¥500/年）
- 备选 2：用 `pro.major_news`（只重大新闻，覆盖窄但够用）
- 备选 3：用 akshare 现存新闻接口（已废弃风险）

### 10.3 训练窗口新闻"看穿"未来

- LLM prompt 明确限定时间范围
- `get_news_for_stock_week` 内部按 `pub_date` 严格过滤
- 按时间顺序跑批（旧周先跑）防止 LLM 在 prompt 上下文里被新数据"污染"

### 10.4 双 LLM 判断质量不对等

- claude vs codex 的语言理解能力客观存在差异
- 这是**有意设计** — 用户明确要求"用对应模型"
- 竞赛维度扩展：策略 + LLM 决策 + LLM 语言理解三层
- 文档明示，不视为不公平

### 10.5 历史预热中断 / 不完整

- `_progress.json` 支持断点续跑
- gate 软降级：alt-factor 历史不完整时只验证经典因子
- 不会因为预热未做完就阻塞 forward 流程

### 10.6 操作员长会话疲劳

- LLM 在会话里自驱循环（next_pending → analyze → save → next）
- 操作员只需启动 + 偶尔确认 + 关闭
- 单次建议 ≤ 2 小时（每次能覆盖 ~3-4 周历史）
- 分多次完成（如：每天 1 小时 × 7 天 = 完成预热）

### 10.7 codex CLI 工具能力可能与 Claude Code 不对等

- 提供两侧等效的 Python helper API（`llm_analyzer.py`）
- prompt 模板 LLM-agnostic
- 如果 codex CLI 不能高效循环调 helper，备选方案：让 codex 一次性产出整周分析的 markdown，Python 后处理成 csv

### 10.8 新闻覆盖偏差

- 重大新闻 / 大盘股新闻多
- 小盘股可能 0 新闻 → sentiment 因子 NaN → 自动剔除
- 这其实**符合直觉**：新闻不报道的股票不在情感因子的有效域

## 11. 不在本设计范围

- 不调任何 LLM API（§7.0 不变）
- 不引入第三方 NLP 库（sklearn / transformers / spacy 等）
- 不引入实时新闻流（始终批处理）
- 不改 daily / weekly 现有流程的执行时间
- 不改 baseline 锁字段
- 不动 dual-dashboard 的新手版

## 12. 实施顺序

详见 `tasks.md`。前置依赖：

```
add-historical-backtest-engine （DRAFT）
            ↓ 必须先完成
add-llm-sentiment-alpha-factor （本 change）
```

粗粒度顺序（本 change 内部）：

1. 新闻 fetch + 缓存层（Python）
2. 关键词 NER
3. LLM analyzer helper（不调 API，分发器/调度器）
4. Slash command UX
5. 一次手动跑通"分析 1 周历史"端到端
6. factor_pipeline + overlay_guard 集成
7. 回测 engine + gate 集成
8. Dashboard 集成
9. 文档（含 LLM prompt 版本）
10. 启动历史预热（操作员长跑）
