# tasks · add-beginner-dashboard-view

## 1. OpenSpec foundation

- [x] 1.1 proposal.md / design.md / tasks.md / spec.md 落
- [x] 1.2 `openspec validate add-beginner-dashboard-view --strict` 通过
- [x] 1.3 human operator confirm

## 2. 单位 / 文案 helper

- [x] 2.1 新文件 `stock_analyze/beginner_format.py`
- [x] 2.2 实现 `cny`、`pct`、`cn_date`、`cn_relative_date`
- [x] 2.3 单元测试 `tests/test_beginner_format.py`(各 5+ case)

## 3. 简化版渲染器

- [x] 3.1 新文件 `stock_analyze/beginner_dashboard.py`
- [x] 3.2 8 个 section 渲染函数(见 design.md §2)
- [x] 3.3 `render_beginner_competition_html(...)` 主函数
- [x] 3.4 `render_beginner_agent_html(...)` 单 agent 版本

## 4. dashboard 生成集成

- [x] 4.1 修改 `stock_analyze/dashboard_aggregator.py` 的 `generate_competition_dashboard`,同时输出 pro + simple
- [x] 4.2 simple.html 写到 `reports/competition/simple.html`
- [x] 4.3 单 agent simple 写到 `reports/competition/simple/<agent>.html`
- [x] 4.4 `pro.html` 作为 symlink 或别名指向现有 `dashboard.html`

## 5. serve-dashboard CLI

- [x] 5.1 修改 `stock_analyze/cli.py` 的 serve-dashboard,默认根路径 / 重定向到 /simple.html
- [x] 5.2 加 /pro.html 路由别名
- [x] 5.3 加 /simple/claude.html 与 /simple/codex.html 路由

## 6. 文档

- [x] 6.1 README.md 加 "简化版 vs 专业版" 一段
- [x] 6.2 `docs/system-overview.md` §11(Dashboard)更新拓扑
- [x] 6.3 `docs/competition-runbook.md` 加访问简化版的步骤

## 7. CSS / 文案

- [x] 7.1 暖色调主题
- [x] 7.2 中文表头 / 单位 / 相对日期
- [x] 7.3 红涨绿跌(中国习惯)
- [x] 7.4 emoji 装饰(👤 我的账户 / 📊 持仓 / 🔄 最近交易)

## 8. 测试

- [x] 8.1 `tests/test_beginner_dashboard.py` 8 个 section 各自单元测试
- [x] 8.2 输出 HTML ≤ 80 KB 断言
- [x] 8.3 与现有专业版同时生成,文件不冲突
- [x] 8.4 空 portfolio 不崩
- [x] 8.5 所有 unittest 通过 + pyflakes 0 + openspec validate --strict 通过

## 9. e2e

- [x] 9.1 `python3 -m stock_analyze competition-dashboard` 后,`ls reports/competition/` 含 `simple.html`、`dashboard.html`、`simple/claude.html`、`simple/codex.html`
- [x] 9.2 `python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765`
- [x] 9.3 浏览器访问 `/` 跳到 `/simple.html`,点 [专业版] 跳到 `/pro.html`,点 [策略演进] 跳到现有 pro dashboard 的 `#tab-compare`(`/evolution.html` 是可选未单独建页)
- [x] 9.4 文件大小 simple.html < 80 KB

## 10. 不在范围

- 不引入新前端框架(jinja2/react)
- 不做手机响应式
- 不集成 WebSocket
- 不做用户登录
- 不在 simple.html 显示因子明细 / 覆盖率 / IC(那些在 pro.html)
