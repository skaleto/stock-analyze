# tasks · add-llm-sentiment-alpha-factor

## 0. 前置依赖

- [ ] 0.1 `add-historical-backtest-engine` 必须先完成（含 simulator 时钟参数化 / backtest engine / gate）
- [ ] 0.2 确认 Tushare `pro.news` 是否在 2000 积分包内（实施前需 human operator 测一下 token 调用是否成功）
- [ ] 0.3 备选数据源决策：如 `pro.news` 不可用，选 `pro.major_news` 或升 4000 积分

## 1. OpenSpec foundation

- [ ] 1.1 proposal.md / design.md / tasks.md 落
- [ ] 1.2 加 specs/ 子目录，每个新 capability 一份 spec：
  - [ ] specs/news-data-fetch/spec.md
  - [ ] specs/news-stock-ner/spec.md
  - [ ] specs/llm-sentiment-analysis-workflow/spec.md
  - [ ] specs/agent-specific-alt-factor-pipeline/spec.md
  - [ ] specs/sentiment-factor-backtest-integration/spec.md
  - [ ] specs/cross-llm-comparison-dashboard/spec.md
- [ ] 1.3 `openspec validate add-llm-sentiment-alpha-factor --strict` 通过
- [ ] 1.4 human operator confirm

## 2. 新闻数据层

- [ ] 2.1 新文件 `stock_analyze/news/__init__.py`
- [ ] 2.2 新文件 `stock_analyze/news/fetch.py`，exposing:
  - [ ] `fetch_news_for_date(as_of, sources, data_root)`
  - [ ] `prepare_news_data(start, end, force=False)` 入口
- [ ] 2.3 Tushare news 4 个源接入：`新浪财经` / `财联社` / `同花顺` / `央视新闻`（按 2000 积分包确认情况调整）
- [ ] 2.4 CLI 子命令 `prepare-news-data --as-of <date> [--start --end] [--force]`
- [ ] 2.5 写入 `data/shared/news_cache/<date>/<source>.json` + `_meta.json` 维护
- [ ] 2.6 幂等：已拉过的 (date, source) 跳过
- [ ] 2.7 错误处理：单源失败不影响其他源；累积错误写 `_meta.json.errors`
- [ ] 2.8 单元测试：mock Tushare client，验证写盘结构 + 幂等性 + 多源合并

## 3. NER 关键词匹配

- [ ] 3.1 新文件 `stock_analyze/news/ner.py`，exposing:
  - [ ] `TickerIndex.build(stock_basic_df) -> TickerIndex`
  - [ ] `match_news_to_stocks(news_text, ticker_index) -> List[str]`
- [ ] 3.2 全名 / 简称 / cnspell / 行业关键词四级匹配，按优先级
- [ ] 3.3 歧义规则：`贵州` 不映射 `贵州茅台`（歧义词列表）
- [ ] 3.4 启动时构建 TickerIndex 一次（基于最新 stock_basic.csv）
- [ ] 3.5 fetch 时同时跑 NER，把 `mentioned_tickers` 写进 news json 记录
- [ ] 3.6 单元测试：构造典型新闻文本（含简称 / 全名 / 行业宽匹配 / 歧义），验证识别准确率

## 4. ECS pipeline 集成

- [ ] 4.1 扩展 `prepare-market-data` ExecStart 序列，加 `prepare-news-data --as-of <today>` 步骤
- [ ] 4.2 失败 → 不阻塞后续 daily agent（news 不是关键路径）
- [ ] 4.3 deploy/systemd/stock-analyze-market-data.service 更新 ExecStart

## 5. LLM analyzer helper（不调 API）

- [ ] 5.1 新文件 `stock_analyze/news/llm_analyzer.py`，提供 Python 内部 API:
  - [ ] `next_pending_week(agent_id) -> (week_end_date, tickers)` 或 None
  - [ ] `get_news_for_stock_week(ts_code, week_end) -> List[NewsItem]`
  - [ ] `save_sentiment(agent_id, ts_code, week_end, score, confidence, drivers, news_count)`
  - [ ] `current_progress(agent_id) -> ProgressSummary`
  - [ ] `current_epoch(agent_id) -> EpochInfo`
  - [ ] `bump_epoch(agent_id, llm_model, prompt_version, notes)` 升 epoch
