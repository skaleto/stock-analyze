# 操作员告警设置（Lark Webhook）

PIPELINE_FAILURES.log 始终记录失败。Lark 推送是可选的、为了让操作员不用 poll log 就能知道有事。

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
