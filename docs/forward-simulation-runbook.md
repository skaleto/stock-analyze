# 前向模拟交易运行手册

本文档说明如何从一台新电脑拉取代码、准备运行环境、执行 A 股前向模拟交易系统，以及如何在 Linux/systemd 环境里部署定时任务。系统只做模拟交易：不连接券商、不真实下单、不构成投资建议。

## 系统能做什么

- 每周按配置的 A 股股票池生成选股信号。
- 根据目标持仓生成待执行的模拟订单。
- 在下一交易日用模拟价格、佣金、印花税、滑点执行订单；缺少可见行情、停牌、涨停买入、跌停卖出或 T+1 不可卖时，订单会继续保留在 pending 状态。
- 每日更新账户净值、持仓、交易流水和基准指数。
- 生成中文 Markdown 周报和静态 HTML dashboard。
- 记录数据源健康状态，方便看到接口失败、重试、缓存和降级情况。

## 目录结构

```text
configs/strategy_v1.yaml     策略账户、因子、过滤条件、交易成本和目标
stock_analyze/               Python 包：CLI、数据源、策略、模拟器、报告
deploy/systemd/              Linux systemd service/timer 模板
docs/                        运维和运行文档
openspec/changes/            OpenSpec 变更记录和需求规格
data/                        运行状态目录，除 .gitkeep 外不提交
reports/                     生成报告目录，除 .gitkeep 外不提交
logs/                        运行日志目录，除 .gitkeep 外不提交
backups/                     部署备份目录，除 .gitkeep 外不提交
```

## 基本运行环境

推荐环境：

- Python 3.10 或更高版本。
- Git。
- 能访问公开行情/财务数据接口的网络。
- 如果要自动定时运行，推荐 Linux + systemd。

Python 依赖在 `requirements.txt` 中声明：

- `akshare>=1.18.62`
- `baostock>=0.9.1`
- `pandas>=2.0.0`
- `numpy>=1.24.0`

从新电脑拉取并安装：

```bash
git clone git@github.com:skaleto/stock-analyze.git
cd stock-analyze
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "import akshare, baostock, pandas, numpy; print(akshare.__version__)"
python -m py_compile stock_analyze/*.py
```

如果东方财富实时或历史接口被当前网络/风控断开，可以从浏览器开发者工具里复制东财行情请求的 Cookie，并只通过环境变量传入运行进程：

```bash
export EASTMONEY_COOKIE='ct=...; ut=...'
```

`EASTMONEY_COOKIE` 是敏感会话信息。不要提交到 Git，不要写入 `configs/`，不要打印到日志；服务器部署时应放在 systemd EnvironmentFile 或受权限保护的 shell 环境中。

如果使用 HTTPS 克隆，替换成你自己的 GitHub 克隆地址即可。不要把个人 SSH key 路径、服务器 IP、用户名、token、本机绝对路径提交进仓库。

## 本地运行命令

初始化模拟账户：

```bash
python -m stock_analyze init
```

每周生成信号、刷新净值、生成周报和 dashboard：

```bash
python -m stock_analyze run-weekly
```

每日执行到期模拟订单并刷新净值：

```bash
python -m stock_analyze run-daily
```

只重新生成报告：

```bash
python -m stock_analyze report
python -m stock_analyze dashboard
```

本地启动 dashboard：

```bash
python -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765/dashboard.html
```

如果想把测试数据放到临时目录，不污染默认 `data/` 和 `reports/`：

```bash
python -m stock_analyze \
  --config configs/strategy_v1.yaml \
  --data-dir /tmp/stock-analyze-data \
  --reports-dir /tmp/stock-analyze-reports \
  run-weekly
```

## 运行产物

以下文件是运行时生成的本地状态，默认不提交 Git：

