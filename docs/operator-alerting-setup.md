# 操作员通知设置

系统只保留两类飞书消息：即时故障告警和固定窗口的整体任务摘要。

| 消息 | 时间 | 内容 |
| --- | --- | --- |
| 日报 | 工作日 19:30 | 四条日流水线、两套策略总净值、当日成交与异常 |
| 周报提醒 | 周六 10:45 | 四条周流水线、待执行订单、周度复盘提示语 |
| 月报提醒 | 每月 1 日 09:30 | 月报状态、月度表现、策略演化提示语 |
| 故障告警 | 失败后立即 | 失败 unit 和日志位置 |

单个策略完成后只刷新 Dashboard，不再发送消息。三类摘要使用
`data/notifications/workflow_sent.json` 去重，同一 cadence/target 默认只发一次。

## 自建应用凭证

飞书私聊卡片需要自建应用具备 `im:message:send_as_bot` 权限，并在 ECS
`/etc/stock-analyze/secrets.env` 中配置：

```text
SA_LARK_APP_ID=<app-id>
SA_LARK_APP_SECRET=<app-secret>
SA_LARK_USER_OPEN_ID=<operator-open-id>
```

文件应由 root 持有且权限为 `600`。凭证不得写入仓库或日志。

故障告警可以额外配置群机器人：

```text
SA_LARK_WEBHOOK=<group-bot-webhook>
```

没有 webhook 时，故障脚本会使用上述自建应用凭证给操作员发送私聊。

## 定时器

```text
stock-analyze-daily-summary.timer    Mon-Fri 19:30 Asia/Shanghai
stock-analyze-weekly-summary.timer   Sat 10:45 Asia/Shanghai
stock-analyze-monthly-summary.timer  day 1 09:30 Asia/Shanghai
```

对应服务统一调用：

```bash
python3 -m stock_analyze notify-workflow-summary \
  --cadence <daily|weekly|monthly> \
  --repo-root /opt/stock-analyze/app
```

日报在 A 股 18:30 和 QDII 18:50 流水线之后发送；周提醒在周六 10:00 和
10:15 两组任务之后发送；月提醒在 09:00 月度评审之后发送。

## 手动验证

预览，不发消息也不写去重账本：

```bash
cd /opt/stock-analyze/app
/opt/stock-analyze/venv/bin/python -m stock_analyze \
  notify-workflow-summary --cadence daily --preview
```

真实发送当前周提醒：

```bash
systemctl start stock-analyze-weekly-summary.service
tail -n 20 /opt/stock-analyze/logs/weekly-summary.log
tail -n 20 /opt/stock-analyze/logs/weekly-summary.err
```

已经发送的 target 会返回 `workflow notification already sent`。只有修正消息时
才使用 `--force`。

## 故障处理

每个行情、策略、Dashboard 和摘要服务都通过
`stock-analyze-pipeline-failure@.service` 记录失败。日志路径：

```text
/opt/stock-analyze/logs/PIPELINE_FAILURES.log
```

检查命令：

```bash
systemctl --failed --no-pager
journalctl -u <failed-unit> --since '2 days ago' --no-pager
tail -n 80 /opt/stock-analyze/logs/PIPELINE_FAILURES.log
```

消息发送失败不会改变纸面账户状态，也不会删除订单。修复凭证或网络后可安全重启
对应 summary service，去重账本会阻止已成功 target 的重复推送。
