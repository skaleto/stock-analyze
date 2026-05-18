# Stock Analyze

一个面向 A 股的前向模拟交易系统。它只做模拟交易、净值追踪、报告和 dashboard，不接券商接口，不真实下单，也不构成投资建议。

## 第一版目标

- 股票池：沪深300、中证500
- 基准：沪深300指数、中证500指数
- 资金：模拟总资金 100 万，两个账户各 50 万
- 调仓：每周生成信号，下一交易日模拟成交
- 持仓：每个账户选前 10 只，等权
- 目标：扣除成本后，观察是否能跑出年化超额收益
- 报告：CSV/JSON、Markdown 周报、静态 HTML dashboard

## 策略逻辑

因子权重在 [configs/strategy_v1.yaml](configs/strategy_v1.yaml) 中配置：

- 价值 30%：PE、PB
- 质量 30%：ROE、毛利率
- 安全 20%：资产负债率、总市值
- 动量 20%：20 日收益率、60 日收益率

硬过滤：

- 排除 ST
- 排除停牌或取不到价格的股票
- 排除 PE <= 0
- 排除最近 20 日平均成交额过低的股票
- 排除关键财务数据缺失严重的股票

交易成本默认：

- 佣金：0.03%，最低 5 元
- 印花税：卖出 0.05%
- 滑点：买卖各 0.05%
- 买入：100 股整数倍

## 使用方法

更完整的环境准备、运行、部署、故障排查说明见 [docs/forward-simulation-runbook.md](docs/forward-simulation-runbook.md)。模型与工程差距 review 见 [docs/quant-model-gap-review-2026-05-18.md](docs/quant-model-gap-review-2026-05-18.md)。本次系统化整理的 OpenSpec 记录在 [openspec/changes/document-forward-simulation-runbook](openspec/changes/document-forward-simulation-runbook)。

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

可选：如果东方财富接口在当前网络下频繁断开，可以从浏览器开发者工具复制东财请求的 Cookie，并只放到运行环境变量里：

```bash
export EASTMONEY_COOKIE='ct=...; ut=...'
```

不要把 Cookie 写入仓库、配置文件或日志。

初始化模拟账户：

```bash
python3 -m stock_analyze init
```

每日运行：

```bash
python3 -m stock_analyze run-daily
```

每周生成信号和报告：

```bash
python3 -m stock_analyze run-weekly
```

只生成 dashboard：

```bash
python3 -m stock_analyze dashboard
```

本地查看 dashboard：

```bash
python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765/dashboard.html`。

## 运行输出

运行数据默认写到本地目录，不提交进 Git：

- `data/state.json`
- `data/pending_orders.json`
- `data/daily_nav.csv`
- `data/trades.csv`
- `data/positions.csv`
- `data/performance_summary.json`
- `reports/weekly_report.md`
- `reports/dashboard.html`

## 服务器部署

第一版推荐部署到 Linux 服务器的 `/opt/stock-analyze`：

```text
/opt/stock-analyze/
  app/
  data/
  reports/
  logs/
  backups/
  venv/
```

仓库包含 systemd 模板：

- `deploy/systemd/stock-analyze-daily.service`
- `deploy/systemd/stock-analyze-daily.timer`
- `deploy/systemd/stock-analyze-weekly.service`
- `deploy/systemd/stock-analyze-weekly.timer`
- `deploy/systemd/stock-analyze-dashboard.service`

dashboard 服务只监听 `127.0.0.1:8765`。建议通过 SSH 隧道访问：

```bash
ssh -L 8765:127.0.0.1:8765 user@your-server
```

然后打开 `http://127.0.0.1:8765/dashboard.html`。

## 旧版筛选器

仓库仍保留一个单文件筛选器 [quant_value_quality_strategy.py](quant_value_quality_strategy.py)，用于手动生成观察池：

```bash
python3 quant_value_quality_strategy.py
```

## 怎么看结果

不要把最高分当作“马上买”。更合理的用法是：

1. 先看 `warnings`，有高负债、利润负增长、数据缺失的公司要谨慎。
2. 再看业务是否能理解，不懂公司怎么赚钱就先不碰。
3. 对候选股票逐只做 F10、年报、同行对比。
4. 用模拟结果验证模型稳定性，不用短期胜负直接调参。

## 风险边界

- 数据来自公开接口，可能受网络、接口变更、限流影响。
- 财务指标可能有缺失或口径差异，不能只看脚本输出。
- 第一版做前向模拟，不代表未来收益。
- PE、PB、ROE、动量都只是工具，不是买卖指令。