```text
data/state.json                 账户现金和持仓状态
data/pending_orders.json        等待模拟执行的订单，包含 status、attempts、unfilled_reason
data/daily_nav.csv              每日账户净值，按 date + account_id 去重/upsert
data/trades.csv                 模拟交易流水
data/positions.csv              当前模拟持仓
data/latest_signals.csv         最近一次选股结果
data/performance_summary.json   dashboard 绩效摘要
data/data_health.json           数据源尝试、重试、降级、缓存状态
data/cache/*.csv                数据源/股票级缓存
reports/weekly_report.md        中文周报
reports/dashboard.html          静态 dashboard
logs/*.log, logs/*.err          systemd stdout/stderr 日志
```

## 策略配置

默认配置在 `configs/strategy_v1.yaml`：

- 账户：沪深300模拟账户、中证500模拟账户。
- 调仓：每周生成信号，下一交易日模拟成交。
- 因子：PE、PB、ROE、毛利率、资产负债率、市值、20 日动量、60 日动量。
- 成本：佣金、最低佣金、印花税、滑点、100 股整数倍。
- 仓位：`max_single_weight` 会限制单只股票目标权重。
- 严格必需字段：`require_fields`。
- 降级必需字段：`fallback_require_fields`。

策略会先执行严格过滤。如果公开数据缺失导致候选池被筛空，会记录 `hard_filters_empty_relaxed`，再用 `fallback_require_fields` 继续生成可观察的模拟结果。缺失因子不会加分，仍会影响排序。

## 模拟成交规则

周度任务会优先通过 AkShare 交易日历选择信号日之后的下一个 A 股交易日；如果交易日历接口和缓存都不可用，系统会降级为仅跳过周末的工作日近似，并把降级记录进 `data_health.json`。

日度任务执行 pending orders 时采用保守规则：

- 只使用当前运行日 `as_of` 之前已经可见的日 K 行情，不再为了成交去读取未来行情。
- 正常模拟成交不再使用订单参考价兜底；如果运行日尚无可见行情，订单保留 pending。
- 停牌、买入时涨停、卖出时跌停会阻塞模拟成交，并写入 `unfilled_reason`。
- 持仓会记录 `available_shares` 和 `last_buy_date`。当日买入的股票按 T+1 近似处理，当日不可卖。
- 部分成交或无法成交的订单不会静默消失，会保留 `status`、`attempts`、`last_attempt_at`、`unfilled_reason`、剩余 `delta_shares`。
- 同一天重复更新 NAV 时，`daily_nav.csv` 会按 `date + account_id` 保留最新一行，避免重复净值点污染收益和回撤。

## 数据源和降级逻辑

每次运行会把关键数据源状态写入 `data/data_health.json`。

实时行情：

- 优先 AkShare 东方财富实时行情。
- 失败后尝试 AkShare 新浪实时行情。
- 再失败则使用本地 `data/cache/spot_latest.csv`。
- Baostock 不作为全市场实时行情替代源。
- 如设置 `EASTMONEY_COOKIE`，系统会在东方财富请求上附加浏览器式 `User-Agent`、`Referer` 和 Cookie；未设置时仍按无 Cookie 请求并保留降级路径。

指数成分：

- 中证指数成分接口。
- 中证指数权重成分接口。
- AkShare 默认成分接口。
- Baostock 支持的成分接口。
- 本地缓存。

历史日 K：

- 东方财富。
- 腾讯。
- 新浪。
- Baostock。
- 本地缓存。

估值：

- AkShare 百度估值接口。
- Baostock 日 K 中的 `peTTM` / `pbMRQ`。
- 本地缓存。

财务指标：

- AkShare 财务摘要/财务指标。
- Baostock 季度利润表、资产负债表、成长能力数据。
- 本地缓存。

已处理的口径差异：

- 部分历史行情源的成交额是“万元”，部分是“元”。系统会把疑似万元的成交额归一化成元，再用于 `avg_amount_20` 流动性过滤。
- Baostock 的 ROE、毛利率、成长性等比例字段会转换成百分数口径。
- Baostock 的资产负债率优先由 `assetToEquity` 推导，避免直接使用异常量纲字段。

## Linux/systemd 部署

推荐服务器目录：

```text
/opt/stock-analyze/
  app/
  data/
  reports/
  logs/
  backups/
  venv/
```

