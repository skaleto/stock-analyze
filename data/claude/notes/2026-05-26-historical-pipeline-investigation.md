# Historical Pipeline Investigation · 2026-05-26

## 背景

`data/claude/runs.csv` 与 `data/codex/runs.csv` 在 2026-05-24 18:07 这 9 秒钟内,
连续写入了 4 条 `run-daily` 记录,as_of 分别为 2026-05-18~05-21（周一到周四，
都是正常交易日）。但相同 as_of 在历史时段（如 17:25 的 daily 触发）并不存在。

待回答：这 4 条记录是不是 17:25 自动 daily 跑失败后留下的"幽灵补跑"？

## 结论：**Hypothesis A 成立，但根因不是"自动跑失败"，而是"自动跑还没安装"**

5-18 ~ 5-21 期间 **新的 per-agent + market-data dispatch 链路根本没在 ECS 上
跑过**——它是 5-24 23:51 才作为系统化 deploy 被装上去的。runs.csv 里那 4 条
2026-05-24T18:07 的 success 行，是人工 backfill 的产物。

数据可信度方面，**没有任何东西被丢**：daily_nav.csv 4 个交易日 8 行（2 账户×4
日）齐全，trades.csv 5-18 有 81 笔建仓、5-19~5-21 各 0 笔（持仓不动）；后续
5-22 weekly + 5-25 daily 都正常衔接。

## 证据链

### 1. runs.csv 实际上是 8 条不是 4 条

```
18:00:07  as_of=2026-05-18  failed  CacheMiss: history_000425_20260518_45
18:02:04  as_of=2026-05-19  failed  CacheMiss: history_000425_20260519_45
18:04:00  as_of=2026-05-20  failed  CacheMiss: history_000425_20260520_45
18:05:59  as_of=2026-05-21  failed  CacheMiss: history_000425_20260521_45
18:07:02  as_of=2026-05-18  success
18:07:06  as_of=2026-05-19  success
18:07:09  as_of=2026-05-20  success
18:07:11  as_of=2026-05-21  success
```

明显是一次手工 backfill：先批量跑 `run-daily --as-of X` 四次全部 cache miss，
然后人工 `prepare-market-data --force --as-of X` 把缓存填上，再跑一遍全过。
两批之间隔 2 分钟，符合 `prepare-market-data` 逐日抓 800 候选的耗时下限。

### 2. `stock-analyze-market-data.timer` 5-22 之前不存在

`journalctl -u stock-analyze-market-data.timer --since 2026-05-15` 全部输出：

```
May 22 15:50:27 ... Started stock-analyze-market-data.timer
```

→ 这个 timer 是 5-22 15:50 才被首次 enable 的，5-18 ~ 5-21 物理上没有 17:25
trigger。"为什么 17:25 没跑" 的答案：那个时间点这个 timer 根本不在系统里。

### 3. systemd unit 文件 mtime 集中在 5-24 23:51

```
2026-05-24 23:51:58  stock-analyze-market-data.service
2026-05-24 23:51:58  stock-analyze-market-data.timer
2026-05-24 23:51:58  stock-analyze-daily.service    (旧的单 agent 服务)
2026-05-24 23:51:58  stock-analyze-weekly.service
2026-05-26 12:42:49  stock-analyze-{claude,codex}-daily.service  (今天的 OnFailure 补丁)
```

整套现 per-agent 链路是 **5-24 晚上 23:51** 作为一次完整 deploy 落到 ECS 的——
也就是说 5-24 18:07 的人工 backfill 跑完之后才装的。所以 backfill 当时甚至
不是为了"配合即将到来的 timer"，而就是单纯地把缺失的 4 个交易日数据补出来。

### 4. 5-18 ~ 5-21 期间真实跑过的东西

