# Design · add-historical-backtest-baseline

## 1. 数据源选型

| 源 | 价值 | 缺陷 |
|---|---|---|
| **Baostock**(选) | 免费,匿名,已集成在 `data_provider.py` 作为 fallback;真 point-in-time;peTTM/pbMRQ 自带;1.4 分钟 800 票 13 天 | 财务字段需要拼接 5 个接口;`liabilityToAsset` 对部分公司(尤其银行)数据可疑,需校验 |
| TuShare Pro | 一日一截面 (`daily_basic(trade_date)`),21 次拉全月最快;字段最全(含 turnover、market_cap、dv_ratio) | 需注册 + 实名 + 2000 积分;新依赖 |
| AkShare `stock_value_em` | 已在 akshare,真历史 | 单股查询,5400 次循环;走东财,与 push2 同生态,反爬风险高 |

→ Baostock 是当前**集成成本最低 + 反爬零风险**的选项。TuShare 留作"future enhancement"。

## 2. 历史数据 cache 布局

```
data/shared/cache/
  historical/
    <yyyy-mm-dd>/                # one folder per trading day
      <sh.600519>.csv            # one CSV per stock, OHLCV + peTTM/pbMRQ/psTTM/turn
      ...
  financials/
    <year>Q<quarter>.csv         # one CSV per fiscal quarter, all codes
  dividends/
    <year>.csv                   # one CSV per calendar year, all codes
```

或者(性能更优):

```
data/shared/cache/historical/
  <start>_<end>.parquet          # all codes × all dates in one parquet
```

后续决定:per-day per-stock 利于增量更新,parquet 利于读取性能。**先做 per-day per-stock**(与现有 cache 风格一致),后续如需性能优化可加 parquet snapshot 层。

## 3. Baostock 接口字段映射

| 仓库内部字段 | Baostock 接口 + 字段 | 备注 |
|---|---|---|
| `open/close/high/low/volume/amount` | `query_history_k_data_plus`,fields 同名 | adjustflag=2 (前复权) |
| `pe` (PE_TTM) | `query_history_k_data_plus.peTTM` | 真历史每日 |
| `pb` (PB_MRQ) | `query_history_k_data_plus.pbMRQ` | 真历史每日 |
| `turnover` | `query_history_k_data_plus.turn` | % |
| `momentum_20 / momentum_60` | 从 `close` 自算 | 已有逻辑 |
| `low_volatility_60` | 从 `close.pct_change()` 自算 | 已有逻辑 |
| `roe` | `query_profit_data.roeAvg` | 季度粒度 |
| `gross_margin` | `query_profit_data.gpMargin` | 季度粒度 |
| `debt_ratio` | `query_balance_data.liabilityToAsset` | ⚠️ 银行/保险口径异常,需 cross-validate |
| `net_profit_growth` | `query_growth_data.YOYNI` | 季度粒度 |
| `dividend_yield` | `query_dividend_data.dividCashPsBeforeTax 累计` ÷ `close` | 自算;注意值是每 10 股 |
| `market_cap_yi` / `industry` | **Baostock 没有,需另接 akshare 或 csindex** | industry 已知 99% 未分类 bug |

## 4. Backtest 引擎流程

```
run_backtest(start='2023-01-01', end='2025-12-31', overlay=claude.yaml):
  1. baseline = load configs/competition.yaml
  2. validate_overlay(overlay)  # 复用 tighten-audit-findings 的 F1
  3. universe_codes = union(hs300_constituents, zz500_constituents)
       └ ⚠️ 这里用了当下成分股,有幸存者偏差(docs/system-overview.md §16 已 acknowledge)
         future enhancement: introduce-point-in-time-constituents
  4. trading_days = baostock_trading_calendar(start, end)
  5. for signal_day in 每周最后一个交易日:
       a. signals = factor_pipeline(snapshot at signal_day)
       b. targets = portfolio_controls(signals, top_n=50)
       c. orders  = diff(current_positions, targets)
       d. next_open = trading_days[signal_day_idx + 1]
       e. simulator.execute_orders(orders, prices=next_open_prices)
       f. simulator.update_nav(close_prices_each_day until next_signal_day)
  6. perf = compute_account_performance(nav_series, benchmark_series)
  7. write data/backtest/<run_id>/{nav,trades,positions,signals,performance}.csv
  8. write reports/backtest/<run_id>.md
```

## 5. 与现有 simulator 复用

- `execute_orders` / `update_nav` / `build_target_orders` 全部直接复用
- 只把"今天"换成"模拟日历的某一天",数据从 historical cache 读
- 不重写交易成本逻辑

## 6. 验收

- 拉 2023-01-01 ~ 2025-12-31 共 3 年 hs300 + zz500 数据(Baostock):预估 ~10-20 分钟
- claude overlay 跑一遍:输出年化超额 / 最大回撤 / Sharpe / IR
- codex overlay 跑一遍:同上
- 对比报告:claude vs codex 月度对比、风格漂移、相关性

## 7. 风险

- **幸存者偏差**:用当下成分股回溯,会高估收益。后续应做 `point-in-time-constituents`。
- **行业映射 0% 覆盖**(已知 bug):`max_industry_weight` 在回测中也失效,与现状一致。回测仍可跑,只是少了行业约束信号。
- **Baostock 数据质量**:`liabilityToAsset` 对部分公司(尤其银行)异常。回测时可加 cross-validate 或在 overlay 里把 debt_ratio 权重设 0。
- **延展性**:如果以后加 TuShare,本 change 的 cache 布局应该不必动。
