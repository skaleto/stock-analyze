# Design · migrate-data-source-to-tushare-pro

## 1. 顶层数据流

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ECS (or local) · systemd timer 17:25                                         │
│                                                                              │
│   prepare-market-data --as-of <today>                                        │
│     ├─ TushareProvider 一次 HTTP 拉全 A 股:                                  │
│     │    daily(trade_date=today)        → 5400 行 OHLCV                      │
│     │    daily_basic(trade_date=today)  → 5400 行 pe_ttm/pb/dv_ttm/total_mv  │
│     │ ✅ 两次 HTTP                                                            │
│     │                                                                        │
│     ├─ TushareProvider 逐股拉财报(2000 积分内,每分钟 200 次)               │
│     │    fina_indicator(ts_code=*, period=latest)                            │
│     │    universe(hs300+zz500) = 800 codes × 1 query = 4 分钟内              │
│     │                                                                        │
│     ├─ TushareProvider 拉股票池                                              │
│     │    index_weight(000300.SH, trade_date=latest_month)                    │
│     │    index_weight(000905.SH, ...)                                        │
│     │                                                                        │
│     ├─ TushareProvider 拉基准                                                │
│     │    index_daily(000300.SH/000905.SH, last 30 days)                      │
│     │                                                                        │
│     └─ 全部写到 data/shared/cache/(命名向后兼容)                            │
│                                                                              │
│   ExecStartPost: run-daily --offline (claude + codex,读 cache,无网)         │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 2. 类继承结构

```python
# stock_analyze/data_provider.py

class CacheMiss(RuntimeError): ...   # 不变

class DataProvider(ABC):              # 新基类
    """Abstract 接口,所有 provider 实现这 10 个方法"""
    @abstractmethod
    def spot(self, as_of: str) -> pd.DataFrame: ...
    def daily(self, code: str, start: str, end: str) -> pd.DataFrame: ...
    def fina_indicator(self, code: str) -> pd.DataFrame: ...
    def index_weight(self, scope: str, trade_date: str) -> pd.DataFrame: ...
    def index_daily(self, code: str, as_of: str) -> pd.DataFrame: ...
    def stock_basic(self) -> pd.DataFrame: ...
    def dividend(self, code: str) -> pd.DataFrame: ...
    def trade_cal(self) -> list[str]: ...

class TushareProvider(DataProvider):
    """Primary source. 2000 积分起,自家友好 API,无反爬。"""
    def __init__(self, token: str, cache_dir: Path, offline: bool = False, as_of: str | None = None): ...
    # cache-first + Tushare network fetch

class BaostockProvider(DataProvider):
    """Fallback when Tushare unreachable / rate-limited."""
    def __init__(self, cache_dir: Path, offline: bool = False): ...
    # 把现有 baostock_* 方法独立成完整 provider

def make_provider(token: str | None, cache_dir: Path, offline: bool = False, as_of: str | None = None) -> DataProvider:
    """工厂方法:有 token 就 Tushare,否则 Baostock。"""
    if token:
        return TushareProvider(token, cache_dir, offline, as_of)
    return BaostockProvider(cache_dir, offline)
```

## 3. cache 文件命名(向后兼容)

| 文件 | Tushare 来源 | Baostock 来源 |
|---|---|---|
| `data/shared/cache/spot_<YYYYMMDD>.csv` | `daily_basic + daily merge` | 800 codes 循环 |
| `data/shared/cache/history_<code>_<end>_<days>.csv` | `daily(ts_code, start_date, end_date)` | `query_history_k_data_plus` |
| `data/shared/cache/financial_<code>_<YYYYMMDD>.csv` | `fina_indicator(ts_code, period)` | `query_profit_data + balance + growth` |
| `data/shared/cache/valuation_<code>_<YYYYMMDD>.csv` | 从 spot 拆出 (已合并到 spot) | 自算 |
| `data/shared/cache/constituents_<index>_<YYYYMMDD>.csv` | `index_weight(index_code, trade_date)` | `query_zz500/hs300_stocks` |
| `data/shared/cache/benchmark_<code>_<YYYYMMDD>.csv` | `index_daily(ts_code, end_date)` | 同 |
| `data/shared/cache/dividend_<code>.csv` | `dividend(ts_code)` | `query_dividend_data` |

→ Cache 文件名 schema 不变,任何 provider 写出来的格式一致,下游 reading code 不变。

## 4. token 注入

