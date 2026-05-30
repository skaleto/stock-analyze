#!/usr/bin/env bash
# run-overseas.sh — 本机拉港股/美股 yfinance 数据 + 跑模拟 + 飞书 DM 汇报。
#
#   用法: run-overseas.sh <daily|weekly> <hk|us|both> [agents]
#     daily  = run-daily  (执行到期订单 + 标记 NAV)
#     weekly = run-weekly (生成信号 + 下单 + NAV + 报告)
#     agents = 可选,空格分隔(默认 "claude codex");测试时可只传 "claude"
#
# 前提: 香港住宅代理常开(端口 7897)。脚本会先自检出口必须是 HK,
#       不是就发飞书提醒并跳过(避免在错误出口下 yfinance 429)。
# 两个 agent(claude + codex)都会跑,镜像竞赛;首跑自动 init。
# 由 launchd 定时调用;锁屏照跑,睡眠靠 pmset 唤醒 + launchd 补跑(见 plist)。
set -uo pipefail

REPO="/Users/yaoyibin/Documents/stock/stock-analyze"
PROXY="http://127.0.0.1:7897"
PY="$REPO/.venv/bin/python3"
NOTIFY="$REPO/scripts/notify-overseas.sh"
SUMMARY="$REPO/scripts/overseas_summary.py"
LOG="$REPO/logs/overseas.log"

cd "$REPO" || exit 1
mkdir -p "$REPO/logs"
export http_proxy="$PROXY" https_proxy="$PROXY" all_proxy="socks5://127.0.0.1:7897"
# 飞书凭据(SA_LARK_*),供 overseas_summary.py 发卡片;缺了就只打印不发
[ -f "$HOME/.stock-analyze.env" ] && { set -a; . "$HOME/.stock-analyze.env"; set +a; }
RESULTS="$(mktemp -t overseas_results.XXXXXX)"
trap 'rm -f "$RESULTS"' EXIT

MODE="${1:-daily}"
WHICH="${2:-both}"
AGENTS="${3:-claude codex}"
ts() { date '+%Y-%m-%d %H:%M'; }
logln() { printf '%s\n' "$(date '+%F %T') $*" >>"$LOG"; }

case "$MODE" in daily|weekly) ;; *) echo "MODE must be daily|weekly" >&2; exit 2;; esac
case "$WHICH" in hk) markets=(hk);; us) markets=(us);; both) markets=(hk us);; *) echo "WHICH must be hk|us|both" >&2; exit 2;; esac

logln "=== run-overseas $MODE $WHICH start ==="

# --- 出口自检:必须香港(yfinance 需香港住宅 IP) ---
geo="$(curl -s --max-time 12 https://ipinfo.io/json 2>/dev/null)"
country="$(printf '%s' "$geo" | sed -n 's/.*"country"[ :]*"\([^"]*\)".*/\1/p' | head -1)"
if [ "${country:-?}" != "HK" ]; then
  logln "egress not HK (got '${country:-none}') -> skip"
  "$NOTIFY" "🚨 港美股 $MODE 跳过($(ts))：出口不是香港（当前 ${country:-未知}），香港住宅代理没开或节点不对。请开代理后手动重跑：scripts/run-overseas.sh $MODE $WHICH"
  exit 0
fi

cmd="run-$MODE"
allok=1

parse_metrics() {  # $1=run output ; sets BATCHES/TRADES/FAILED
  local out="$1" line
  line="$(printf '%s\n' "$out" | grep -E 'run complete' | tail -1)"
  BATCHES="$(printf '%s' "$line" | sed -n 's/.*batches=\([0-9]*\).*/\1/p')"
  TRADES="$(printf '%s' "$line" | sed -n 's/.*trades=\([0-9]*\).*/\1/p')"
  # 失败票数 = 出现过 fetch failed 的不同 ticker
  FAILED="$(printf '%s\n' "$out" | grep -oE 'fetch failed for [0-9A-Za-z.]+' | awk '{print $NF}' | sort -u | wc -l | tr -d ' ')"
  COMPLETE="$line"
}

run_one() {  # $1=agent $2=market
  local ag="$1" mkt="$2" out rc state
  state="data/$mkt/$ag/state.json"
  [ -f "$state" ] || { logln "init $mkt/$ag"; "$PY" -m stock_analyze --agent "$ag" --market "$mkt" init >>"$LOG" 2>&1; }

  out="$("$PY" -m stock_analyze --agent "$ag" --market "$mkt" "$cmd" 2>&1)"; rc=$?
  parse_metrics "$out"

  # weekly 覆盖率过低(yfinance 偶发 TLS 丢票)→ 重跑一次
  if [ "$MODE" = weekly ] && [ "${BATCHES:-0}" -lt 50 ]; then
    logln "$mkt/$ag low coverage (batches=${BATCHES:-0}) -> retry"
    sleep 5
    out="$("$PY" -m stock_analyze --agent "$ag" --market "$mkt" "$cmd" 2>&1)"; rc=$?
    parse_metrics "$out"
  fi

  if [ $rc -ne 0 ] || [ -z "$COMPLETE" ]; then
    allok=0; rc=${rc:-1}; [ $rc -eq 0 ] && rc=1
    logln "$mkt/$ag FAIL rc=$rc"
    logln "$(printf '%s\n' "$out" | tail -3)"
  else
    logln "$mkt/$ag OK batches=${BATCHES:-} trades=${TRADES:-} failed=${FAILED:-0}"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$mkt" "$ag" "$cmd" "$rc" "${BATCHES:-}" "${TRADES:-}" "${FAILED:-0}" >>"$RESULTS"
}

for mkt in "${markets[@]}"; do
  for ag in $AGENTS; do run_one "$ag" "$mkt"; done
done

# 富汇总 + 发飞书(详细:做了什么/拿到什么数据/动作/NAV/持仓/sanity/结论)
"$PY" "$SUMMARY" "$MODE" "$RESULTS" "${markets[@]}" >>"$LOG" 2>&1 \
  || "$NOTIFY" "⚠️ 港美股 $MODE [$WHICH] $(ts)：汇总器异常,详见 logs/overseas.log"

logln "=== done (allok=$allok) ==="
[ $allok -eq 1 ] || exit 1
exit 0
