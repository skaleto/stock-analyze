# 操作员通知设置（Lark）

系统对 operator 推两类消息，**走两条独立通道**，互不影响：

| 类型 | 触发 | 通道 | 收件人 |
|---|---|---|---|
| **故障告警** | systemd `OnFailure=` | 群机器人 webhook | 群里所有人 |
| **每日状态日报 + 待办** | aggregate-dashboard `ExecStartPost=` | Lark Open API DM | 你 1 个人 |

PIPELINE_FAILURES.log 始终记录失败（不依赖任何 Lark 配置）。两条 Lark 推送都是可选的。

---

# 一、故障告警（群机器人 webhook）

## 1. Lark 群机器人 webhook（推荐）

在 Lark 群里：

```
[+] → 添加群机器人 → 自定义机器人
→ 命名"Stock-Analyze 告警"
→ 复制 webhook URL（形如 https://open.feishu.cn/open-apis/bot/v2/hook/<token>）
→ 可选：勾选"自定义关键词 = Stock-Analyze" 或 IP 白名单
```

## 2. 注入到 ECS

```bash
ssh ai_baby
cat >> /etc/stock-analyze/secrets.env <<'EOF'
SA_LARK_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/<token>
EOF
chmod 600 /etc/stock-analyze/secrets.env

# Reload systemd so the template service picks up the env file change
systemctl daemon-reload
```

## 3. 验证

```bash
# 手动触发一次失败通知（用任意 unit 名作为占位）
systemctl start 'stock-analyze-pipeline-failure@manual-test-event.service'
# 等 1 秒
tail -5 /opt/stock-analyze/logs/PIPELINE_FAILURES.log

# 应该收到 Lark 群消息："🚨 Stock-Analyze 流水线失败 ..."
```

## 4. 关闭告警

如要临时禁用 Lark 推送但保留日志：

```bash
# 注释或删除 secrets.env 里的 SA_LARK_WEBHOOK 行
systemctl daemon-reload
```

日志写入路径**始终**有效，与 webhook 是否配置无关。

## 5. 替代方案

如果不想用 Lark：

- **邮件**：把 `scripts/notify-pipeline-failure.sh` 里的 curl 部分替换为 `mail -s "..." op@example.com < /dev/null`
- **短信**：接阿里云短信 API / 移动云短信，curl POST
- **企业微信**：webhook 格式与 Lark 类似，但 JSON schema 略不同

注：脚本设计上 webhook 失败不影响 PIPELINE_FAILURES.log 的写入。

---

# 二、每日状态日报 + 待办（Lark Open API DM）

每天 daily run 结束（约 17:35），把 NAV / 持仓 / sanity-check / 待办整合成一条
DM 推给你（不是群消息）。基于 systemd `ExecStartPost=` 触发，由
`stock_analyze.notifier.cli_send_daily_summary` 处理。

## 1. 创建 Lark 自建应用

DM 通道需要你自己的 Lark app（**不是**前面那个群机器人，是独立的）。

