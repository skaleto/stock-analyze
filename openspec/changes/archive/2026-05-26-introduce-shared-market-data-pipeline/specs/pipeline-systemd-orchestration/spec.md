## ADDED Requirements

### Requirement: market-data systemd timer drives weekday data + daily agents

`deploy/systemd/` SHALL 提供 `stock-analyze-market-data.timer` 在 Mon-Fri 17:25 Asia/Shanghai（`Mon..Fri *-*-* 09:25:00 UTC`）触发 `stock-analyze-market-data.service`；该 service SHALL 在 `ExecStart=prepare-market-data` 成功完成后，通过两条 `ExecStartPost=` 行**直接** `systemctl start --no-block` 两个 daily agent service（不经过任何分发脚本）。

#### Scenario: market-data.timer fires Mon-Fri 17:25 CST
- **WHEN** 在 ECS 上 `systemctl list-timers stock-analyze-market-data.timer`
- **THEN** `OnCalendar` 字段等价于 `Mon..Fri *-*-* 09:25:00 UTC`
- **AND** `NEXT` 显示下一个工作日 17:25 CST

#### Scenario: market-data.service triggers daily agents Mon-Fri
- **GIVEN** 今天是周一到周五
- **WHEN** `stock-analyze-market-data.service` 的 ExecStart 成功完成
- **THEN** 两条 `ExecStartPost=/bin/systemctl start --no-block stock-analyze-{claude,codex}-daily.service` 行依次执行
- **AND** 两个 agent service 并行启动（`--no-block` 不等待对方完成）
- **AND** 周五**不再**触发 weekly（weekly 由 Sat weekly-trigger 拉起）

#### Scenario: market-data.service does not trigger agents when ExecStart fails
- **GIVEN** `prepare-market-data` 中途抛异常（例如全部 spot 失败）退出非 0
- **WHEN** systemd 收到 ExecStart 失败信号
- **THEN** ExecStartPost 一行不执行
- **AND** 两个 daily agent service 都不会启动
- **AND** `data/<agent>/runs.csv` 当天不写新行

### Requirement: weekly-trigger systemd timer drives Saturday weekly agents

`deploy/systemd/` SHALL 提供 `stock-analyze-weekly-trigger.timer` 在 Sat 10:00 Asia/Shanghai（`Sat *-*-* 02:00:00 UTC`）触发 `stock-analyze-weekly-trigger.service`；该 service `ExecStart=/bin/true`（占位），通过两条 `ExecStartPost=` 行**直接** `systemctl start --no-block` 两个 weekly agent service；**不**再次跑 `prepare-market-data`，weekly 复用周五 17:25 写入的 cache。

#### Scenario: weekly-trigger.timer fires Sat 10:00 CST
- **WHEN** 在 ECS 上 `systemctl list-timers stock-analyze-weekly-trigger.timer`
- **THEN** `OnCalendar` 字段等价于 `Sat *-*-* 02:00:00 UTC`
- **AND** `NEXT` 显示下一个周六 10:00 CST

#### Scenario: weekly-trigger.service triggers both weekly agents
- **GIVEN** 周六 10:00 CST 到达
- **WHEN** `stock-analyze-weekly-trigger.service` 启动
- **THEN** `ExecStart=/bin/true` 立刻成功
- **AND** 两条 `ExecStartPost=/bin/systemctl start --no-block stock-analyze-{claude,codex}-weekly.service` 行依次执行
- **AND** 两个 weekly agent 都使用周五 17:25 写入的 `data/shared/cache/` 与 `data/shared/market_snapshot_<Fri-date>.json`

#### Scenario: weekly-trigger.service does not re-fetch market data
- **WHEN** `stock-analyze-weekly-trigger.service` 启动并完成
- **THEN** 没有任何 `prepare-market-data` 进程出现在进程列表
- **AND** `data/shared/cache/` 下所有 csv 文件的 mtime 不变
- **AND** `data/shared/runs.csv` 不会因为 weekly-trigger 自身新增一行（agent service 自己写各自的 runs.csv）

### Requirement: Agent services run in offline mode and have no independent timer

修改后的 4 个 agent service（`stock-analyze-{claude,codex}-{daily,weekly}.service`）SHALL 在 `ExecStart` 末尾加 `--offline`；对应的 4 个 timer 文件 SHALL 从 `deploy/systemd/` 删除；ECS 部署 SHALL `systemctl disable --now` 这 4 个老 timer 并 `rm /etc/systemd/system/<timer-file>`。

#### Scenario: Agent service ExecStart contains --offline
- **WHEN** 读取 `deploy/systemd/stock-analyze-claude-daily.service`
- **THEN** `ExecStart=` 行包含 `--offline`
- **AND** 同样适用于 codex-daily / claude-weekly / codex-weekly

#### Scenario: No agent timer files remain in deploy/systemd
- **WHEN** 列 `deploy/systemd/stock-analyze-*.timer`
- **THEN** 只见 `stock-analyze-market-data.timer` / `stock-analyze-weekly-trigger.timer` / `stock-analyze-monthly-review.timer`（和老的单 agent 兼容路径的 `stock-analyze-{daily,weekly}.timer`）
- **AND** **不**出现 `stock-analyze-{claude,codex}-{daily,weekly}.timer`

#### Scenario: Agent service fails fast on cache miss
- **GIVEN** prepare-market-data 跑挂，没有为今天写新 cache
- **WHEN** 运维手工 `systemctl start stock-analyze-claude-daily.service`（自动链路下不会发生：ExecStart 失败时 ExecStartPost 不执行）
- **THEN** claude-daily.service ExecStart 启动后立即 raise `CacheMiss`
- **AND** systemd 标记 service `failed`
- **AND** `data/<agent>/runs.csv` 写一行 `status=failed`
- **AND** 不会有任何 outbound HTTP 请求

### Requirement: Idempotent migration from old per-agent timers

ECS 上的迁移 SHALL 安全可重复：执行 `disable --now` 旧 timer × 4 + `rm` 单元文件 × 4 + 安装新 unit + `daemon-reload` + `enable --now` 两个新 timer（market-data + weekly-trigger）整套；重复执行不应破坏现有 cache / data / runs。

#### Scenario: Re-running migration script is safe
- **GIVEN** 已经完整跑过一次迁移
- **WHEN** 再跑一次迁移脚本
- **THEN** `data/<agent>/state.json` / `daily_nav.csv` / `trades.csv` 全部未变
- **AND** `systemctl list-timers` 仍只显示 `market-data` + `weekly-trigger` + `monthly-review` 三个 pipeline timer
- **AND** 不会出现重复的 ExecStartPost 触发链