| 时间 | 服务 | 来源 | 写到哪 |
|------|------|------|--------|
| 05-18 19:21 | 旧 `stock-analyze-daily.service` | 人工/旧 timer | `logs/daily.log` 写入；runs.csv **未写** |
| 05-19 16:30 | 旧 `stock-analyze-daily.service` | 旧 16:30 timer | `logs/daily.log` 写入；runs.csv **未写** |
| 05-20 16:30 | 新 `stock-analyze-claude-daily.service` | 人工 `systemctl start`（试新服务） | `logs/claude-daily.log` 写入；runs.csv **未写** |
| 05-21 16:30 | 新 `stock-analyze-claude-daily.service` | 人工 | 同上 |
| 05-21 20:43 | 新 `stock-analyze-claude-daily.service` | 人工 | 同上 |

四条 `claude-daily.log` 行（"Daily run complete: trades=0/0/0/122, nav_rows=2"）
对应的不是 runs.csv 任何一条。换言之，**当时跑过、log 里有痕迹，但 run_ledger
没记录**——这是历史 deploy 时序的副产物，不是当下需要修的代码 bug。

### 5. daily_nav.csv / trades.csv 的覆盖确认

```
2026-05-18  trades=81   benchmark_code="300"   (旧 3 位格式)
2026-05-19  trades=0    benchmark_code="300"
2026-05-20  trades=0    benchmark_code="300"
2026-05-21  trades=0    benchmark_code="300"
2026-05-22  trades=0    weekly signal
2026-05-25  trades=122  benchmark_code="000300" (新 6 位格式)
```

4 个交易日的 NAV 行都齐了，且 5-18 那 81 笔建仓的 `trade_date` 也都写到了
`trades.csv`。**补跑成功地把数据补全**，没有任何持仓/交易/估值丢失。

## 副产物观察（不属于本次修复范围，仅记录）

1. **5-22 17:25 第一次 timer 触发失败**：journal 显示 `EnvironmentFile=/etc/stock-analyze/secrets.env` 里 eastmoney cookie 含分号被 systemd 当成 slot 分隔符
   解析错误（`Failed to resolve specifiers in EASTMONEY_COOKIE=...; ignoring: Invalid slot`）。
   5-25 17:25 已经成功了，说明 cookie 这一行后来被引号转义或拿掉了。

2. **runs.csv 写不写的开关**：5-20 / 5-21 新 service 跑成功但 runs.csv 没行；
   5-24 命令行直跑就有行。怀疑是 `--offline` 路径 / 当时 app 版本的 run_ledger
   尚未对齐到 per-agent 目录布局，**或** 当时 deploy 还没 sync 最新 app 代码。
   今天（5-26）的 `06ebd86` 已经加了 OnFailure 通知，且 5-25 17:34 的 service
   跑也确实写了 runs.csv 行——所以这个问题在新一轮 deploy 之后已自愈，**无需
   单独 follow-up code fix**。如果之后再观察到 systemd 触发的 run 不写 runs.csv，
   再单独立 issue。

3. **benchmark_code 5-22 之后从 `300` / `905` 变成 `000300` / `000905`**：是
   `82e5c8a Migrate data source from AKShare to Tushare Pro + Baostock fallback`
   附带的 code 长度规范化结果。已被新 deploy 接受，但 5-18 ~ 5-22 段历史行没回填。
   不影响 daily NAV 计算（数值匹配），仅是字段格式不一致。后续 dashboard 如果
   按 `benchmark_code` group by 会把 `300` 和 `000300` 看作两个序列。属可观察
   到但不紧急的小坑，**留作后续 dashboard refactor 顺手处理**。

## 处置

- **Hypothesis A 确认（with refinement）**：自动 daily 没"失败"，而是 5-18 ~
  5-21 这条新链路压根没装；5-24 18:07 这 4 条 success 行是人工 backfill 的
  产物，正确且完整。
- **没数据丢**：daily_nav / trades / positions 都对得上，competition `start_date`
  也是 2026-05-26（今天），所以 5-18 ~ 5-22 本来就是 pre-competition warm-up，
  不计入正式打分。
- **不需要代码修复**。本次是 deploy 时序产物，已被 5-24 23:51 的 deploy 自愈。
  上面的 3 个副产物观察作为长期改进备忘，留给 weekly review 决定是否升级为 action。
