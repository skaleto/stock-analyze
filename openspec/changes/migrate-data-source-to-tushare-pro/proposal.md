## Why

当前 `stock_analyze/data_provider.py` 把 AKShare 作为主源,内部 8 个 fetch 方法依次走:

```
spot         : eastmoney.push2 → sina
history      : eastmoney.push2his → tencent → sina → baostock
valuation    : eastmoney.push2 → baidu
financial    : eastmoney.akshare_abstract → akshare_indicator → baostock
dividend     : akshare.stock_a_indicator_lg(已下线) → 永远 None
industry     : akshare.stock_individual_info_em(99% 未分类 bug)
constituents : csindex → akshare → baostock
benchmark    : eastmoney → tencent → sina
```

2026-05 这一周累积发现 5 个严重问题:

1. **push2.eastmoney.com 已封锁云数据中心 IP 段**(ECS aliyun)+ 鉴定家宽 IP **也会被封**(我们今天打了一轮就被拉黑 5+ 小时)。`home-backfill-runbook.md` 假设的"家宽不会被封"是错的。
2. **push2 spot 是实时接口**,`--as-of <past>` 无效,home-backfill 给历史日期贴标签 = 浪费 13 次配额。
3. `stock_a_indicator_lg` 已下线 → `dividend_yield` 接口永久返回 None;codex.yaml 把 `dividend_yield` 加权 = 立即触发 `factor_coverage_ratio < 0.6` 全军覆没。
4. `stock_individual_info_em` 返回的 `行业` 字段与 `config/sw_industry_level1_mapping.json` schema 不匹配 → claude 5/19 跑出来 99% 未分类 → `max_industry_weight=0.30` 约束完全失效 → codex top 5 全是白酒,违反公允约束意图。
5. AKShare 内部接口经常变化(`stock_value_em` 改成历史接口,`stock_zh_a_indicator_lg` 改名,新版 akshare 没有了某些函数) → 维护负担高。

2026-05-23 实测验证:

- **Baostock**(已在 fallback 链)拉 800 票真 9 个月历史,1.4 + 12 分钟,字段全(peTTM/pbMRQ/ROE/毛利率/负债率/分红/行业),证明"不靠 push2 也能跑 claude 完整模型"。✅
- 但 **Baostock 单股查询**,800 票要循环 ~10 分钟(weekly OK,daily 慢);**总市值字段不直接给**,要反算流通市值。
- **Tushare Pro 2000 积分**(免费实名认证 + 资料完善即可)提供:
  - `pro.daily` 一次拉全 A 股某日 OHLCV(~5400 行)
  - `pro.daily_basic` 一次拉全 A 股某日 pe_ttm + pb + dv_ttm + total_mv + circ_mv + turnover_rate
  - `pro.fina_indicator` 一次拉单股全部财务历史(ROE / 毛利率 / 负债率 / 净利同比)
  - `pro.stock_basic` 全 A 股基础信息(name / industry / list_date)
  - `pro.index_weight` 指数成分股(月度)
  - `pro.index_daily` 指数日线(基准)
  - `pro.dividend` 现金分红明细
  - `pro.trade_cal` 交易日历

→ Tushare Pro **一日一次 HTTP 拿全市场,字段比 Baostock + push2 还全**,且**自家友好 API,无反爬封禁**。

## What Changes

把 Tushare Pro 设为 **primary source**,Baostock 保留为兜底(网络故障 / Tushare 限频时),**完全删除 AKShare 依赖**。

### 高层变化

1. `requirements.txt`:
   - **remove** `akshare>=1.18.62`
   - **add** `tushare>=1.4.0`
   - `baostock>=0.9.1` 保留(降级用)
   - `pandas`、`numpy` 保留

2. `stock_analyze/data_provider.py`:
   - **新增** `class TushareProvider`,所有 8 个 fetch 方法走 `pro.*` 接口
   - **保留** `AkshareProvider` 改名为 `BaostockProvider`,只保留 baostock 那条链路,删 akshare/eastmoney/tencent/sina
   - `make_provider(config)` 工厂方法,默认 Tushare,fallback Baostock
   - `class CacheMiss` 行为不变
   - 所有 cache file 命名不变(向后兼容 ECS 上现有 cache)

3. `configs/competition.yaml`:
   - 新增 `data_source.primary: tushare_pro`,`data_source.fallback: baostock`(非锁字段;agent overlay 不能覆盖)

4. Token 管理:
   - 通过环境变量 `TUSHARE_TOKEN` 读取
   - **绝不**写入仓库 / 配置文件 / 日志
   - systemd unit 通过 `EnvironmentFile=/etc/stock-analyze/secrets.env` 注入
   - 本地开发用 `direnv` / `.envrc`(已 gitignored)

