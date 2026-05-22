# tasks.md · add-historical-backtest-baseline

## 1. OpenSpec foundation

- [ ] 1.1 起草 proposal.md / design.md / tasks.md(本文件)。
- [ ] 1.2 写 capability spec `specs/historical-backtest-baostock-source/spec.md`。
- [ ] 1.3 `openspec validate add-historical-backtest-baseline --strict` 通过。

## 2. Baostock 历史数据 fetch 接口

- [ ] 2.1 `data_provider.py` 新增 `baostock_history_universe(start, end, codes) -> pd.DataFrame`。
- [ ] 2.2 `data_provider.py` 新增 `baostock_financials_snapshot(codes, year, quarter) -> pd.DataFrame`。
- [ ] 2.3 `data_provider.py` 新增 `baostock_dividend_snapshot(codes, year) -> pd.DataFrame`,字段对齐成 `dividend_yield` 计算单位(每 10 股 → 每股)。
- [ ] 2.4 缓存路径:`data/shared/cache/historical/<yyyy-mm-dd>/<code>.csv`、`financials_<y>Q<q>.csv`、`dividends_<y>.csv`。
- [ ] 2.5 添加 `--offline` mode 校验:历史 cache miss 时 raise `CacheMiss`(与 `introduce-shared-market-data-pipeline` 行为一致)。

## 3. CLI

- [ ] 3.1 新增 `prepare-historical-data` 子命令,接受 `--start --end` 参数,落 cache。
- [ ] 3.2 新增 `backtest` 子命令,接受 `--start --end --agent` 参数,跑全流程。

## 4. Backtest 引擎

- [ ] 4.1 新增 `stock_analyze/backtest.py`,`run_backtest(start, end, overlay_path, ...)`。
- [ ] 4.2 复用 `factor_pipeline` / `portfolio_controls` / `simulator` / `performance`。
- [ ] 4.3 输出 `data/backtest/<run_id>/{nav,trades,positions,signals,performance,coverage}.csv`。
- [ ] 4.4 输出 `reports/backtest/<run_id>.md`(中文 markdown,绩效卡片 + 月度战绩 + 关键风险)。

## 5. 与现有 dashboard 集成(可选)

- [ ] 5.1 `reporting.py` 新增"历史回测"面板,链接到最近一次 backtest report。
- [ ] 5.2 `dashboard_aggregator.py` 在对比 tab 增加 claude vs codex 历史回测对比。

## 6. 单元 + 集成测试

- [ ] 6.1 `tests/test_backtest.py`:小规模(5 票 × 30 天)端到端 smoke。
- [ ] 6.2 `tests/test_baostock_history.py`:用 mock 验证字段映射与 cache 落盘。
- [ ] 6.3 至少 8 个 case 覆盖:cache hit / cache miss + offline / 银行股 debt_ratio 异常处理 / 停牌日跳过 / 持仓 buffer 在历史上的行为 / 单行业上限 / max_holding_days / fee 计算。

## 7. 文档

- [ ] 7.1 新增 `docs/historical-backtest-runbook.md`。
- [ ] 7.2 更新 `docs/system-overview.md` §17 roadmap 把这个 change 从 "future" 改为 "in progress"。
- [ ] 7.3 README.md 顶部加一个"历史回测"指引。

## 8. 验收 checklist

- [ ] 8.1 拉 2023-01-01 ~ 2025-12-31 全数据成功,无失败码。
- [ ] 8.2 claude overlay backtest 完整跑通,输出 nav/trades/perf 文件。
- [ ] 8.3 codex overlay 同上。
- [ ] 8.4 两份 backtest 对比报告中:年化 / Sharpe / 最大回撤 / 超额三组数字都不是 null。
- [ ] 8.5 `openspec validate add-historical-backtest-baseline --strict` 通过。
- [ ] 8.6 全部单元测试通过 + py_compile clean + pyflakes 0。

## 9. 不在范围

- 不做 point-in-time 历史成分股(留给后续 `introduce-point-in-time-constituents`)。
- 不做 TuShare 集成(留给后续 `add-tushare-pro-source`)。
- 不做新的因子(沿用现有 10 个)。
- 不做行业映射修复(`fix-industry-mapping` 是独立 change)。
- 不接券商,不下真单,不调 LLM API。