- [ ] 5.2 把上述 helper 全部暴露为 CLI 子命令：
  - [ ] `python3 -m stock_analyze news-analyze next-pending --agent <id>`（输出 JSON）
  - [ ] `python3 -m stock_analyze news-analyze get-news --ticker <code> --week-end <date>`（输出 JSON）
  - [ ] `python3 -m stock_analyze news-analyze save-sentiment --agent --ticker --week-end --score --confidence --drivers --news-count`
  - [ ] `python3 -m stock_analyze news-analyze progress --agent <id>`
  - [ ] `python3 -m stock_analyze news-analyze bump-epoch --agent --llm-model --prompt-version --notes`
- [ ] 5.3 `data/<agent>/alt_factors/_progress.json` schema 和读写
- [ ] 5.4 `data/<agent>/alt_factors/_epoch.json` schema 和读写
- [ ] 5.5 `data/<agent>/alt_factors/sentiment/<YYYY-MM>.csv` schema 和追加写
- [ ] 5.6 `(ts_code, week_end, epoch)` 缓存层：已分析的不重复写
- [ ] 5.7 单元测试：模拟 LLM 通过 CLI 调用 helper 的全流程

## 6. Slash command（Claude Code 侧）

- [ ] 6.1 新文件 `.claude/commands/analyze-historical-news.md`，提示 LLM:
  - 读 CLAUDE.md
  - 循环：通过 Bash 跑 `news-analyze next-pending` → 对每只 ticker 跑 `get-news` → 自己分析出 JSON → 跑 `save-sentiment`
  - 每 10 只打印进度
  - 接受 `--from YYYY-MM --to YYYY-MM` 参数限定范围
- [ ] 6.2 新文件 `.claude/commands/analyze-current-week-news.md`，提示 LLM:
  - 自动 `--from` `--to` 设为本周
  - 短会话（~15 分钟）
- [ ] 6.3 prompt 模板放 `stock_analyze/news/prompts/sentiment_v1.0.md`

## 6b. Codex CLI 侧适配（实施时按 codex 版本能力选）

- [ ] 6b.1 调研 codex CLI 是否支持 slash command 机制
- [ ] 6b.2 若支持 → 写对称的 commands 模板
- [ ] 6b.3 若不支持 → 写一份 markdown prompt 模板，操作员粘贴启动
- [ ] 6b.4 文档化操作员每次进入 codex CLI 后该做什么

## 7. factor_pipeline 集成

- [ ] 7.1 `factor_pipeline.py` 加 `load_agent_alt_factor(agent_id, factor_name, as_of, candidates)`
- [ ] 7.2 实现 `_1w` 和 `_4w` 两种聚合
- [ ] 7.3 NaN 处理：复用现有"缺失因子按比例分摊"
- [ ] 7.4 单元测试：构造已知 sentiment.csv，验证 1w / 4w 聚合正确

## 8. overlay_guard 扩展

- [ ] 8.1 `AVAILABLE_FACTORS` 拆成 `CLASSIC_FACTORS`（fixed）+ `AGENT_FACTOR_PREFIX`（regex）
- [ ] 8.2 `validate_factor_name(name, agent_id)` 函数
- [ ] 8.3 新异常 `OverlayCrossAgentFactor`，跨 agent 引用时 raise
- [ ] 8.4 错误消息中文 + 指明具体字段
- [ ] 8.5 单元测试：claude 用 claude_* OK / claude 用 codex_* 拒 / 用未知 factor 拒

## 9. 回测引擎集成

- [ ] 9.1 `backtest/engine.py::run_backtest` 加 `agent_id` 必需参数
- [ ] 9.2 `backtest/data_view.py::PointInTimeView.agent_alt_factor(agent_id, factor, as_of)` 实现
- [ ] 9.3 严格按 `week_end_date < as_of` 过滤（防泄漏）
- [ ] 9.4 新异常 `BacktestAltFactorMissing`，alt-factor 覆盖不全时 raise
- [ ] 9.5 `gate.py::validate_overlay_via_backtest` 加 `check_alt_factor_coverage`
- [ ] 9.6 软降级逻辑：覆盖不全时跑 `strip_agent_factors(overlay, agent_id)` 后的 overlay
- [ ] 9.7 提示信息告诉操作员"先跑 /analyze-historical-news"
- [ ] 9.8 单元测试：完整覆盖 / 部分覆盖 / 完全缺失 三场景

## 10. CLI 集成

- [ ] 10.1 `backtest` 子命令加 `--agent` 必需参数（之前 optional）
- [ ] 10.2 `evolution_writer` 集成时传 `agent_id`
- [ ] 10.3 单元测试：CLI 解析 + 调用链

## 11. Dashboard 集成

