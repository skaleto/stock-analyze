## Why

`docs/system-overview.md` §16 明确写仓库**不是回测系统**,只做前向模拟 (paper trading)。竞赛 `start_date=2026-05-26`,在那之前没有任何机制可以:

1. 在真实历史数据上跑一遍 `factor_pipeline` + `portfolio_controls`,验证模型行为(权重 / 行业约束 / 持仓 buffer)是否符合预期
2. 出 3-5 年历史样本下的年化 / 超额 / 最大回撤 / Sharpe / IR / 换手率 / 成本占比基准
3. 对比 claude overlay vs codex overlay 在历史样本上的差异化与共同性
4. 对新提案的因子(如 codex 想加入 dividend_yield 权重)做样本外校验

2026-05-22 调查 `home-backfill` 流程时发现两个事实:

- **push2 spot 接口本质是 realtime**,`--as-of <past>` 只是 cache 文件名贴标签,内容仍是当下数据。home-backfill 给"五月每个交易日"贴 13 个不同标签,**spot 内容字节级相同**。所以这条流程对"历史回测"零贡献。
- **Baostock 给的就是真历史**(`query_history_k_data_plus` 含 `peTTM`/`pbMRQ`/`psTTM`/`pcfNcfTTM`/`turn`/`pctChg`),并且 `query_profit_data` / `query_balance_data` / `query_growth_data` / `query_dividend_data` 提供季报粒度的 ROE / 毛利率 / 资产负债率 / 增长率 / 现金分红。本仓库 `data_provider.py` 已经把 Baostock 作为 fallback 链路集成,但只在网络兜底时使用,没有用它做真历史拉取。

实测验证:

- 800 票(hs300 + zz500)5/6 - 5/22 共 13 个交易日的 OHLCV + peTTM + pbMRQ,**1.4 分钟拉完,零失败,1.3MB CSV**。
- 茅台 5/22 peTTM=19.53 / pbMRQ=5.96,与逐日 spot 抓取并交叉验证完全一致 → 真 point-in-time。
- 财务接口对 3 只样本股(茅台 / 比亚迪 / 平安银行)全字段成功,2025 全年 ROE / 毛利率 / 净利率 / 净利同比 / 股息均拉到。

→ "我们没有历史数据"这件事**实际上是假命题**,Baostock 一直都在,只是没被用作真历史源。这个 change 就是把它升级为 primary historical source 并搭一个最简回测引擎。

## What Changes

新增 `add-historical-backtest-baseline` capability,把 Baostock 提升为**真历史数据 primary source**,并搭一个保守的 backtest engine 复用现有 `factor_pipeline` + `portfolio_controls` + `performance` 计算链:

1. `stock_analyze/data_provider.py` 新增 `baostock_history_universe(start, end, codes)` —— 一次拉一批 codes 在一个日期区间的 OHLCV + peTTM + pbMRQ + psTTM + turn,落 `data/shared/cache/historical/<date>/<code>.csv`(per-day per-stock)或 `historical_<start>_<end>.parquet`(批量)。
2. `stock_analyze/data_provider.py` 新增 `baostock_financials_snapshot(codes, year, quarter)` —— 一次拉一批 codes 在某年/季度的 ROE / 毛利率 / 净利同比 / 资产负债率,落 `data/shared/cache/financials_<year>Q<quarter>.csv`。
3. `stock_analyze/data_provider.py` 新增 `baostock_dividend_snapshot(codes, year)` —— 拉年度累计现金分红,后续算 dividend_yield。
4. 新增 `stock_analyze/backtest.py`,接口 `run_backtest(start, end, overlay_path, baseline_path, output_dir)`:
   - 加载 Baostock 历史 cache
   - 对每个 rebalance 日按 `factor_pipeline` 出信号
   - 用 `portfolio_controls` 凑 TopN
   - 用现有 `simulator.py` 的成交逻辑(开盘价 + 滑点 + 佣金 + 印花税)模拟
   - 用 `performance.py` 算年化 / Sharpe / 超额 / IR / 换手 / 成本
   - 输出 `data/backtest/<run_id>/{nav,trades,positions,signals,performance}.csv` + `reports/backtest/<run_id>.md`
5. 新增 CLI 子命令 `backtest`:
   ```bash
   python3 -m stock_analyze --agent claude backtest --start 2023-01-01 --end 2025-12-31
   python3 -m stock_analyze --agent codex  backtest --start 2023-01-01 --end 2025-12-31
   ```
6. 数据获取与回测**完全离线**(读 Baostock cache),不打 push2/eastmoney/任何 realtime 接口。一次拉数据可以反复回测。
7. 不改 baseline / overlay 锁字段;不改 `simulator.py` 成交逻辑;不引入新的成交规则。回测就是"把同一套 paper trading 流程往回放"。

## 边界与非目标

- **不做样本内最优化** —— 这个 change 只搭"跑一遍"的能力,**不调参**。调参是 agent monthly proposal 自己的事。
- **不引入新因子源** —— 沿用现有 `factor_pipeline` 接受的字段集(pe / pb / roe / gross_margin / debt_ratio / net_profit_growth / momentum_20 / momentum_60 / low_volatility_60 / dividend_yield)。`industry` 行业映射这次仍走 akshare 的 `stock_individual_info_em` —— 当前已知有 ~99% 未分类 bug(2026-05-19 audit),需要在另一个 change 修。
- **不接券商** —— 真单始终被禁止。
- **不影响 ECS 生产 pipeline** —— 回测是开发机 / 本地一次性任务,不进 systemd timer。

## 与已有 OpenSpec change 的关系

- `introduce-shared-market-data-pipeline`(已落地):定义了 `data/shared/cache/` 是共享数据目录,prepare-market-data 是当下数据流水线。**本 change 与它正交** —— prepare-market-data 写当下数据(spot),本 change 写历史数据(historical_<date>);两者文件名 prefix 不同,互不覆盖。
- `align-beginner-friendly-multi-factor-foundations`(已落地):定义了 factor pipeline 与 portfolio controls。**本 change 完全复用** —— 回测只是把这条 pipeline 喂历史数据。
- `tighten-audit-findings`(已落地):F1 修了 `validate_overlay` 的 TOCTOU,本 change 在回测中复用同样的 validate 流程。

## Agent 来源声明

本提案由 `claude` agent 在 2026-05-23 投研验证中起草,基于 [home-backfill](../../../scripts/home-backfill.sh) 调查及 Baostock 实测。文件创建动作触及 `openspec/`(`CLAUDE.md §7` 列为禁地),由 human operator 在 session 中显式邀请("全部并发做");以**DRAFT** 状态提交,等待 human operator + 团队 review 后决定是否进入实施。