```bash
# 本地开发(zsh / bash)
echo 'export TUSHARE_TOKEN=your_32_char_token_here' >> ~/.zshrc
# 或者 direnv
echo 'export TUSHARE_TOKEN=...' > ~/.envrc

# ECS systemd
sudo mkdir -p /etc/stock-analyze
sudo bash -c 'echo "TUSHARE_TOKEN=..." > /etc/stock-analyze/secrets.env'
sudo chmod 600 /etc/stock-analyze/secrets.env
# 然后 .service 文件加
EnvironmentFile=/etc/stock-analyze/secrets.env
```

代码读取:

```python
import os
token = os.environ.get("TUSHARE_TOKEN", "").strip()
if not token:
    raise RuntimeError("TUSHARE_TOKEN not set; see docs/tushare-token-setup.md")
```

**绝不**把 token 写进 commit、log、cache 或任何文件。日志里要打印调用了哪个接口,**不打印 token**。

## 5. 限频策略

Tushare Pro 2000 积分:每分钟 200 次,每天 10w 次,基础接口无总量限制。

我们的日常用量:
- daily: 1 次(trade_date 一次拉全市场)/ 日
- daily_basic: 1 次 / 日
- fina_indicator: 800 次 / 周(每周一次刷财报)
- index_weight: 4 次 / 月(每月一次刷成分)
- index_daily: 2 次 / 日(两个基准 incremental)
- stock_basic: 1 次 / 月
- trade_cal: 1 次 / 月

→ daily 用量 ~10 次,远低于 200/min。weekly fina_indicator 800 次 / 4 分钟 = 200 次/min 恰好触及上限,加 1s sleep 间隔保险。

## 6. cache-first 与 offline mode

完全沿用 `introduce-shared-market-data-pipeline` 的契约:

- cache 命中:直接 return,不打网络
- cache miss + offline=False:走 Tushare → 写 cache → return
- cache miss + offline=True:raise `CacheMiss`

Tushare 临时不可用时:

- 主链 Tushare 抛 ConnectionError / TushareTimeout
- 自动降级到 Baostock(同样 cache-first)
- 仅在 daily/daily_basic 的 spot 类大查询时触发降级;细粒度(单股财务)直接 raise CacheMiss 让下次 prepare-market-data 补

## 7. 删除清单

| 文件 / 段落 | 操作 |
|---|---|
| `stock_analyze/data_provider.py` 中 `import akshare as ak` | 删 |
| 所有 `ak.stock_zh_a_*` 调用 | 删 |
| `eastmoney_retry` / `fallback_retry`(只服务 akshare) | 删 |
| `EASTMONEY_COOKIE` 环境变量逻辑 | 删 |
| `scripts/home-backfill.sh` | 删 |
| `docs/home-backfill-runbook.md` | 删 |
| `requirements.txt` 中 `akshare` 行 | 删,加 `tushare>=1.4.0` |
| `data_provider.py` 现有 baostock fallback 块 | 重构成 `BaostockProvider` 独立类 |

## 8. 添加清单

| 文件 / 模块 | 操作 |
|---|---|
| `stock_analyze/data_provider.py` | 重写为新结构 |
| `docs/tushare-token-setup.md` | 新文档:本地 + ECS 注入步骤 |
| `tests/test_tushare_provider.py` | 单元测试(用 fixture mock pro.daily 等) |
| `tests/test_baostock_provider.py` | 把现有相关测试集中到这 |

## 9. 滚动迁移路径(零中断)

1. **PR 1**:加 `TushareProvider` + `BaostockProvider`,但 default 仍走旧的 `AkshareProvider`。CI 跑通。
2. **PR 2**:`configs/competition.yaml` 加 `data_source.primary`,从 `akshare` 切到 `tushare_pro`。
3. **PR 3**:删 `AkshareProvider`、`akshare` 依赖、home-backfill 脚本和 doc。

不必三步合一,可按上述 PR 顺序推。但提案文档允许"一把推完"也行,代码总量约 600-800 行。

## 10. 测试矩阵

| 用例 | 测试 |
|---|---|
| token 缺失 → 抛出明确错误 | unit test |
| cache miss + offline=True → CacheMiss | unit test(已有契约) |
| cache hit → 不打网络 | unit test |
| daily_basic 返回单位"万元" → 转换为"亿元" | unit test |
| dividend 字段"每股" vs "每 10 股" → 单位一致 | unit test |
| fina_indicator quarter=4 = 全年报 | unit test |
| ST 排除:`name.contains('ST')` | unit test |
| index_weight 月度数据,trade_date=月初 vs 月末 | unit test |
| 24h regression:跑 `prepare-market-data --as-of <昨天>` 全市场,字段覆盖 ≥ 95% | integration test |