- [ ] 11.1 `reporting.py::render_sentiment_factor_panel(agent_id) -> html`：
  - [ ] 持仓 Top10 过去 26 周 sentiment 折线
  - [ ] 本周持仓情感分布
  - [ ] 关键判断驱动词云（按 key_drivers 聚合）
- [ ] 11.2 `reporting.render_factor_contribution_panel` 把 `<agent>_news_sentiment_*` 纳入因子归因
- [ ] 11.3 `dashboard_aggregator.py::render_llm_comparison_panel()`:
  - [ ] 本周新闻量 Top 5 股票
  - [ ] 列：股票 / claude score / codex score / 差值
  - [ ] 差值 > 0.5 高亮（大分歧）
- [ ] 11.4 嵌入到专业版 Claude / Codex / 对比三个 tab
- [ ] 11.5 新手版 dashboard 不动
- [ ] 11.6 单元测试：渲染输出快照对比

## 12. CLAUDE.md / AGENTS.md 更新

- [ ] 12.1 §4 加 `<agent>_*` 前缀因子说明
- [ ] 12.2 §7（forbidden actions）加 "不可读对手 alt_factors/"
- [ ] 12.3 §8（allowed exploration）加 "可读 data/shared/news_cache/"
- [ ] 12.4 §10 加新动作 "每月初跑 /analyze-historical-news 补全历史预热（如需要）/ 每周末跑 /analyze-current-week-news"

## 13. 文档

- [ ] 13.1 新增 `docs/llm-sentiment-factor-flow.md` 完整流程说明
- [ ] 13.2 更新 `docs/system-overview.md`：
  - [ ] §4 数据流加新闻 fetch + LLM 分析路径
  - [ ] §6 因子流水线加 alt-factor 说明
  - [ ] §13 关键产物清单加 alt_factors / news_cache / _progress
- [ ] 13.3 新增 `docs/historical-news-warmup-runbook.md` — 操作员历史预热长跑指南

## 14. 测试

- [ ] 14.1 单元测试覆盖：fetch / ner / llm_analyzer / factor_pipeline_integration / overlay_guard / backtest_integration / gate / reporting
- [ ] 14.2 端到端：模拟 1 周新闻 → NER → 触发模拟 LLM 调用 → save sentiment → factor_pipeline 读 → 进入排序
- [ ] 14.3 端到端：故意构造跨 agent factor 引用 → overlay_guard 拒绝
- [ ] 14.4 端到端：backtest with alt-factor coverage incomplete → gate 软降级
- [ ] 14.5 全部 unittest 通过 + pyflakes 0 + openspec validate --strict 通过

## 15. e2e 验证（手动）

- [ ] 15.1 跑 `prepare-news-data --as-of 2026-05-26` 验证拉新闻成功
- [ ] 15.2 操作员开 Claude Code 跑 `/analyze-current-week-news claude` 验证 ~15 分钟会话能写完 1 周 sentiment
- [ ] 15.3 sentiment.csv 内容 sanity check（情感分布合理、关键驱动文本通顺）
- [ ] 15.4 同样验证 codex 端在 Codex CLI（操作员视实际环境）
- [ ] 15.5 启动历史预热：分多次会话跑完 4+1 年（双方各 ~166K 次），监控 `_progress.json` 完成度
- [ ] 15.6 完成后跑 backtest 含 alt-factor，确认不再触发软降级
- [ ] 15.7 dashboard 上看情感时间线 + 对比 panel 显示正常

## 16. 历史预热（长跑）

- [ ] 16.1 操作员制定预热计划（建议：每天 1-2 小时，连续 5-7 天完成）
- [ ] 16.2 跑 `/analyze-historical-news claude --from 2021-01 --to 2021-06`（第一批，~6 个月）
- [ ] 16.3 ... 重复直至 2026-04 全部完成
- [ ] 16.4 codex 端同步进行
- [ ] 16.5 完成后两边 `_progress.json.completion_pct = 1.0`

## 17. 不在范围

- ❌ `_confidence_1w` / `_volume_1w` 因子（先实现 `_1w` `_4w`）
- ❌ LLM NER（让 LLM 识别股票，留作未来）
- ❌ 事件型 dummy 因子
- ❌ 雪球 / 股吧 / 微博等社交情绪
- ❌ 实时新闻
- ❌ LLM 跨 agent 互评
- ❌ 中文以外语言
- ❌ 卫星 / 信用卡 / 招聘等
- ❌ 在 Python 内调任何 LLM API
- ❌ 不改 baseline 锁字段
- ❌ 不改 daily / weekly 执行时间
