# add-llm-sentiment-alpha-factor

把"另类数据"——具体来说是**新闻情感**——引入策略，作为与 PE/ROE 平级的 alpha 因子。

**关键架构选择**（与本 repo 当前约束保持一致）：

- 双 agent 各自用自家 LLM：claude 跑用 Claude Code 会话，codex 跑用 Codex CLI 会话。同样的新闻，两个模型可能打不同的情感分 — 这成为竞赛的合法差异化维度
- §7.0 不动：所有 LLM 分析在**操作员驱动的 CLI 会话**里完成，Python 内部不调任何 LLM API
- 颗粒度 Z：每只候选股每周一次 LLM 调用（输入 = 该股过去 7 天新闻聚合，输出 = `(sentiment_score, confidence, key_drivers)`）
- 双向集成：因子既进 forward（每周 LLM 输出当周分数），也进 backtest（一次性预热历史窗口）

**操作员时间成本**：一次性预热 ~6-12 小时（4+1 年历史，~166K 次 LLM 调用，分多次会话），之后每周 ~15 分钟。

**Token 成本**：0 — 假设操作员有 Claude Code / Codex CLI 订阅。

详见 `proposal.md` / `design.md` / `tasks.md`。

**Status**：DRAFT，待 human operator confirm 后开干。**依赖** `add-historical-backtest-engine` 先落地（因为 alt-factor 要进回测 gate）。
