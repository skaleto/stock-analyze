# LLM 市场情感因子流程（Path 2 MVP）

> 由 OpenSpec change `add-llm-sentiment-alpha-factor` 实施（仍在 active，
> 待操作员完成 5/30 实战首跑）。详细设计见 `proposal.md` / `design.md` /
> `IMPLEMENTATION_REPORT.md`。

把"市场情感"作为 alpha 因子加入策略，**以最小成本**先把通路打通；后续按真实证据决定要不要升级到 Phase 2/3。

---

## 1. MVP 选择的妥协

- **颗粒度**：市场级（每周 1 个数），不是 per-stock
- **数据**：操作员客户端 LLM 自带 web search 即时产出，**不预存新闻**
- **持续性**：仅 live，**不进回测**
- **操作员动作**：每周末 ~10 分钟手动跑

**重要诚实点**：MVP 的广播因子**对横截面排名无影响**（所有股票被同样数值上下平移）。
MVP 的实质是"建数据通路 + 形成行为习惯"，不是"立刻产生 alpha"。

---

## 2. 操作员每周末工作流

### 2.1 跑 prompt

打开 Claude.ai（claude 侧）/ ChatGPT（codex 侧），粘贴：

```
stock_analyze/alt_factors/prompts/market_sentiment_v1.md
```

替换 `{agent_id}` / `{week_start_date}` / `{week_end_date}` 三个占位符。LLM 用自带 web search 拉本周财经新闻 + 输出严格 JSON。

### 2.2 落盘

```bash
python3 -m stock_analyze record-sentiment \
  --agent claude \
  --week-end 2026-05-29 \
  --score 0.32 \
  --confidence 0.78 \
  --drivers "AI 算力链回暖,央行 MLF 偏鸽,地产新政预期反复" \
  --llm-model claude-sonnet-4.5 \
  --sources "https://www.cls.cn/x|https://finance.sina.com.cn/y"
```

CLI 验证 score/confidence 范围、防重复（同 week_end 二次写需 `--force`），写入
`data/<agent>/alt_factors/market_sentiment.csv`（追加 1 行）。

对 codex 同样跑（ChatGPT + `--agent codex`）。

---

## 3. 全链路数据流

```
操作员手动     →  data/<agent>/alt_factors/market_sentiment.csv (每周 1 行)
                       ↓
                strategy.build_signals 自动检测 overlay 含 broadcast factor
                       ↓
                factor_pipeline.load_broadcast_factor 读最近 week_end ≤ as_of 的 score
                       ↓
                process_factors(broadcast_values={...}) — 广播到全部候选股
                       ↓
                每只候选 score 都 += sentiment_score × overlay.weight × direction
                       ↓
                top_n 选股 + build_target_orders 生成 pending
                       ↓
                next-day daily run 执行成交
```

---

## 4. 关键约束

### 4.1 §7.0 不动
所有 LLM 分析在**操作员驱动的客户端**里完成。Python 内部**不调任何 LLM API**。

### 4.2 因子命名 + 跨 agent 隔离
- 命名：`<agent_id>_market_sentiment_1w`（claude / codex）
- claude.yaml 只能引用 `claude_*`，引用 `codex_*` 会被 `overlay_guard` 拒绝
- agent **不能读对手的 sentiment.csv**；dashboard 是"操作员视图"可聚合展示

### 4.3 没记录就 NaN
某周操作员漏跑 → 因子值 NaN → factor_pipeline 现有"缺失因子重新分摊"逻辑接管，不报错。
Dashboard 上"已 N 周未更新"警示提醒补录。

---

## 5. Dashboard 入口

专业版 dashboard 上有两个 panel：
- `reporting.render_market_sentiment_panel(agent)` — 单 agent 26 周时序
- `dashboard_aggregator.render_sentiment_comparison_panel()` — claude vs codex 对比

新手版 dashboard **不显示**情感因子（按 ≤80KB 限制）。

---

## 6. 演进路线

| Phase | 颗粒度 | 数据源 | 操作员时间 | 回测可行 |
|---|---|---|---|---|
| **Phase 1（当前 MVP）** | 市场级（每周 1 个数） | 客户端 LLM + web search | ~10 min/周 | 否（live-only）|
| Phase 2 | 加 news_volume per-stock 因子 | 升 Tushare 新闻包 ¥1000/年 | +5 min/周 | 是（一次性 2-3h 回填） |
| Phase 3 | per-stock LLM sentiment（颗粒度 Z） | 同上 + 各自 LLM CLI | ~50-100h 一次性 + 20h/年 | 是 |
| Phase 4 | 事件型 / 跨市场 / alt-data 其它 | TBD | TBD | TBD |

每个 Phase 独立 OpenSpec change，不在前一 Phase 跑稳 ≥6 个月前启动下一个。
