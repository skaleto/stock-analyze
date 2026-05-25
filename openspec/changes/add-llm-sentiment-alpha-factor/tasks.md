# tasks · add-llm-sentiment-alpha-factor

## 0. 前置依赖

- [ ] 0.1 无前置依赖（与 `add-historical-backtest-engine` 完全正交）

## 1. OpenSpec foundation

- [ ] 1.1 proposal.md / design.md / tasks.md / README.md 落
- [ ] 1.2 加 specs/ 子目录，每个新 capability 一份 spec：
  - [ ] specs/weekly-market-sentiment-recording/spec.md
  - [ ] specs/agent-specific-broadcast-alt-factor/spec.md
- [ ] 1.3 `openspec validate add-llm-sentiment-alpha-factor --strict` 通过
- [ ] 1.4 human operator confirm

## 2. 核心模块：alt_factors/sentiment.py

- [ ] 2.1 新文件 `stock_analyze/alt_factors/__init__.py`
- [ ] 2.2 新文件 `stock_analyze/alt_factors/sentiment.py`，提供 Python API:
  - [ ] `record_market_sentiment(agent_id, week_end, score, confidence, drivers, sources, llm_model, prompt_version, force=False) -> None`
  - [ ] `load_latest_market_sentiment(agent_id, as_of) -> float | None`
  - [ ] `load_sentiment_history(agent_id, last_n=None) -> List[SentimentRow]`
  - [ ] `remove_sentiment(agent_id, week_end) -> None`
- [ ] 2.3 CSV schema 验证：
  - [ ] score ∈ [-1.0, 1.0]
  - [ ] confidence ∈ [0.0, 1.0]
  - [ ] week_end 是周五（A 股调仓信号日；非交易日顺延规则同 simulator）
  - [ ] drivers 至少 1 个、不超过 5 个
- [ ] 2.4 防重复：默认拒绝同一 (agent, week_end) 第二次写，除非 `force=True`
- [ ] 2.5 单元测试：8 个 cases（happy / 重复拒绝 / force 通过 / 范围错误 4 个 / 字段缺失 / 无效日期）

## 3. CLI 子命令

- [ ] 3.1 `record-sentiment` 子命令（cli.py）
  - [ ] 参数：`--agent --week-end --score --confidence --drivers --sources --llm-model [--prompt-version v1] [--force]`
  - [ ] 调用 `sentiment.record_market_sentiment(...)`
  - [ ] 输出：成功 echo "✓ recorded; csv now has N weeks"，失败 echo "✗ <原因>"
- [ ] 3.2 `sentiment-log` 子命令
  - [ ] `--agent <id> [--last N]` 显示历史
  - [ ] `--agent <id> --remove --week-end <date>` 删除（带 confirm）
- [ ] 3.3 单元测试：CLI 解析 + dispatch

## 4. factor_pipeline 集成

- [ ] 4.1 `factor_pipeline.py` 加 `is_broadcast_factor(factor_name) -> bool`
- [ ] 4.2 `factor_pipeline.py` 加 `load_broadcast_factor(agent_id, factor_name, as_of) -> float`
- [ ] 4.3 `compute_composite_score(...)` 分流：broadcast factor 直接乘 weight（跳过 winsorize / z-score / 行业中性化）
- [ ] 4.4 broadcast factor 缺失（NaN）→ 复用现有缺失因子分摊逻辑
- [ ] 4.5 单元测试：
  - [ ] broadcast factor 正确广播到所有候选
  - [ ] broadcast factor 跳过预处理
  - [ ] 缺失时按比例分摊
  - [ ] broadcast + per-stock 混合 overlay 正确计算

## 5. overlay_guard 扩展

- [ ] 5.1 `overlay_guard.py` 把 `AVAILABLE_FACTORS` 拆成 `CLASSIC_FACTORS` + `AGENT_FACTOR_PATTERN`
- [ ] 5.2 `validate_factor_name(name, agent_id)` 函数
- [ ] 5.3 新异常 `OverlayCrossAgentFactor`
- [ ] 5.4 错误消息中文 + 指明具体字段
- [ ] 5.5 单元测试：
  - [ ] claude 用 `claude_market_sentiment_1w` ✓
  - [ ] claude 用 `codex_market_sentiment_1w` → raise OverlayCrossAgentFactor
  - [ ] claude 用 `claude_unknown_factor` → raise OverlayUnknownFactor
  - [ ] claude 用 `pe` ✓（classic）

