# 三市场运行 Runbook(A股 ECS / 港美股本机)

> A股/港股/美股、claude+codex 双 agent 的「该跑什么、在哪跑、怎么查、出问题怎么办」。
> 数据源连通性结论见 [verify-hk-us-data-sources.md](verify-hk-us-data-sources.md)。
>
> 临时授权覆盖 CLAUDE.md §7.0(docs 改写禁令),把本 runbook 纳入版本控制
> (operator 2026-05-30 口头授权;与既有 `competition-runbook.md` /
> `forward-simulation-runbook.md` 同属 docs runbook)。

---

## 0. 一句话全景

- **A股**:跑在 **ECS**(阿里云 `ai-baby-aliyun` / 120.55.188.242),systemd 全自动,数据源 Tushare。
- **港股 / 美股**:跑在 **operator 本机 Mac**,launchd 定时,数据源 yfinance,**必须经香港住宅代理**(ECS 机房 IP 被 Yahoo 429,大陆直连被 geo-block;只有这台开香港住宅代理的 Mac 能拉)。
- 两边都向 operator 推**飞书**:A股 = ECS 的 `notify-daily-summary`(应用 DM);港美股 = 本机 `overseas_summary.py`(应用 DM 交互卡片)。

## 1. 调度全景(北京时间 CST)

| 市场 | 任务 | 时间(CST) | 在哪 | 触发器 |
|---|---|---|---|---|
| A股 | market-data + 两 agent daily | 周一~五 **18:30** | ECS | `market-data.timer`(10:30 UTC)→ 链起 claude/codex-daily |
| A股 | 两 agent weekly | 周六 **10:00** | ECS | `weekly-trigger.timer`(02:00 UTC) |
| A股 | 月度演化 review | 每月 1 号 **09:00** | ECS | `monthly-review.timer`(01:00 UTC) |
| 港股 | 两 agent daily | 周一~五 **16:30** | 本机 | launchd `overseas-hk-daily` |
| 美股 | 两 agent daily | 周二~六 **08:00** | 本机 | launchd `overseas-us-daily`(跑前一夜美股) |
| 港+美 | 两 agent weekly | 周六 **09:00** | 本机 | launchd `overseas-weekly` |

> A股周末休市 → 周六/日无 A股 daily(只有周六 10:00 weekly)。港美股 daily 在非交易日是 no-op(无新 bar,trades=0),无害。

### 1b. 下单 / 成交时点(三市场)

三市场都是**「周五出信号 → 下周一执行」**(weekly 生成的订单 `trade_date` 都是下周一)。差异在结算与「实际由哪次 daily 跑成交」:

| 市场 | 出信号 | 模拟下单(执行)日 | 结算 | 实际成交于 |
|---|---|---|---|---|
| A股 | 周五收盘 (`last_trading_day_of_week`) | **周一**开盘 (`next_trading_day_open`) | T+1 | ECS 周一 18:30 daily |
| 港股 | 周五 (`signal_day: friday`) | **周一** | **T+2** (`settlement_days:2`) | 本机周一 16:30 daily |
| 美股 | 周五 (`signal_day: friday`) | **周一**(美东) | **T+1** (`settlement_days:1`) | 本机**周二 08:00** daily |

- 「每周一下单」对三市场都成立。
- **美股唯一差异**:美东周一盘北京周二凌晨才收盘、yfinance 才定稿 → 美股周一单实际在本机**周二早上 08:00** 那次 US daily 跑里按美东周一价成交(故 US daily 排周二~六、HK daily 排周一~五)。
- `signal_day`/`execution`/`settlement` 是 `configs/competition_*.yaml` 锁定字段(§3),只读不改。

---

## 2. ECS Runbook(A股)

**连接 / 路径**
- SSH:`ssh ai-baby-aliyun`(密钥 `~/.ssh/ai_baby_aliyun`)。
- app `/opt/stock-analyze/app` · venv `/opt/stock-analyze/venv` · logs `/opt/stock-analyze/logs`
- A股数据 `app/data/a_share/<agent>/` · 共享缓存 `data/shared/cache/` · 密钥 `/etc/stock-analyze/secrets.env`(`TUSHARE_TOKEN`、`SA_LARK_*`、`SA_LARK_WEBHOOK`)

**自动化链路(systemd)**
- **日更**:`market-data.timer`(周一~五 18:30)→ `prepare-market-data`(Tushare 拉全市场写 `data/shared/cache/`)→ ExecStartPost 启 `claude-daily` + `codex-daily`(`run-daily --offline` 读缓存)→ 各 `OnSuccess` → `aggregate-dashboard` → `ExecStartPost notify-daily-summary.sh`(飞书 DM)。
- **周更**:`weekly-trigger.timer`(周六 10:00)→ 启 `claude-weekly` + `codex-weekly`(`run-weekly --offline`,用周五缓存)→ aggregate-dashboard → 飞书。
- **月度**:`monthly-review.timer`(1 号 09:00)→ `competition-monthly-review` + `competition-dashboard`。
- **失败告警**:每个 service `OnFailure=` → `pipeline-failure@.service` → `notify-pipeline-failure.sh` → 追加 `logs/PIPELINE_FAILURES.log` + 飞书 webhook。
- 注:`daily.timer` / `weekly.timer`(老单 agent 版)未启用;竞赛走上面两条链。

