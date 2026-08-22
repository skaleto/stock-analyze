# 研究目录名称与只读标的详情设计

## 目标

让研究目录中的 A 股显示并可按证券名称检索；点击任何目录标的时，在不接触正式账户、订单或交易记录的前提下查看已落盘的 K 线和研究指标。

## 数据边界

`refresh-research-universes` 是唯一允许从 Tushare 读取 A 股名称的入口。它读取上市状态为 `L` 的 `stock_basic(ts_code,name)`，将名称和来源写进 dated/latest 研究目录快照。成分股没有对应非空名称时，刷新失败且不推进 `latest.json`，避免发布半完整目录。

Dashboard 只读取目录快照、已有行情缓存和 `data/research/features/<market>`。新的详情投影不读取任一正式账户的 `positions.csv`、`trades.csv`、订单、策略预测或 agent 配置，也不调用外部数据提供方。

## 详情接口与交互

新增 `/api/dashboard/research-universe-instrument.json?kind=<kind>&code=<code>`，仅接受当前目录快照内的标的。响应包含目录元数据、可用的 K 线、最新价格变化和已有研究指标，并永久标记 `executionEffect: none_research_only`。

`a_share` 映射已有 A 股缓存；`exchange_fund` 映射跨境 ETF 缓存；`otc_fund` 只展示目录元数据，并以可理解的提示说明当前没有可展示的场内 K 线。缓存缺失时以受控 warning 返回空 K 线，绝不回源请求或伪造数值。

研究目录表将代码/名称作为可访问的详情按钮。点击后打开右侧只读抽屉，展示名称、代码、研究范围/基金元数据、最新行情、K 线及指标；Escape、关闭按钮和遮罩均可关闭抽屉。抽屉不显示交易、持仓、策略概率或执行控件。

## 验证

后端回归测试覆盖名称快照、名称检索、缺失名称 fail-closed、详情只读投影与未知标的；HTTP 路由测试覆盖新接口。前端测试覆盖点击 A 股记录请求详情、显示 K 线/指标和无 K 线的受控提示。完整构建与既有相关套件用于回归。
