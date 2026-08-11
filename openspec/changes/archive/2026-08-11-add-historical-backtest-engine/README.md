# add-historical-backtest-engine

历史回测引擎，服务两个场景：

1. **Gate 前端**：LLM 月度演化时自动跑验证窗口回测，跌穿底线（max DD > 25% / Sharpe < -0.5 / 累计 < -15%）则拒绝 commit
2. **Research 前端**：操作员手动 CLI 跑任意时间窗口 + 任意 overlay 的回测，输出研究报告

底层共享同一个 `backtest.engine.run_backtest()`，复用现有 `simulator.py` 驱动日期循环 — 保证回测与前向模拟走同一份执行/成交/换算代码（输出 schema 完全一致，可在同一 dashboard 上并列比较）。

时间窗口纪律：2021-2024 训练 / 2025-2026-04 验证 / 2026-05+ live OOS。

详见 `proposal.md` / `design.md` / `tasks.md`。

**Status**：DRAFT，待 human operator confirm 后开干。
