import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BarChart3,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  Gauge,
  Layers3,
  RefreshCcw,
  Search,
  ShieldAlert,
  WalletCards,
} from "lucide-react";
import { fetchDetail, fetchSummary } from "./api";
import CompetitionPanel from "./CompetitionPanel";
import EtfResearchPanel from "./EtfResearchPanel";
import { PerformanceChart } from "./FinancialCharts";
import InstrumentDrawer from "./InstrumentDrawer";
import { PortfolioSection, RuntimeHistory, StrategyBrief, TradeTimeline } from "./PortfolioViews";
import { accountLabel, formatFieldValue, formatMoney, formatPercent, sideLabel } from "./finance";
import type {
  DashboardDetail,
  DashboardSummary,
  MarketSummary,
  OrderRow,
  StrategyProfile,
  SummaryAgent,
} from "./types";

const preferredMarket = "cn_qdii_etf";
const preferredAgent = "codex";

function chooseDefault(summary: DashboardSummary): { market: string; agent: string } {
  const preferred = summary.markets
    .find((market) => market.market === preferredMarket)
    ?.agents.find((agent) => agent.agent === preferredAgent);
  if (preferred) return { market: preferredMarket, agent: preferredAgent };
  const firstMarket = summary.markets.find((market) => market.agents.length > 0);
  return {
    market: firstMarket?.market ?? preferredMarket,
    agent: firstMarket?.agents[0]?.agent ?? preferredAgent,
  };
}

function agentFromSummary(summary: DashboardSummary | null, market: string, agent: string): SummaryAgent | null {
  return summary?.markets.find((item) => item.market === market)?.agents.find((item) => item.agent === agent) ?? null;
}

function marketFromSummary(summary: DashboardSummary | null, market: string): MarketSummary | null {
  return summary?.markets.find((item) => item.market === market) ?? null;
}

function statusTone(status?: string): "ok" | "warn" | "muted" {
  if (status === "success") return "ok";
  if (status === "failed") return "warn";
  return "muted";
}

function StatusBadge({ status }: { status?: string }) {
  const tone = statusTone(status);
  const Icon = tone === "ok" ? CheckCircle2 : tone === "warn" ? ShieldAlert : Clock3;
  const label = status === "success" ? "正常" : status === "failed" ? "失败" : status === "running" ? "运行中" : "待运行";
  return <span className={`status status-${tone}`}><Icon size={14} aria-hidden="true" />{label}</span>;
}

function MetricTile({
  label,
  value,
  helper,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: string;
  helper: string;
  icon: typeof Activity;
  tone?: "neutral" | "positive" | "negative";
}) {
  return (
    <article className={`metric-tile metric-${tone}`}>
      <span className="metric-icon"><Icon size={18} aria-hidden="true" /></span>
      <div><p>{label}</p><strong>{value}</strong><small>{helper}</small></div>
    </article>
  );
}

function Skeleton() {
  return <div className="skeleton-grid" aria-label="加载中">{Array.from({ length: 8 }, (_, index) => <div key={index} />)}</div>;
}

function matchesSearch(row: OrderRow, search: string): boolean {
  const normalized = search.trim().toLowerCase();
  if (!normalized) return true;
  return Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(normalized));
}

