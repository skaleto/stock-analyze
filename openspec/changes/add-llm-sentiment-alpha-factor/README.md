# add-llm-sentiment-alpha-factor

把市场情感作为一个 alpha 因子加入策略，规模从 MVP 开始，预留清晰升级路径。

**MVP 架构（Path 2，本 change 实施范围）**：

- **单因子**：`<agent>_market_sentiment_1w` — 每周 1 个值，applies to all candidates
- **LLM 在客户端跑**：操作员每周打开 Claude.ai（claude）/ ChatGPT（codex），让 LLM 用自带 web search 分析本周市场情感
- **零 Python 内 LLM API 调用**：§7.0 不动，零 token 成本（订阅覆盖）
- **零新闻 fetch 基础设施**：不依赖 Tushare 新闻包（省 ¥1000/年）
- **每周操作员时间**：~10 分钟/agent
- **历史回填**：跳过 — alt-factor live-only，不进回测 gate

**演进路线**（已写入 `design.md §11` 作为一等公民）：

```
Phase 1 (本 change, MVP)        Path 2: LLM web search + market 颗粒度 + live-only
        ↓ 跑 6 个月后看 alpha
Phase 2 (后续 change)           Path 1: 升 Tushare ¥1000/年 + 加 news_volume 因子 + 历史回填 + 回测集成
        ↓ 验证 per-stock 有 alpha 后
Phase 3 (后续 change)           Per-stock LLM sentiment（颗粒度 Z），双方各自批量 CLI 跑
        ↓
Phase 4 (后续 change)           事件型因子 / 跨市场信号 / 更深 alt-data
```

每个 Phase 都是独立 OpenSpec change。Phase 1 跑稳之后再 confirm Phase 2，避免一次性大投入。

详见 `proposal.md` / `design.md` / `tasks.md`。

**Status**：DRAFT，待 human operator confirm 后开干。**不依赖**任何其它 change（与 `add-historical-backtest-engine` 完全正交）。
