#!/usr/bin/env bash
# notify-overseas.sh — 给 operator 发一条飞书(Lark)应用 DM。
#   用法: notify-overseas.sh "消息文本"   或   echo "消息" | notify-overseas.sh
# 凭据从 ~/.stock-analyze.env 读(SA_LARK_APP_ID / SA_LARK_APP_SECRET / SA_LARK_USER_OPEN_ID)。
# 缺凭据 / 发送失败都静默退出 0(通知是状态通道,不该拖垮主流程)。
set -uo pipefail

REPO="/Users/yaoyibin/Documents/stock/stock-analyze"
ENV_FILE="$HOME/.stock-analyze.env"
PROXY="http://127.0.0.1:7897"

msg="${1:-$(cat)}"
[ -z "${msg:-}" ] && exit 0

# 凭据
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
# Feishu open API 通过香港住宅代理也可达(实测),给 urllib 一个代理
export http_proxy="${http_proxy:-$PROXY}"
export https_proxy="${https_proxy:-$PROXY}"

cd "$REPO" 2>/dev/null || exit 0
"$REPO/.venv/bin/python3" - "$msg" <<'PY' 2>/dev/null
import sys
try:
    from stock_analyze.notifier import LarkCredentials, send_lark_dm
except Exception:
    sys.exit(0)
creds = LarkCredentials.from_env()
if not creds:
    sys.stderr.write("notify-overseas: no Lark creds in env\n"); sys.exit(0)
try:
    send_lark_dm(sys.argv[1], creds)
except Exception as e:
    sys.stderr.write(f"notify-overseas: send failed: {e}\n")
PY
exit 0
