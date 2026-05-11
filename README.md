# A股新手量化价值质量策略

这是一个入门级研究工具，用来把“看起来不错的股票”筛成一个观察池。它不自动下单，也不构成投资建议。

## 策略逻辑

默认股票池是沪深300，适合新手先从流动性和公司质量相对较好的大盘股开始。

默认筛选条件：

- `PE` 在 `0-25` 之间
- `PB` 在 `0-4` 之间
- 总市值不低于 `500` 亿元
- `ROE` 不低于 `12%`
- 资产负债率不高于 `60%`
- 排除名称包含 `ST` 的股票

脚本会继续按以下维度打分：

- 估值：PE、PB
- 质量：ROE、毛利率
- 安全：资产负债率、市值规模
- 成长：净利润增长率
- 风险提示：高估值、高负债、利润负增长、财务数据缺失

## 使用方法

安装依赖后运行：

```bash
python3 quant_value_quality_strategy.py
```

输出会写到 `outputs/`：

- `value_quality_watchlist_*.csv`
- `value_quality_watchlist_*.json`

放宽条件示例：

```bash
python3 quant_value_quality_strategy.py --pe-max 35 --pb-max 6 --roe-min 8
```

换股票池示例：

```bash
python3 quant_value_quality_strategy.py --scope zz500
python3 quant_value_quality_strategy.py --scope custom:600519,000858,000333,600036
```

如果提示 `ModuleNotFoundError: No module named 'akshare'`，说明当前 Python 环境还没有安装依赖。请先运行：

```bash
python3 -m pip install akshare pandas
```

如果当天 `akshare` 接口不可用，也可以使用本地 CSV：

```bash
python3 quant_value_quality_strategy.py --input-csv your_stocks.csv
```

CSV 可以使用中文列名或英文列名：

- 股票代码：`代码` 或 `code`
- 股票名称：`名称` 或 `name`
- 估值：`市盈率`/`pe`，`市净率`/`pb`
- 市值：`market_cap_yi`，单位为亿元
- 财务指标：`roe`、`debt_ratio`、`gross_margin`、`net_profit_growth`

## 怎么看结果

新手不要把最高分当作“马上买”。更合理的用法是：

1. 先看 `warnings`，有高负债、利润负增长、数据缺失的公司要谨慎。
2. 再看业务是否能理解，不懂公司怎么赚钱就先不碰。
3. 对候选股票逐只做 F10、年报、同行对比。
4. 只有在估值、基本面、仓位计划都清楚时，才考虑买入。

## 风险边界

- 数据来自公开接口，可能受网络、接口变更、限流影响。
- 财务指标可能有缺失或口径差异，不能只看脚本输出。
- 这个策略没有做历史回测，不代表未来收益。
- DCF、PE、PB、ROE 都只是工具，不是买卖指令。