5. 删除已废弃:
   - `scripts/home-backfill.sh`(无 push2 依赖了,不需要)
   - `docs/home-backfill-runbook.md`
   - `openspec/changes/add-home-broadband-data-backfill-workflow/`(如有,标 archived)
   - `data_provider.py` 中所有 `akshare`、`eastmoney_retry`、`fallback_retry` 走 ak 的分支
   - `EASTMONEY_COOKIE` 环境变量支持

6. ECS systemd:
   - `stock-analyze-market-data.timer` 时间可调早一些(Tushare Pro 17:00 已可拿当日数据,比 Baostock 18:30 早 90 分钟)
   - 但保守起见保持 17:25

7. Dashboard / reporting 不变(它们读 cache,不在意来源)

### 已知不在范围

- 不动 baseline(`competition.yaml` 锁字段)
- 不动 agent overlay(`configs/agents/{claude,codex}.yaml`)
- 不动 factor pipeline、portfolio controls、simulator、performance、monthly review
- 不动 dashboard 渲染逻辑
- 不动 CLAUDE.md / AGENTS.md 操作手册
- 不引入 TuShare Pro 的非"2000 积分"接口(如 Level-2 行情、龙虎榜),后续 change 再加

## 字段映射详表(claude.yaml + codex.yaml 的 union)

| 我们字段 | 接口 | Tushare 字段 | 单位 | 备注 |
|---|---|---|---|---|
| `pe` | `daily_basic` | `pe_ttm` | 倍 | 真历史每日 |
| `pb` | `daily_basic` | `pb` | 倍 | 真历史每日 |
| `roe` | `fina_indicator` | `roe` | % | 季度更新 |
| `gross_margin` | `fina_indicator` | `grossprofit_margin` | % | 季度 |
| `debt_ratio` | `fina_indicator` | `debt_to_assets` | % | 季度;银行口径正确 |
| `net_profit_growth` | `fina_indicator` | `netprofit_yoy` | % | 季度 |
| `momentum_20` | `daily.close` 自算 | — | 比 | 21 个收盘价 |
| `momentum_60` | `daily.close` 自算 | — | 比 | 61 个收盘价 |
| `low_volatility_60` | `daily.close.pct_change` 自算 | — | std | 60 日 |
| `dividend_yield` | `daily_basic` | `dv_ttm` | % | 直接给,真历史 |
| `total_mv (market_cap_yi)` | `daily_basic` | `total_mv` ÷ 10000 | 亿元 | 单位万元 → 亿元 |
| `avg_amount_20` | `daily.amount` 自算 | — | 元 | tushare 单位千元,要 × 1000 |
| `industry` | `stock_basic` 或 `index_classify` | `industry` 或 SW 一级 | 文本 | 二选一,前者快后者精 |
| `listing_days` | `stock_basic.list_date` 自算 | — | 天 | |
| `name` (ST 判断) | `stock_basic.name` | `name` | 文本 | `.contains('ST')` |
| hs300 成分 | `index_weight(000300.SH)` | `con_code` | — | 月度 |
| zz500 成分 | `index_weight(000905.SH)` | `con_code` | — | 月度 |
| 000300 close | `index_daily(000300.SH)` | `close` | 点 | |
| 000905 close | `index_daily(000905.SH)` | `close` | 点 | |
| 交易日历 | `trade_cal` | `cal_date` (where `is_open=1`) | — | |

→ **每一项都映射到一个明确的 Tushare 接口字段**,无空洞。

## 验收

- `requirements.txt` 中无 `akshare`
- `stock_analyze/data_provider.py` 中无 `import akshare`
- `pytest` 全部通过(替换 mock 即可)
- `python3 -m stock_analyze prepare-market-data --as-of 2026-05-22` 5 分钟内完成,字段完整
- `python3 -m stock_analyze --agent claude run-weekly` Saturday 跑通,signals 100 票,字段全
- dashboard 渲染无 `尚无 X` 数据错误
- 删除 `scripts/home-backfill.sh` 后 systemd timer 列表里没有 home-backfill 相关单元

## 风险

- **Tushare Pro token 泄漏** → systemd EnvironmentFile + `.envrc` 隔离,绝不 commit
- **2000 积分被超限** → 已加 cache-first + 限频(每分钟 200 次,我们一周一次 ~5400 行远低于上限)
- **首次实施时 Tushare 临时维护** → Baostock fallback 完整保留可顶上
- **历史 cache 命名冲突** → 沿用现有 cache 文件名 schema,Tushare 写入位置与 AKShare 同(`spot_<date>.csv` 等),已有 cache 不破坏

## Agent 来源声明

本 change 触及 `stock_analyze/*.py`、`requirements.txt`、`configs/competition.yaml`、`scripts/home-backfill.sh`、`docs/home-backfill-runbook.md`,均在 `CLAUDE.md §7` 的禁地列表。**由 human operator 在 session 中显式邀请实施**("把整个流程全部重新实现一下"),且 explicit "先落 OpenSpec 文档,我 confirm 才动手"。当前 status = **DRAFT,await user confirmation**。