**排查命令(ECS 上)**
```bash
systemctl list-timers "stock-analyze*" --no-pager        # 下次/上次触发
systemctl status stock-analyze-claude-daily.service       # 某服务状态
journalctl -u "stock-analyze-*" --since today --no-pager  # 今天日志
tail /opt/stock-analyze/logs/PIPELINE_FAILURES.log        # 失败记录
tail /opt/stock-analyze/app/data/a_share/claude/runs.csv  # 运行台账
systemctl start stock-analyze-market-data.service         # 手动补跑日更链
```
**健康判据**:`runs.csv` 末行 status=success;`daily_nav.csv` 最新日期=最近交易日(周末为周五);list-timers 的 LAST 在预期时间。

---

## 3. 本机 Runbook(港股 / 美股)

**前提(唯一日常依赖)**:跑的时间点**香港住宅代理常开**(Clash :7897,出口须 HKT/AS4760 住宅 IP;机房 IP 会被 Yahoo 429)。wrapper 自检出口,非 HK 则发飞书提醒并跳过、不乱拉。

**组件**
- `scripts/run-overseas.sh <daily|weekly> <hk|us|both> [agents]` — 主 wrapper:设代理 → source `~/.stock-analyze.env` → 自检出口 → 跑两市场两 agent(weekly 覆盖率<50 自动重跑一次)→ 调汇总器。
- `scripts/overseas_summary.py` — 发**飞书交互卡片**(彩色状态标题 + 分市场 2×2 字段:数据/动作/NAV/持仓 + 结论),复用 `stock_analyze.notifier` 凭据/token;卡片失败退回纯文本。
- `scripts/notify-overseas.sh` — 纯文本兜底通知。
- 凭据 `~/.stock-analyze.env`(`SA_LARK_APP_ID/APP_SECRET/USER_OPEN_ID`,从 ECS secrets.env 拉来,chmod 600,不进 git)。
- **`.venv` 必须装 yfinance**(首跑坑:系统 python 有、venv 没有 → `pip install yfinance==1.2.0`)。

**launchd 任务**(`~/Library/LaunchAgents/com.stockanalyze.overseas-*.plist`,未纳入 git)
- `overseas-hk-daily`(周一~五 16:30)/ `overseas-us-daily`(周二~六 08:00)/ `overseas-weekly`(周六 09:00,both)。
- 数据 `data/hk|us/<agent>/`;报告 `reports/hk|us/<agent>/`。

**命令(本机)**
```bash
launchctl list | grep stockanalyze                                  # 任务在册
launchctl start com.stockanalyze.overseas-hk-daily                  # 手动触发(两 agent)
./scripts/run-overseas.sh weekly both                               # 手动全量 weekly
tail -f logs/overseas.log                                           # 运行流水
curl -s https://ipinfo.io/json | grep country                      # 确认出口=HK
```
**睡眠**:锁屏照跑;睡眠时段靠唤醒后 launchd 补跑。更准时设 `sudo pmset repeat wakeorpoweron MTWRF 07:58:00`(需插电)。

---

## 4. 已知坑 / 待办

1. **yfinance 偶发 `curl (35) TLS`**(macOS LibreSSL + curl_cffi + 代理隧道)→ 每轮丢几只票,重试即好。wrapper 已对 weekly 低覆盖自动重跑;治本需 provider 对 TLS(35) 也重试(`stock_analyze/markets/_yfinance_base.py`,§7.0 禁改区,需授权)。
2. **个别 universe 代码 404 delisted**(如 0011.HK / 0489.HK)→ 观察,持续 404 再清 universe。
3. **TCC**:launchd 用 `/bin/bash` 读 `~/Documents`,若日志现 `Operation not permitted`,去「完全磁盘访问」加 `/bin/bash`。
4. 3 个 launchd plist 未纳入 git(可放 `deploy/launchd/` 比照 `deploy/systemd/`,换机可复现)。

## 5. 数据源决策(港美股)

| 出口 | yfinance/Yahoo | akshare/东财 |
|---|---|---|
| 联通家宽直连(CN) | ❌ 403/429 | ✅ 通 |
| 香港机房代理 | ❌ 429 | ❌ 掐 |
| **香港住宅代理(选定)** | ✅ 港美一体 | ❌ 掐(东财按地域封) |

→ 港美股统一用 **yfinance + 香港住宅代理**,不用 akshare。详见 [verify-hk-us-data-sources.md](verify-hk-us-data-sources.md)。
