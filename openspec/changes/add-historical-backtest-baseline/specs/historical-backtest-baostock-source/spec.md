## ADDED Requirements

### Requirement: Baostock historical universe fetch

`stock_analyze.data_provider.AkshareProvider` SHALL 提供 `baostock_history_universe(start_date, end_date, codes)` 方法,接受 `codes: Iterable[str]`(6 位股票代码,如 `["600519", "002594"]`)以及 ISO 日期区间,返回 `pandas.DataFrame`,列至少包含 `code,date,open,high,low,close,volume,amount,turn,pctChg,peTTM,pbMRQ,psTTM,tradestatus,isST`,行数等于 `unique_codes × trading_days_in_range`。

#### Scenario: 800 codes × 13 days returns 10,400 rows
- **GIVEN** `codes` 是 hs300+zz500 union(800 codes)且 `start_date="2026-05-06", end_date="2026-05-22"`
- **WHEN** 调用 `provider.baostock_history_universe(...)`
- **THEN** 返回的 DataFrame `len == 10400`
- **AND** `df["code"].nunique() == 800`
- **AND** `df["date"].nunique() == 13`
- **AND** 没有任何 code 缺日(每个 code 都有 13 行)

#### Scenario: peTTM and pbMRQ are point-in-time per row
- **GIVEN** 拉到的茅台 `code="600519"` 数据
- **WHEN** 检查 5/6 与 5/22 两行
- **THEN** `peTTM` 两个值不同(5/6 ≈ 20.82,5/22 ≈ 19.53)
- **AND** `pbMRQ` 两个值不同(5/6 ≈ 6.36,5/22 ≈ 5.96)

#### Scenario: Trading-day-only output (no holidays)
- **GIVEN** 5/1-5/5 为劳动节
- **WHEN** 拉 5/1-5/22 范围
- **THEN** 5/1, 5/4, 5/5 不出现在结果 `date` 列中
- **AND** 5/6 是结果中第一个日期

### Requirement: Baostock financials snapshot fetch

`AkshareProvider` SHALL 提供 `baostock_financials_snapshot(codes, year, quarter)` 方法,对每个 code 调 `query_profit_data` / `query_balance_data` / `query_growth_data` 并合并,返回每 code 一行,字段至少包含 `code,statDate,pubDate,roeAvg,gpMargin,npMargin,liabilityToAsset,YOYNI,YOYEPSBasic,epsTTM`。

#### Scenario: Maotai 2025Q4 has expected ROE
- **WHEN** `provider.baostock_financials_snapshot(["600519"], 2025, 4)`
- **THEN** 唯一一行的 `roeAvg` 在 [0.30, 0.40] 区间
- **AND** `gpMargin > 0.85`(高毛利消费龙头)

#### Scenario: Bank stocks have known anomaly in liabilityToAsset
- **WHEN** `provider.baostock_financials_snapshot(["000001"], 2025, 4)`(平安银行)
- **THEN** 调用成功,DataFrame 非空
- **AND** 调用方 SHOULD 把 `liabilityToAsset < 0.5` 的银行/保险股标记为 `debt_ratio_suspect=true`,以便回测时 cross-validate 或在 overlay 把 debt_ratio 权重设 0

### Requirement: Baostock dividend snapshot fetch

`AkshareProvider` SHALL 提供 `baostock_dividend_snapshot(codes, year)` 方法,返回每 code 在该日历年的现金分红累计:`code, year, total_cash_per_share, payments_count`,其中 `total_cash_per_share` 已经从"每 10 股 X 元"归一化为"每股 X/10 元"。

#### Scenario: Per-share normalization
- **WHEN** Baostock 返回 `dividCashPsBeforeTax = 30.876`(对应"10 派 308.76 元")
- **THEN** 输出行的 `total_cash_per_share = 3.0876`

### Requirement: Historical cache directory layout

历史数据 SHALL 缓存在以下路径:

- `data/shared/cache/historical/<yyyy-mm-dd>/<bs_code>.csv` —— 一只票一天 (OHLCV + peTTM + pbMRQ + psTTM + turn) 一个 CSV
- `data/shared/cache/financials/<year>Q<quarter>.csv` —— 一份季度财报全体 codes 一份 CSV
- `data/shared/cache/dividends/<year>.csv` —— 一年现金分红全体 codes 一份 CSV

#### Scenario: Cache key separation from current spot
- **GIVEN** 现有 `data/shared/cache/spot_20260522.csv` 是 prepare-market-data 写的当下 spot
- **WHEN** 同时跑 `prepare-historical-data --start 2026-05-22 --end 2026-05-22`
- **THEN** 历史数据落到 `data/shared/cache/historical/2026-05-22/<code>.csv`,**不覆盖** `spot_*.csv`
- **AND** 两类 cache 互不污染,可同时存在

### Requirement: CacheMiss in offline mode

读历史 cache 时 SHALL 遵守 `introduce-shared-market-data-pipeline` 已定义的 `offline=True` 语义:cache 不存在时 raise `CacheMiss`,不打网络。

#### Scenario: offline backtest never opens network sockets
- **GIVEN** `provider = AkshareProvider(cache_dir=..., offline=True)`
- **AND** `data/shared/cache/historical/2026-05-22/sh.600519.csv` 不存在
- **WHEN** 调用 `provider.baostock_history_universe(..., codes=["600519"], ...)`
- **THEN** raise `CacheMiss(method="baostock_history_universe", cache_name="historical/2026-05-22/sh.600519")`

### Requirement: Backtest CLI

`stock_analyze` SHALL 提供两个新 CLI 子命令:

1. `prepare-historical-data --start <ISO> --end <ISO> [--scopes hs300 zz500] [--workers 5]` —— 离线拉取并落 cache
2. `backtest --agent <claude|codex> --start <ISO> --end <ISO>` —— 在历史 cache 上跑 backtest,输出 NAV / trades / performance

#### Scenario: Backtest produces NAV time series matching historical days
- **GIVEN** 历史 cache 已经包含 2023-01-01 ~ 2023-12-31 全部交易日
- **WHEN** 跑 `python3 -m stock_analyze --agent claude backtest --start 2023-01-01 --end 2023-12-31`
- **THEN** 输出 `data/backtest/<run_id>/daily_nav.csv` 行数等于 2023 年交易日数(~242 行)× 账户数(2)= ~484 行
- **AND** `performance_summary.json` 包含 `annualized_return`、`sharpe_ratio`、`max_drawdown`、`information_ratio` 非 null
- **AND** 全过程零网络请求(`provider.offline=True`)