function TargetOrders({
  rows,
  currency,
  onSelect,
}: {
  rows: OrderRow[];
  currency: string;
  onSelect: (row: OrderRow, title: string, trigger: HTMLElement) => void;
}) {
  return (
    <section className="target-orders terminal-section" role="region" aria-label="目标订单">
      <header className="section-heading">
        <div>
          <span className="section-kicker"><Layers3 size={14} aria-hidden="true" />NEXT ORDERS</span>
          <h2>目标订单</h2>
          <p>策略输出的下一交易日计划，尚未成交，不等于当前持仓</p>
        </div>
        <div className="section-stat"><span>待执行</span><strong>{rows.length}</strong></div>
      </header>
      <div className="orders-table-wrap">
        <table className="orders-table">
          <thead><tr><th>计划执行日</th><th>证券</th><th>底层市场</th><th>方向</th><th>份额</th><th>目标金额</th><th>综合评分</th><th>账户</th></tr></thead>
          <tbody>
            {rows.length === 0 ? <tr><td className="empty-cell" colSpan={8}>当前没有待执行订单</td></tr> : rows.map((row) => (
              <tr
                key={`${row.account_id || "account"}-${row.code}-${row.side}`}
                tabIndex={0}
                aria-haspopup="dialog"
                onClick={(event) => onSelect(row, "订单", event.currentTarget)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(row, "订单", event.currentTarget);
                  }
                }}
              >
                <td>{row.execute_after || row.trade_date || "-"}</td>
                <td><b>{row.name || row.code}</b><small>{row.code}</small></td>
                <td>{row.exposure_group || row.industry || "未分类"}<small>{row.theme || "-"}</small></td>
                <td><span className={`side-badge side-${row.side}`}>{row.side_label || sideLabel(String(row.side || ""))}</span></td>
                <td>{formatFieldValue("shares", row.shares)}</td>
                <td>{formatMoney(row.target_value, currency)}</td>
                <td>{formatFieldValue("score", row.score)}</td>
                <td>{row.account_label || accountLabel(String(row.account_id || ""))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const emptyStrategy = (agent: string): StrategyProfile => ({
  agent,
  agent_label: "策略版本",
  strategy_id: null,
  name: "策略版本",
  factors: [],
});

export default function App() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [detail, setDetail] = useState<DashboardDetail | null>(null);
  const [selectedMarket, setSelectedMarket] = useState(preferredMarket);
  const [selectedAgent, setSelectedAgent] = useState(preferredAgent);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selectedRow, setSelectedRow] = useState<OrderRow | null>(null);
  const [selectedRowTitle, setSelectedRowTitle] = useState("明细");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const selectionRef = useRef({ market: selectedMarket, agent: selectedAgent });
  const summaryAbortRef = useRef<AbortController | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);
  const summaryRequestIdRef = useRef(0);
  const detailRequestIdRef = useRef(0);
  const drawerTriggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    selectionRef.current = { market: selectedMarket, agent: selectedAgent };
  }, [selectedMarket, selectedAgent]);

  const loadSummary = useCallback(async () => {
    summaryAbortRef.current?.abort();
    const controller = new AbortController();
    summaryAbortRef.current = controller;
    const requestId = ++summaryRequestIdRef.current;
    const payload = await fetchSummary(controller.signal);
    if (requestId !== summaryRequestIdRef.current) return;
    setSummary(payload);
    setSummaryError(null);
    const current = selectionRef.current;
    const currentMarket = payload.markets.find((market) => market.market === current.market);
    const currentAgent = currentMarket?.agents.find((agent) => agent.agent === current.agent);
    if (!currentMarket || !currentAgent) {
      const next = chooseDefault(payload);
      setSelectedMarket(next.market);
      setSelectedAgent(next.agent);
    }
  }, []);

  const loadDetail = useCallback(async (market: string, agent: string) => {
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;
    const requestId = ++detailRequestIdRef.current;
    setDetailLoading(true);
    setDetail((current) => current?.market === market && current?.agent === agent ? current : null);
    try {
      const payload = await fetchDetail(market, agent, controller.signal);
      if (requestId !== detailRequestIdRef.current) return;
      setDetail(payload);
      setDetailError(null);
    } catch (reason) {
      if (requestId !== detailRequestIdRef.current) return;
      if (reason instanceof Error && reason.name === "AbortError") return;
      setDetail(null);
      setDetailError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (requestId === detailRequestIdRef.current) setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    loadSummary()
      .catch((reason: Error) => { if (reason.name !== "AbortError") setSummaryError(reason.message); })
      .finally(() => setLoading(false));
  }, [loadSummary]);

  useEffect(() => { void loadDetail(selectedMarket, selectedAgent); }, [loadDetail, selectedMarket, selectedAgent]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(() => {
      loadSummary().catch((reason: Error) => { if (reason.name !== "AbortError") setSummaryError(reason.message); });
      void loadDetail(selectedMarket, selectedAgent);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loadDetail, loadSummary, selectedAgent, selectedMarket]);

  useEffect(() => () => {
    summaryAbortRef.current?.abort();
    detailAbortRef.current?.abort();
  }, []);

  const selectedMarketSummary = marketFromSummary(summary, selectedMarket);
  const selectedAgentSummary = agentFromSummary(summary, selectedMarket, selectedAgent);
  const markets = summary?.markets ?? [];
  const agentOptions = selectedMarketSummary?.agents ?? [];
  const activeDetail = detail?.market === selectedMarket && detail?.agent === selectedAgent ? detail : null;
  const error = detailError ?? summaryError;

  const refresh = async () => {
    setLoading(true);
    setSummaryError(null);
    setDetailError(null);
    try {
      await Promise.all([loadSummary(), loadDetail(selectedMarket, selectedAgent)]);
    } catch (reason) {
      if (!(reason instanceof Error) || reason.name !== "AbortError") setSummaryError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  const changeSelection = (market: string, agent: string) => {
    detailAbortRef.current?.abort();
    detailRequestIdRef.current += 1;
    selectionRef.current = { market, agent };
    setDetail(null);
    setSelectedRow(null);
    setDetailError(null);
    setSelectedMarket(market);
    setSelectedAgent(agent);
  };

  const openDrawer = (row: OrderRow, title: string, trigger: HTMLElement) => {
    drawerTriggerRef.current = trigger;
    setSelectedRowTitle(title);
    setSelectedRow(row);
  };

  const closeDrawer = useCallback(() => {
    const trigger = drawerTriggerRef.current;
    setSelectedRow(null);
    drawerTriggerRef.current = null;
    window.requestAnimationFrame(() => trigger?.focus());
  }, []);

  const positions = activeDetail?.positions.rows ?? [];
  const rawOrders = activeDetail?.orders.rows ?? [];
  const events = activeDetail?.activity?.rows ?? [];
  const runs = activeDetail?.runs.rows ?? [];
  const orders = useMemo(() => rawOrders.filter((row) => matchesSearch(row, search)), [rawOrders, search]);
  const filteredPositions = useMemo(() => positions.filter((row) => matchesSearch(row, search)), [positions, search]);
  const filteredEvents = useMemo(() => events.filter((row) => matchesSearch(row, search)), [events, search]);
  const latest = activeDetail?.nav.latest;
  const strategy = activeDetail?.strategy ?? emptyStrategy(selectedAgent);
  const selectedStrategyLabel = selectedAgentSummary?.strategy?.label
    ?? activeDetail?.strategy.agent_label
    ?? strategy.name;
  const benchmarkReturn = latest?.benchmark_return;
  const rawBenchmarkLabel = activeDetail?.nav.benchmark_label || latest?.benchmark_code;
  const benchmarkLabel = rawBenchmarkLabel && rawBenchmarkLabel !== "基准"
    ? rawBenchmarkLabel
    : selectedMarket === "cn_qdii_etf" ? "跨境ETF组合基准" : "A股账户基准";
  const holdingCount = positions.length || rawOrders.filter((row) => row.side !== "sell").length;

  return (
    <main className="app-shell">
      <aside className="left-rail">
        <div className="brand-lockup">
          <span><Gauge size={18} aria-hidden="true" /></span>
          <div><strong>Stock Analyze</strong><p>国内投资模拟终端</p></div>
        </div>

        <nav className="control-group" aria-label="投资市场">
          <label>市场账户</label>
          <p>查看这个市场发生了什么</p>
          <div className="segmented">
            {markets.map((market) => (
              <button key={market.market} type="button" className={market.market === selectedMarket ? "active" : ""} onClick={() => changeSelection(market.market, market.agents[0]?.agent ?? preferredAgent)}>
                {market.label}
              </button>
            ))}
          </div>
        </nav>

        <nav className="control-group" aria-label="策略版本">
          <label>策略版本</label>
          <p>决定系统如何选证券和调仓</p>
          <div className="segmented agent-segmented">
            {agentOptions.map((agent) => (
              <button key={agent.agent} type="button" className={agent.agent === selectedAgent ? "active" : ""} onClick={() => changeSelection(selectedMarket, agent.agent)}>
                {agent.strategy?.label ?? agent.agent}
              </button>
            ))}
          </div>
        </nav>

        <div className="status-stack">
          <div><span>每日决策</span><StatusBadge status={selectedAgentSummary?.tasks.daily.status} /></div>
          <div><span>周度复盘</span><StatusBadge status={selectedAgentSummary?.tasks.weekly.status} /></div>
        </div>
        <button className="ghost-button" type="button" onClick={() => setAutoRefresh((current) => !current)} aria-pressed={autoRefresh}>
          <Activity size={16} aria-hidden="true" />{autoRefresh ? "自动刷新已开启" : "自动刷新已关闭"}
        </button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p>{selectedMarketSummary?.label ?? activeDetail?.market_label ?? selectedMarket} · 纸面交易</p>
            <h1>{selectedStrategyLabel} 策略工作台</h1>
          </div>
          <div className="topbar-actions">
            <label className="search-box"><Search size={16} aria-hidden="true" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索证券、市场或账户" aria-label="搜索证券" /></label>
            <button className="icon-text-button" type="button" onClick={refresh} aria-label="刷新 dashboard"><RefreshCcw className={detailLoading ? "spin" : ""} size={16} aria-hidden="true" />刷新</button>
          </div>
        </header>

        {error ? <div className="error-banner"><ShieldAlert size={18} aria-hidden="true" />{error}</div> : null}
        {loading && !summary ? <Skeleton /> : null}

        <CompetitionPanel
          comparison={selectedMarketSummary?.comparison}
          activeAgent={selectedAgent}
          currency={activeDetail?.currency ?? selectedMarketSummary?.currency ?? "¥"}
          onSelectAgent={(agent) => changeSelection(selectedMarket, agent)}
        />

        <section className="metric-strip" role="region" aria-label="账户总览">
          <MetricTile label="账户净值" value={latest?.total_value_display ?? selectedAgentSummary?.nav.latest_display ?? "-"} helper={`估值日 ${latest?.date ?? selectedAgentSummary?.nav.date ?? "-"}`} icon={WalletCards} />
          <MetricTile label="累计收益" value={latest?.return_display ?? selectedAgentSummary?.nav.return_display ?? "-"} helper="已扣模拟交易成本" icon={BarChart3} tone={(latest?.return ?? selectedAgentSummary?.nav.return ?? 0) >= 0 ? "positive" : "negative"} />
          <MetricTile label="市场基准" value={formatPercent(benchmarkReturn)} helper={benchmarkReturn == null ? `${benchmarkLabel}等待首次行情` : benchmarkLabel} icon={Gauge} tone={(benchmarkReturn ?? 0) >= 0 ? "positive" : "negative"} />
          <MetricTile label={positions.length ? "持仓证券" : "计划证券"} value={String(holdingCount)} helper={positions.length ? `${activeDetail?.positions.summary.market_value_display || "-"} 已配置` : `${rawOrders.length} 笔等待成交`} icon={CircleDollarSign} />
        </section>

        <section className="performance-section terminal-section" role="region" aria-label="净值与基准">
          <header className="section-heading">
            <div><span className="section-kicker"><BarChart3 size={14} aria-hidden="true" />PERFORMANCE</span><h2>净值与市场基准</h2><p>鼠标移动可查看每个交易日的组合收益、基准收益和超额收益</p></div>
            <div className="section-stat"><span>数据更新时间</span><strong>{String(activeDetail?.generated_at ?? summary?.generated_at ?? "-").replace("T", " ")}</strong></div>
          </header>
          <PerformanceChart points={activeDetail?.nav.series ?? []} benchmarkLabel={benchmarkLabel} />
        </section>

        {selectedMarket === "cn_qdii_etf" ? (
          <EtfResearchPanel
            selection={activeDetail?.selection}
            lookthrough={activeDetail?.lookthrough}
            research={activeDetail?.research}
          />
        ) : null}

        <PortfolioSection positions={filteredPositions} planned={orders} currency={activeDetail?.currency ?? selectedMarketSummary?.currency ?? "¥"} onSelect={openDrawer} />

        <div className="analysis-grid">
          <TradeTimeline events={filteredEvents} onSelect={openDrawer} />
          <StrategyBrief strategy={strategy} reportHref={activeDetail?.weekly_report.href} />
        </div>

        <RuntimeHistory rows={runs} onSelect={openDrawer} />

        <TargetOrders rows={orders} currency={activeDetail?.currency ?? selectedMarketSummary?.currency ?? "¥"} onSelect={openDrawer} />
      </section>

      {selectedRow ? (
        <InstrumentDrawer
          row={selectedRow}
          title={selectedRowTitle}
          market={selectedMarket}
          agent={selectedAgent}
          strategyLabel={selectedStrategyLabel}
          onClose={closeDrawer}
        />
      ) : null}
    </main>
  );
}
