# tasks · migrate-data-source-to-tushare-pro

## 0. 前置(human operator 动手)

- [ ] 0.1 注册 Tushare Pro 账号:https://tushare.pro/register
- [ ] 0.2 完善个人资料(实名 + 工作单位 + 研究方向),拿 120 积分
- [ ] 0.3 凑到 ≥2000 积分(写知乎文章 / 充值 ¥500 / 邀请新用户)
- [ ] 0.4 拿到 Token,本地 `export TUSHARE_TOKEN=...` 加进 `~/.zshrc`
- [ ] 0.5 ECS 上 `/etc/stock-analyze/secrets.env` 注入 `TUSHARE_TOKEN`,`chmod 600`

## 1. OpenSpec foundation

- [x] 1.1 写 proposal.md、design.md、tasks.md
- [x] 1.2 写 capability spec(`specs/tushare-pro-data-provider/spec.md`)
- [ ] 1.3 `openspec validate migrate-data-source-to-tushare-pro --strict` 通过
- [ ] 1.4 human confirm 之后才进入下面任何 task

## 2. requirements.txt

- [ ] 2.1 删 `akshare>=1.18.62`
- [ ] 2.2 加 `tushare>=1.4.0`
- [ ] 2.3 保留 `baostock>=0.9.1` 作为 fallback
- [ ] 2.4 `pip install -r requirements.txt` 本地验证

## 3. data_provider.py 重构

- [ ] 3.1 加 `class DataProvider(ABC)` 抽象基类,定义 10 个方法
- [ ] 3.2 实现 `class TushareProvider(DataProvider)`:
  - [ ] `__init__(token, cache_dir, offline, as_of)` + token 校验
  - [ ] `spot()`:`pro.daily + pro.daily_basic` merge
  - [ ] `daily(code, start, end)`:`pro.daily`
  - [ ] `fina_indicator(code)`:`pro.fina_indicator`
  - [ ] `stock_basic()`:`pro.stock_basic`
  - [ ] `index_weight(scope, trade_date)`:`pro.index_weight(000300.SH/000905.SH)`
  - [ ] `index_daily(code, as_of)`:`pro.index_daily`
  - [ ] `dividend(code)`:`pro.dividend`
  - [ ] `trade_cal()`:`pro.trade_cal`
  - [ ] 单位换算:`total_mv` 万元 → 亿元,`amount` 千元 → 元
  - [ ] cache-first + CacheMiss 契约一致
- [ ] 3.3 实现 `class BaostockProvider(DataProvider)`,把现有 baostock_* 方法移过去
- [ ] 3.4 实现 `make_provider(token, ...)` 工厂方法
- [ ] 3.5 删除所有 `import akshare` 和 `ak.*` 调用
- [ ] 3.6 删除 `eastmoney_retry`、`fallback_retry`(ak 专用版本)
- [ ] 3.7 删除 `EASTMONEY_COOKIE` 环境变量逻辑

## 4. market_data.py(prepare-market-data)

- [ ] 4.1 `prepare_market_data` 用 `make_provider(os.environ['TUSHARE_TOKEN'], ...)`
- [ ] 4.2 spot 改成一次 HTTP 拿全 A 股(替代逐股下钻)
- [ ] 4.3 fina_indicator 仍逐股拉(800 次 / 4 分钟,限频内)
- [ ] 4.4 加 retry + Tushare 失败时降级 Baostock 的逻辑
- [ ] 4.5 snapshot json 加 `data_source: tushare_pro | baostock` 字段

## 5. CLI

- [ ] 5.1 `cli.py` 加 `--token <env_var_name>` 可选参数(默认读 `TUSHARE_TOKEN`)
- [ ] 5.2 启动时打印 `[provider] tushare_pro online` / `[provider] baostock fallback`
- [ ] 5.3 token 缺失时 fail-fast(`TushareTokenMissing` 异常)

## 6. configs

- [ ] 6.1 `configs/competition.yaml` 加 `data_source.primary: tushare_pro`、`data_source.fallback: baostock`
- [ ] 6.2 标记这两个为锁字段(不允许 agent overlay 覆盖)

## 7. systemd / 部署

- [ ] 7.1 修改 `deploy/systemd/stock-analyze-market-data.service` 加 `EnvironmentFile=/etc/stock-analyze/secrets.env`
- [ ] 7.2 timer 时间从 17:25 调整为 17:15(Tushare Pro daily 数据 ~17:00 已可拿)— 或保守保持 17:25
- [ ] 7.3 ECS deploy 文档加 token 注入步骤

## 8. 删除已废弃

- [ ] 8.1 `git rm scripts/home-backfill.sh`
- [ ] 8.2 `git rm docs/home-backfill-runbook.md`
- [ ] 8.3 `git rm openspec/changes/add-historical-backtest-baseline/`(已被本 change 取代;改 archived 状态)
- [ ] 8.4 README.md 中删除 home-backfill 警告段

## 9. 文档

- [ ] 9.1 新增 `docs/tushare-token-setup.md`:本地 + ECS 注入步骤,token 安全规则
- [ ] 9.2 `docs/system-overview.md` 数据流图替换 push2 → tushare
- [ ] 9.3 `docs/competition-runbook.md` 故障排查段更新
- [ ] 9.4 `CLAUDE.md` 不动(agent 边界没变)
- [ ] 9.5 `AGENTS.md` 不动(同上)

## 10. 测试

- [ ] 10.1 `tests/test_tushare_provider.py`(用 fixture mock pro.daily 等)
- [ ] 10.2 `tests/test_baostock_provider.py`(把现有相关测试集中)
- [ ] 10.3 `tests/test_make_provider.py`(工厂方法)
- [ ] 10.4 删除 / 重命名 `tests/test_data_provider.py` 中 akshare 相关用例
- [ ] 10.5 跑 `python3 -m unittest discover -s tests`,全绿
- [ ] 10.6 `python3 -m pyflakes stock_analyze/*.py tests/*.py` 0 warnings
- [ ] 10.7 `openspec validate --strict` 所有 change 通过

## 11. 端到端验证

- [ ] 11.1 `python3 -m stock_analyze prepare-market-data --as-of 2026-05-22`,< 5 分钟,字段覆盖 ≥ 95%
- [ ] 11.2 `python3 -m stock_analyze --agent claude run-weekly --as-of 2026-05-15`,top 50 信号 + 行业分布合理
- [ ] 11.3 `python3 -m stock_analyze --agent codex run-weekly --as-of 2026-05-15`,同上
- [ ] 11.4 `python3 -m stock_analyze competition-dashboard`,dashboard 无 `尚无 X` 错误
- [ ] 11.5 重跑历史 backtest(基于刚落地的 `add-historical-backtest-baseline`),用 Tushare 跑 2024-2025,看年化超额

## 12. 不在范围

- 不动 baseline / overlay / factor pipeline / portfolio controls / simulator / performance / monthly review
- 不动 dashboard 渲染逻辑
- 不动 CLAUDE.md / AGENTS.md
- 不引入 Tushare Pro 5000+ 积分接口
- 不接券商