## 6. Prompt 模板

- [ ] 6.1 新文件 `stock_analyze/alt_factors/prompts/market_sentiment_v1.md`
- [ ] 6.2 内容按 design.md §3.1
- [ ] 6.3 README 在文件顶部说明："操作员每周末打开 Claude.ai / ChatGPT，粘贴本模板填充 {agent_id} 和 {week_*_date}"

## 7. Dashboard 集成

- [ ] 7.1 `reporting.py::render_market_sentiment_panel(agent_id) -> html`：
  - [ ] 过去 26 周折线
  - [ ] 最新 + 4 周均值 + 8 周均值数字
  - [ ] 本周 key_drivers 文字
  - [ ] sources URL 链接（可展开）
  - [ ] 已 >2 周未更新时橙色警示
- [ ] 7.2 `dashboard_aggregator.py::render_sentiment_comparison_panel()`：
  - [ ] claude vs codex 双折线
  - [ ] 本周配对数字
  - [ ] 26 周相关性 + 差值标准差
- [ ] 7.3 嵌入到专业版 Claude / Codex / 对比三个 tab
- [ ] 7.4 新手版 dashboard 不动
- [ ] 7.5 单元测试：渲染输出快照对比

## 8. CLAUDE.md / AGENTS.md 更新

- [ ] 8.1 §4 加 `<agent>_market_sentiment_1w` 因子说明（含 broadcast factor 概念 + MVP 阶段不期待立即 alpha）
- [ ] 8.2 §7（forbidden actions）加 "不可读对手 alt_factors/*"
- [ ] 8.3 §8（allowed exploration）保持不变（MVP 阶段无新闻 cache）
- [ ] 8.4 §10 加新动作 "每周末手动跑 record-sentiment 落盘市场情感"
- [ ] 8.5 演进路线写进 §11（新章节，或追加到 §17 路线图）

## 9. 系统文档

- [ ] 9.1 新增 `docs/llm-sentiment-factor-flow.md`：
  - MVP 流程（操作员每周动作 + Python 处理）
  - **演进路线 4 个 Phase**（一等公民章节）
  - 升级触发条件
  - 关键纪律（每 Phase 独立 change / 6 个月 review）
- [ ] 9.2 更新 `docs/system-overview.md`：
  - §4d 加 "每周末市场情感记录" 步骤
  - §6 加 broadcast factor 说明
  - §13 关键产物清单加 alt_factors/market_sentiment.csv
  - §17 路线图加 Phase 2/3/4 references

## 10. 测试

- [ ] 10.1 单元测试覆盖：sentiment.py / cli / factor_pipeline / overlay_guard / reporting
- [ ] 10.2 端到端：
  - [ ] 跑 `record-sentiment` 4 次模拟 4 周历史
  - [ ] 跑 weekly factor_pipeline，验证 sentiment 被乘 weight 加到 score
  - [ ] dashboard 渲染验证显示正常
- [ ] 10.3 全部 unittest 通过 + pyflakes 0 + openspec validate --strict 通过

## 11. e2e 验证（手动）

- [ ] 11.1 操作员打开 Claude.ai，用 prompt 模板，让它对本周 A 股市场 web search + 输出 JSON
- [ ] 11.2 跑 `python3 -m stock_analyze record-sentiment ...` 落盘
- [ ] 11.3 检查 csv 新增 1 行
- [ ] 11.4 跑 ECS run-weekly（带 sentiment factor 的 overlay），verify factor 进入 score 计算
- [ ] 11.5 dashboard 看 sentiment 面板渲染正常
- [ ] 11.6 同样对 codex 做一遍（操作员打开 ChatGPT）

## 12. 不在范围

- ❌ 任何 Python 内 LLM API 调用
- ❌ 新闻 fetch / 缓存 / NER（留 Phase 2）
- ❌ per-stock LLM sentiment（留 Phase 3）
- ❌ 历史回填
- ❌ 回测引擎集成（live-only）
- ❌ 事件型因子 / 社交媒体 / 卫星等（留 Phase 4）
- ❌ Slash command（操作员直接跑 CLI）
- ❌ Tushare 新闻包订阅
- ❌ 不改 baseline 锁字段
- ❌ 不改 daily / weekly 执行时间
- ❌ 不动新手 dashboard