首次准备：

```bash
sudo mkdir -p /opt/stock-analyze/{app,data,reports,logs,backups}
cd /opt/stock-analyze
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
```

把仓库内容复制到 `/opt/stock-analyze/app` 后安装依赖：

```bash
cd /opt/stock-analyze/app
/opt/stock-analyze/venv/bin/python -m pip install -r requirements.txt
/opt/stock-analyze/venv/bin/python -m py_compile stock_analyze/*.py
```

安装 systemd 模板：

```bash
sudo cp deploy/systemd/stock-analyze-*.service /etc/systemd/system/
sudo cp deploy/systemd/stock-analyze-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-analyze-dashboard.service
sudo systemctl enable --now stock-analyze-daily.timer
sudo systemctl enable --now stock-analyze-weekly.timer
```

手动触发周度任务：

```bash
sudo systemctl start stock-analyze-weekly.service
sudo systemctl status --no-pager --lines=40 stock-analyze-weekly.service
```

查看日志：

```bash
tail -120 /opt/stock-analyze/logs/weekly.log
tail -120 /opt/stock-analyze/logs/weekly.err
tail -120 /opt/stock-analyze/logs/daily.log
tail -120 /opt/stock-analyze/logs/dashboard.err
```

dashboard 默认只监听 `127.0.0.1:8765`。远程服务器建议通过 SSH 隧道访问：

```bash
ssh -L 8765:127.0.0.1:8765 user@your-server
```

然后打开：

```text
http://127.0.0.1:8765/dashboard.html
```

## 验证清单

本地基础验证：

```bash
python -m py_compile stock_analyze/*.py
python -c "import akshare, baostock, pandas, numpy; print(akshare.__version__)"
python -m stock_analyze --data-dir /tmp/stock-analyze-data --reports-dir /tmp/stock-analyze-reports init
python -m stock_analyze --data-dir /tmp/stock-analyze-data --reports-dir /tmp/stock-analyze-reports run-weekly
test -f /tmp/stock-analyze-reports/dashboard.html
test -f /tmp/stock-analyze-data/data_health.json
```

发布前敏感信息扫描：

```bash
git diff --check
rg -n "REPLACE_WITH_LOCAL_USERNAME|REPLACE_WITH_PRIVATE_KEY_NAME|REPLACE_WITH_SERVER_IP|sk-[A-Za-z0-9]" . \
  -g '!data/**' -g '!reports/**' -g '!logs/**' -g '!__pycache__/**'
```

上面的扫描故意比较严格。源码里不应该出现真实本机路径、用户名、密钥名、服务器地址或 token。`user@your-server`、`127.0.0.1`、`/opt/stock-analyze` 这类通用示例可以保留。

## 常见问题

### 东方财富接口失败

本机或服务器都可能遇到东方财富实时/历史接口断连。先看 `data/data_health.json`：如果后续有腾讯、百度、Baostock 或缓存的成功记录，说明降级路径已经接管。

### 周度任务没有候选股票

重点检查：

- `configs/strategy_v1.yaml` 里的 `require_fields`。
- `fallback_require_fields`。
- `min_avg_amount_20`。
- `data/data_health.json` 中是否有大量财务或历史行情缺失。
- `data/cache/history_*.csv` 的成交额单位和行数。

当前代码已处理常见成交额单位差异，并在严格过滤筛空时启用降级必需字段。

### dashboard 显示旧状态

运行一次 `run-daily` 或 `run-weekly`。这两个命令会先持久化数据源健康状态，再生成 dashboard 和周报。

### Baostock 版本显示不一致

不要依赖包内 `__version__`，用 metadata 判断：

```bash
python -c "import importlib.metadata as md; print(md.version('baostock'))"
```

## 如何看模拟结果

- 先看数据源状态和 warnings，再看评分。
- 候选股票需要继续结合 F10、年报、行业对比。
- 因子分和估值只是研究假设，不是买卖指令。
- 至少观察多个周度周期的净值和回撤，再考虑调参。
- 排名模型和仓位/止损/资金期限要分开看。
