# Design · add-beginner-dashboard-view

## 1. 代码层分工

```
stock_analyze/reporting.py
  └─ render_pro_view(state, performance, ...) → 现有 render_dashboard 重命名
  └─ render_beginner_view(state, performance, ...) → 新函数

stock_analyze/dashboard_aggregator.py
  └─ render_pro_competition(...) → 现有 generate_competition_dashboard 重命名
  └─ render_beginner_competition(...) → 新函数
  └─ generate_all_views(...)  → 同时跑 pro + beginner
```

## 2. 模板渲染策略

不引入 jinja2 或 react —— 沿用现有 string-template + helper 函数风格,避免新依赖。

```python
# stock_analyze/beginner_dashboard.py(新)

def render_beginner_competition_html(
    paths_claude: AgentPaths,
    paths_codex: AgentPaths,
    competition_data: dict,
    baseline: dict,
) -> str:
    """Return the full HTML for /simple.html (competition compare view)."""
    sections = []
    sections.append(_render_header())
    sections.append(_render_account_card(paths_claude, paths_codex))
    sections.append(_render_agent_score_cards(paths_claude, paths_codex))
    sections.append(_render_nav_lines(paths_claude, paths_codex))
    sections.append(_render_top_holdings(paths_claude, label="claude", limit=10))
    sections.append(_render_top_holdings(paths_codex, label="codex", limit=10))
    sections.append(_render_position_overlap_summary(paths_claude, paths_codex))
    sections.append(_render_recent_trades(paths_claude, paths_codex, limit=5))
    sections.append(_render_monthly_evolution_summary(paths_claude, paths_codex))
    sections.append(_render_footer_links())
    return _shell_html(title="Claude vs Codex · 简化版", body="\n".join(sections))
```

## 3. 关键 helper 复用

下面 helper 已经在 `reporting.py` 实现,复用就好(只调整文案 + 格式):

| 函数 | 用途 | 新文案 |
|---|---|---|
| `display_positions(df)` | 持仓表 | 减少列 + 千分位 + 中文表头 |
| `display_recent_trades(df, n)` | 近期交易 | "上周二 买入 贵州茅台 100 股 @ ¥1340" |
| `_render_nav_chart(df)` | NAV 曲线 | sub-sample 到周线降密 |

## 4. URL 路由

`stock_analyze/cli.py` serve-dashboard 子命令的 HTTP 路由:

```python
ROUTES = {
    "/":              "reports/competition/simple.html",  # 默认新手
    "/simple.html":   "reports/competition/simple.html",
    "/simple/claude.html": "reports/competition/simple/claude.html",
    "/simple/codex.html":  "reports/competition/simple/codex.html",
    "/pro.html":      "reports/competition/dashboard.html",  # alias
    "/competition/dashboard.html": "reports/competition/dashboard.html",  # 现有
    "/claude/dashboard.html": "reports/claude/dashboard.html",
    "/codex/dashboard.html":  "reports/codex/dashboard.html",
}
```

## 5. 单位 / 文案 helper

新增 `stock_analyze/beginner_format.py`:

```python
def cny(value: float) -> str:
    """Format CNY with appropriate scale:
    1234 → "1,234元"
    12345 → "1.23万元"
    123456789 → "1.23亿元"
    """

def pct(value: float, signed: bool = True, color: bool = False) -> str:
    """-0.012 → "-1.2%" (red span if color)"""

def cn_date(dt: str) -> str:
    """2026-05-22 → "5月22日"(本年内省略年份)"""

def cn_relative_date(dt: str, today: str) -> str:
    """today=2026-05-23, dt=2026-05-22 → "昨天"
       dt=2026-05-19 → "上周二"
       dt=2026-04-15 → "上月15日" """
```

## 6. HTML 结构(简化版 shell)

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>我的纸面投资 · 简化版</title>
  <style>
    body { font: 16px/1.6 -apple-system, "PingFang SC"; max-width: 960px; margin: 0 auto; padding: 24px; background: #fffaf3; color: #2c1a00; }
    .card { background: #fff; border: 1px solid #e0d4b8; border-radius: 12px; padding: 18px 24px; margin: 18px 0; box-shadow: 0 2px 6px rgba(0,0,0,0.04); }
    .big { font-size: 32px; font-weight: 700; }
    .pos { color: #c4520d; }   /* 红涨 */
    .neg { color: #1a7340; }   /* 绿跌 */
    .tab-bar { display: flex; gap: 12px; margin-bottom: 16px; }
    .tab { padding: 8px 16px; border-radius: 6px; text-decoration: none; background: #ede4d3; color: #2c1a00; }
    .tab.active { background: #c4520d; color: white; }
    table { width: 100%; border-collapse: collapse; }
    td, th { padding: 10px; text-align: right; }
    td:first-child, th:first-child { text-align: left; }
    .stock-name { font-weight: 600; }
  </style>
</head>
<body>
  <div class="tab-bar">
    <a class="tab active" href="/simple.html">简化版</a>
    <a class="tab" href="/pro.html">专业版</a>
    <a class="tab" href="/evolution.html">策略演进</a>
  </div>
  ... sections ...
</body>
</html>
```

## 7. 大小预算

| section | 预算行数 / KB |
|---|---|
| header + tab bar | 30 行 / 2 KB |
| 账户总览卡片 | 15 行 / 1 KB |
| 双 agent 卡片 × 2 | 30 行 / 2 KB |
| NAV 曲线(SVG 内联) | 60 行 / 6 KB |
| 持仓表 × 2(各 10 行) | 80 行 / 4 KB |
| 持仓重叠摘要 | 20 行 / 1 KB |
| 近期交易表(5 行) | 30 行 / 1.5 KB |
| 月度策略调整摘要 | 30 行 / 2 KB |
| CSS + footer | 80 行 / 4 KB |
| **总计** | ≈ **375 行 / ~23 KB** |

留有 80 KB 上限的 cap,避免无意识扩张。

## 8. 测试矩阵

| 用例 | 测试 |
|---|---|
| 空 portfolio(无成交)→ 不报错 + 显示"尚未开盘交易" | unit test |
| 单 agent 跑赢 / 跑输基准的方向 | unit test 文案 |
| 中文单位转换(1.23 万元)| unit test |
| 中文相对日期("昨天" / "上周二" / "上月 15 日") | unit test |
| NAV SVG 曲线 viewBox 自适应 | unit test |
| HTML 输出 ≤ 80 KB | unit test(reads output file size) |
| 与现有 `dashboard.html` 文件不冲突 | unit test(both written, sizes match expectations) |
| 持仓表行数 ≤ 10(防止变长)| unit test |

## 9. 不在范围

- 不引入新前端框架
- 不做手机响应式(只支持 macOS / 大屏)
- 不集成实时 push / WebSocket
- 不做用户登录 / 多用户
- 不写 Codex CLI 端的 dashboard 客户端(用户用浏览器即可)
