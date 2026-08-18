# Stock Analyze

面向中国大陆投资者的 A 股与境内跨境 ETF 研究、双策略比较和纸面交易系统。系统不连接券商、不真实下单，也不构成投资建议。

当前正式策略：

- `claude`：稳健防守。
- `codex`：趋势进攻。

内部 ID 为历史兼容字段，两套策略都由当前系统统一维护。直接港股和美股模拟已经归档。

## 文档入口

- [系统总览](docs/system-overview.md)：完成态架构、技术路线、数据源、功能和定时任务。
- [系统 Harness](docs/system-harness.md)：开发、运行、部署、验收和故障处理。
- [竞赛运行手册](docs/competition-runbook.md)：双策略规则、周度复盘和月度演化。
- [项目维护策略](docs/project-maintenance.md)：当前文档入口、数据保留和定期清理规则。

## 快速检查

```bash
python3 -m pip install -r requirements.txt
./scripts/system-audit.sh
```

## Dashboard

```bash
python3 -m stock_analyze competition-dashboard
python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765/app.html`。

生产部署、SSH 隧道和完整验收命令见 [docs/system-harness.md](docs/system-harness.md)。