到 [open.feishu.cn](https://open.feishu.cn) → 创建自建应用：

```
1. 应用名称：「Stock-Analyze 通知」
2. 应用功能 → 启用「机器人」
3. 权限管理 → 添加 scope：
   - im:message:send_as_bot  （让 bot 给你发 DM）
   - contact:user.base:readonly  （可选：根据 email 反查 open_id）
4. 版本管理与发布 → 发布到「仅自己可用」
5. 凭证与基础信息 页面拿到：
   App ID     (e.g. cli_a8xxxxxxxx)
   App Secret (点显示)
```

## 2. 拿你的 open_id

任选一种：

```bash
# 方法 A：用 lark-cli (本地，需先 lark-cli auth login)
lark-cli contact +search-user --query "你的姓名或邮箱"
# 输出里取 open_id 字段

# 方法 B：让我（Claude Code）通过 lark-cli 帮你查 —— 把你 Lark 邮箱给我
```

## 3. 注入凭证到 ECS

```bash
ssh ai_baby
cat >> /etc/stock-analyze/secrets.env <<'EOF'
SA_LARK_APP_ID=cli_a8xxxxxxxx
SA_LARK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SA_LARK_USER_OPEN_ID=ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
EOF
chmod 600 /etc/stock-analyze/secrets.env

# 让 aggregate-dashboard.service 重读 EnvironmentFile
systemctl daemon-reload
```

## 4. 手动触发验证

```bash
# 在 ECS 上手动跑一次，看消息能否到飞书
ssh ai_baby
sudo -u root /opt/stock-analyze/app/scripts/notify-daily-summary.sh
# 应该收到 Lark 私聊一条「📊 Stock-Analyze 日报 ...」

# 不带凭证也能跑 —— 会把消息内容打到 stdout 让你预览
unset SA_LARK_APP_ID && \
  /opt/stock-analyze/venv/bin/python -m stock_analyze notify-daily-summary
```

## 5. 自动触发时间

```
17:25  market-data.service
17:34  claude-daily / codex-daily
17:34  aggregate-dashboard.service
       └─ ExecStartPost=notify-daily-summary.sh  ← 这里发 DM
```

## 6. 日报内容

```
📊 Stock-Analyze 日报 2026-05-27 (周三)
─────────────────────────────
🕐 ECS 自动化（今天）:
  ✓ 17:25  market-data
  ✓ 17:34  claude-daily
  ✓ 17:34  codex-daily
  ✓ 17:34  aggregate-dashboard

💰 NAV:
  claude  ¥1,002,421  (+0.24% vs ¥1M)  Δ -0.15%
  codex   ¥1,000,039  (+0.00% vs ¥1M)  Δ -0.13%

📈 持仓:
  claude  hs300=46  zz500=47  (=93/100) ⚠️
  codex   hs300=46  zz500=47  (=93/100) ⚠️

✅ Sanity-check:
  claude  1 warn
    [WARN] forward_ic_coverage: 92% of factor IC rows ... NaN
  codex   1 warn
    [WARN] forward_ic_coverage: 92% of factor IC rows ... NaN

⏰ 待办:
  • 2026-06-01 (周一) monthly-review timer 触发，提前看看 sentiment 历史

🚨 近 2 天 PIPELINE_FAILURES:
  (none)
```

## 7. 待办触发规则

| 触发时机 | 条件 |
|---|---|
| 周六/周日 | 本周 Friday 的 sentiment 行未记录 |
| 月底前 3 天 | 提示下次 `monthly-review` timer 启动时间 |

## 8. 关闭日报

```bash
# 在 secrets.env 里删掉这 3 个 env var 即可
# notify-daily-summary.sh 会自动进入 preview 模式（不发 DM 只打 stdout）

systemctl daemon-reload
```

## 9. 失败处理

| 失败类型 | 行为 |
|---|---|
| 凭证缺失 | preview 模式，stdout 打日报内容，exit 0 |
| Lark Open API 返回错 | stderr 打错误 + 完整日报内容，exit 1 |
| 网络超时 | 同上 |

`ExecStartPost=` 的 `-` 前缀让 DM 失败**不**触发 OnFailure 级联——
DM 是辅助通道，aggregate-dashboard 本身的成功不被它绑架。失败仍会进 systemd 日志，
可以 `journalctl -u stock-analyze-aggregate-dashboard.service` 查到。

## 10. 设计原则

- **不缓存 token**：日报一天一次，每次拿新 token，简单不出错
- **不持久化任何凭证状态**：所有秘密只在 `/etc/stock-analyze/secrets.env`
- **不在错误信息里 echo token**：`get_tenant_access_token` 抛错时只带 Lark API
  返回的 msg 字段，不带 bearer 值
- **DM 失败不写 PIPELINE_FAILURES.log**：DM 是 status 通道不是 alert 通道，
  失败不应该让操作员怀疑流水线本身坏了
