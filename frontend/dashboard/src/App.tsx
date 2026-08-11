import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  Activity,
  BarChart3,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  Gauge,
  GitCompareArrows,
  Layers3,
  RefreshCcw,
  Search,
  ShieldAlert,
  ShieldCheck,
  TrendingUp,
  WalletCards,
} from "lucide-react";
import { fetchSummary } from "./api";
import CompetitionPanel from "./CompetitionPanel";
import EtfResearchPanel from "./EtfResearchPanel";
import { PerformanceChart } from "./FinancialCharts";
import InstrumentDrawer from "./InstrumentDrawer";
import PredictionPanel from "./PredictionPanel";
import AlertCenter from "./AlertCenter";
import ModelHealthPanel from "./ModelHealthPanel";
import { PortfolioSection, RuntimeHistory, StrategyBrief, TradeTimeline } from "./PortfolioViews";
import { useDashboardData } from "./useDashboardData";
import { accountLabel, formatFieldValue, formatMoney, formatPercent, sideLabel } from "./finance";
import type {
  DashboardSummary,
  MarketSummary,
  ModelIterationStatus,
  OrderRow,
  StrategyProfile,
  SummaryAgent,
} from "./types";

const preferredMarket = "cn_qdii_etf";
const preferredAgent = "codex";
const comparisonView = "compare";
const detailView = "detail";
const modelIterationView = "model-iteration";
const legacyModelShadowView = "model-shadow";
const modelShadowAgent = "model_shadow";
const strategyAgents = {
  defensive: "claude",
  trend: "codex",
} as const;

type StrategyKey = keyof typeof strategyAgents;

type WorkspaceView = typeof comparisonView | typeof detailView | typeof modelIterationView;
type WorkspaceRoute = { market: string; view: WorkspaceView; agent: string };

function isStrategyKey(value: string | null | undefined): value is StrategyKey {
  return value === "defensive" || value === "trend";
}

function strategyForAgent(agent: string): StrategyKey {
  return agent === strategyAgents.defensive ? "defensive" : "trend";
}

function agentForStrategy(strategy: string | null | undefined): string | null {
  return isStrategyKey(strategy) ? strategyAgents[strategy] : null;
}

function strategyFromHistory(): StrategyKey | null {
  const state = window.history.state;
  return state && typeof state === "object" && isStrategyKey(state.strategy) ? state.strategy : null;
}

function routeFromLocation(): WorkspaceRoute {
  const params = new URLSearchParams(window.location.search);
  const rawView = params.get("view") || comparisonView;
  const view = rawView === comparisonView
    ? comparisonView
    : rawView === modelIterationView || rawView === legacyModelShadowView
      ? modelIterationView
      : detailView;
  const strategyAgent = agentForStrategy(params.get("strategy"));
  const historyAgent = agentForStrategy(strategyFromHistory());
  const legacyAgent = ![comparisonView, detailView, modelIterationView, legacyModelShadowView].includes(rawView)
    ? rawView
    : params.get("agent");
  return {
    market: params.get("market") || preferredMarket,
    view,
    agent: view === modelIterationView
      ? modelShadowAgent
      : strategyAgent || legacyAgent || historyAgent || preferredAgent,
  };
}

function routeSearch(route: WorkspaceRoute): string {
  const params = new URLSearchParams({
    market: route.market,
    view: route.view,
  });
  if (route.view === detailView) params.set("strategy", strategyForAgent(route.agent));
  return params.toString();
}

function routeHistoryState(route: WorkspaceRoute): { strategy?: StrategyKey } {
  return route.view === detailView ? { strategy: strategyForAgent(route.agent) } : {};
}

function locationMatchesRoute(route: WorkspaceRoute): boolean {
  const params = new URLSearchParams(window.location.search);
  if (params.get("market") !== route.market || params.get("view") !== route.view || params.has("agent")) return false;
  if (route.view === comparisonView || route.view === modelIterationView) return !params.has("strategy");
  return params.get("strategy") === strategyForAgent(route.agent);
}

function normalizeRoute(summary: DashboardSummary, route: WorkspaceRoute): WorkspaceRoute {
  const fallback = chooseDefault(summary);
  const market = summary.markets.find((item) => item.market === route.market)
    ?? summary.markets.find((item) => item.market === fallback.market);
  if (!market) return { market: fallback.market, view: detailView, agent: fallback.agent };
  if (route.view === modelIterationView) {
    return { market: market.market, view: modelIterationView, agent: modelShadowAgent };
  }
  const agent = market.agents.find((item) => item.agent === route.agent)?.agent
    ?? market.agents.find((item) => item.agent === preferredAgent)?.agent
    ?? market.agents[0]?.agent
    ?? fallback.agent;
  if (route.view === comparisonView && market.comparison) {
    return { market: market.market, view: comparisonView, agent };
  }
  if (route.view === detailView && market.agents.some((item) => item.agent === agent)) {
    return { market: market.market, view: detailView, agent };
  }
  return {
    market: market.market,
    view: market.comparison ? comparisonView : detailView,
    agent,
  };
}

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

function ModelIterationLifecycle({ status }: { status?: ModelIterationStatus | null }) {
  const champion = status?.champion;
  const candidate = status?.candidate;
  const cycles = candidate?.shadow_cycles ?? 0;
  const requiredCycles = cycles + (candidate?.shadow_cycles_remaining ?? 4);
  const progress = requiredCycles > 0 ? Math.min(100, cycles / requiredCycles * 100) : 0;
  const history = status?.version_history ?? [];
  const recent = history[history.length - 1];
  return (
    <section className="model-iteration-status" role="region" aria-label="模型版本生命周期">
      <header className="model-iteration-heading">
        <span className="model-iteration-icon"><BrainCircuit size={18} aria-hidden="true" /></span>
        <div><strong>Champion / Challenger</strong><p>{status?.isolation ?? "完全隔离，不计入双策略竞赛"}</p></div>
        {recent ? <small>最近完成 {recent.display_version ?? recent.model_version}</small> : null}
      </header>
      <div className="model-version-flow">
        <article className="model-version-block champion">
          <span><ShieldCheck size={14} aria-hidden="true" />当前正式版</span>
          <strong>{champion?.display_version ?? "尚无正式版本"}</strong>
          <small>{champion ? `${champion.horizon ?? status?.horizon}日 · ${champion.status_label ?? "正式使用"}` : "正式策略暂不读取模型"}</small>
        </article>
        <div className="model-version-connector" aria-hidden="true"><i /></div>
        <article className="model-version-block challenger">
          <span><BrainCircuit size={14} aria-hidden="true" />当前验证版</span>
          <strong>{candidate?.display_version ?? "等待候选版本"}</strong>
          <small>{candidate ? `${candidate.horizon ?? status?.horizon}日 · ${candidate.status_label ?? "研究候选"}` : "新训练版本会自动进入"}</small>
        </article>
        <div className="model-validation-progress">
          <span>验证进度 {cycles} / {requiredCycles || 4}</span>
          <div><i style={{ width: `${progress}%` }} /></div>
          <small>{candidate?.status === "shadow" ? "达到门槛后晋级，随后自动切换下一候选" : "通过研究门槛后开始累计验证周期"}</small>
        </div>
      </div>
    </section>
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
  const initialRoute = useRef(routeFromLocation());
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [workspaceRoute, setWorkspaceRoute] = useState(initialRoute.current);
  const [loading, setLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selectedRow, setSelectedRow] = useState<OrderRow | null>(null);
  const [selectedRowTitle, setSelectedRowTitle] = useState("明细");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const selectionRef = useRef<WorkspaceRoute>(initialRoute.current);
  const summaryRef = useRef<DashboardSummary | null>(null);
  const summaryAbortRef = useRef<AbortController | null>(null);
  const summaryRequestIdRef = useRef(0);
  const drawerTriggerRef = useRef<HTMLElement | null>(null);
  const selectedMarket = workspaceRoute.market;
  const selectedView = workspaceRoute.view;
  const selectedAgent = workspaceRoute.agent;
  const isComparison = selectedView === comparisonView;
  const isModelIteration = selectedView === modelIterationView;
  const isSingleStrategy = selectedView === detailView;
  const routeMarketSummary = marketFromSummary(summary, selectedMarket);
  const detailEnabled = isModelIteration || (
    isSingleStrategy
    && Boolean(routeMarketSummary?.agents.some((agent) => agent.agent === selectedAgent))
  );
  const {
    detail,
    error: detailError,
    loading: detailLoading,
    reload: loadDetail,
  } = useDashboardData(selectedMarket, selectedAgent, detailEnabled);

  const applyWorkspace = useCallback((
    market: string,
    view: WorkspaceView,
    agent: string,
    historyMode: "push" | "replace" = "push",
  ) => {
    const route = { market, view, agent };
    const current = selectionRef.current;
    if (historyMode === "push"
      && current.market === route.market
      && current.view === route.view
      && current.agent === route.agent
      && locationMatchesRoute(route)) return;
    selectionRef.current = route;
    setSelectedRow(null);
    setSearch("");
    setWorkspaceRoute(route);
    window.history[historyMode === "push" ? "pushState" : "replaceState"](
      routeHistoryState(route),
      "",
      `${window.location.pathname}?${routeSearch(route)}`,
    );
  }, []);

  useEffect(() => {
    selectionRef.current = workspaceRoute;
  }, [workspaceRoute]);

  useEffect(() => {
    const restoreWorkspace = () => {
      const parsed = routeFromLocation();
      const route = summaryRef.current ? normalizeRoute(summaryRef.current, parsed) : parsed;
      selectionRef.current = route;
      setSelectedRow(null);
      setSearch("");
      setWorkspaceRoute(route);
      if (!locationMatchesRoute(route)) {
        window.history.replaceState(routeHistoryState(route), "", `${window.location.pathname}?${routeSearch(route)}`);
      }
    };
    window.addEventListener("popstate", restoreWorkspace);
    return () => window.removeEventListener("popstate", restoreWorkspace);
  }, []);

  const loadSummary = useCallback(async () => {
    summaryAbortRef.current?.abort();
    const controller = new AbortController();
    summaryAbortRef.current = controller;
    const requestId = ++summaryRequestIdRef.current;
    const payload = await fetchSummary(controller.signal);
    if (requestId !== summaryRequestIdRef.current) return;
    summaryRef.current = payload;
    setSummary(payload);
    setSummaryError(null);
    const current = selectionRef.current;
    const next = normalizeRoute(payload, current);
    if (next.market !== current.market || next.view !== current.view || next.agent !== current.agent || !locationMatchesRoute(next)) {
      applyWorkspace(next.market, next.view, next.agent, "replace");
    }
  }, [applyWorkspace]);

  useEffect(() => {
    setLoading(true);
    loadSummary()
      .catch((reason: Error) => { if (reason.name !== "AbortError") setSummaryError(reason.message); })
      .finally(() => setLoading(false));
  }, [loadSummary]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(() => {
      loadSummary().catch((reason: Error) => { if (reason.name !== "AbortError") setSummaryError(reason.message); });
      if (detailEnabled) void loadDetail();
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, detailEnabled, loadDetail, loadSummary]);

  useEffect(() => () => {
    summaryAbortRef.current?.abort();
  }, []);

  const selectedMarketSummary = routeMarketSummary;
  const markets = summary?.markets ?? [];
  const agentOptions = selectedMarketSummary?.agents ?? [];
  const selectedAgentSummary = isSingleStrategy
    ? agentFromSummary(summary, selectedMarket, selectedAgent)
    : null;
  const activeDetail = detailEnabled && detail?.market === selectedMarket && detail?.agent === selectedAgent ? detail : null;
  const error = (detailEnabled ? detailError : null) ?? summaryError;

  const refresh = async () => {
    setLoading(true);
    setSummaryError(null);
    try {
      await Promise.all(detailEnabled ? [loadSummary(), loadDetail()] : [loadSummary()]);
    } catch (reason) {
      if (!(reason instanceof Error) || reason.name !== "AbortError") setSummaryError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  const changeMarket = (market: string) => {
    const marketSummary = summary?.markets.find((item) => item.market === market);
    const nextAgent = isModelIteration
      ? modelShadowAgent
      : marketSummary?.agents.some((agent) => agent.agent === selectedAgent)
      ? selectedAgent
      : marketSummary?.agents[0]?.agent ?? preferredAgent;
    const nextView = isModelIteration
      ? modelIterationView
      : isComparison && marketSummary?.comparison
        ? comparisonView
        : detailView;
    applyWorkspace(market, nextView, nextAgent);
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
  const selectedStrategyLabel = isModelIteration
    ? "模型迭代"
    : selectedAgentSummary?.strategy?.label
    ?? activeDetail?.strategy.agent_label
    ?? strategy.name;
  const benchmarkReturn = latest?.benchmark_return;
  const rawBenchmarkLabel = activeDetail?.nav.benchmark_label || latest?.benchmark_code;
  const benchmarkLabel = rawBenchmarkLabel && rawBenchmarkLabel !== "基准"
    ? rawBenchmarkLabel
    : selectedMarket === "cn_qdii_etf" ? "跨境ETF组合基准" : "A股账户基准";
  const holdingCount = positions.length || rawOrders.filter((row) => row.side !== "sell").length;
  const workspaceTitle = isComparison
    ? "双策略竞技场"
    : isModelIteration
      ? "模型迭代工作台"
      : `${selectedStrategyLabel} 策略工作台`;
  const workspaceSubtitle = isComparison
    ? "收益、风险、持仓与决策差异"
    : isModelIteration
      ? "版本晋级、候选验证与独立模拟组合"
      : "账户、预测、持仓与执行明细";
  const modelIteration = activeDetail?.model_iteration ?? activeDetail?.model_shadow;

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
              <button key={market.market} type="button" className={market.market === selectedMarket ? "active" : ""} onClick={() => changeMarket(market.market)}>
                {market.label}
              </button>
            ))}
          </div>
        </nav>

        <nav className="rail-analysis-nav" aria-label="分析导航">
          <span className="rail-nav-label">分析视图</span>
          <div className="rail-nav-list">
            <button
              type="button"
              className={isComparison ? "rail-nav-item active" : "rail-nav-item"}
              aria-current={isComparison ? "page" : undefined}
              aria-label="策略对比"
              onClick={() => applyWorkspace(selectedMarket, comparisonView, selectedAgent)}
            >
              <GitCompareArrows size={17} aria-hidden="true" />
              <strong>策略对比</strong>
            </button>

            <div className={isSingleStrategy ? "rail-menu-branch active" : "rail-menu-branch"}>
              <button
                type="button"
                className={isSingleStrategy ? "rail-nav-item active" : "rail-nav-item"}
                aria-current={isSingleStrategy ? "page" : undefined}
                aria-expanded={isSingleStrategy}
                aria-controls="strategy-object-navigation"
                aria-label="单策略分析"
                onClick={() => applyWorkspace(selectedMarket, detailView, selectedAgent)}
              >
                <Layers3 size={17} aria-hidden="true" />
                <strong>单策略分析</strong>
                <ChevronDown className="rail-nav-chevron" size={15} aria-hidden="true" />
              </button>

              {isSingleStrategy ? (
                <nav id="strategy-object-navigation" className="rail-strategy-nav" aria-label="策略对象">
                  {agentOptions.map((agent) => {
                    const active = selectedAgent === agent.agent;
                    const Icon = agent.agent === "claude" ? ShieldCheck : TrendingUp;
                    return (
                      <button
                        key={agent.agent}
                        type="button"
                        className={active ? `rail-strategy-item rail-strategy-item-${agent.agent} active` : `rail-strategy-item rail-strategy-item-${agent.agent}`}
                        style={{ "--view-color": agent.strategy?.color ?? "var(--accent)" } as CSSProperties}
                        aria-current={active ? "page" : undefined}
                        aria-label={agent.strategy?.label ?? agent.agent}
                        onClick={() => applyWorkspace(selectedMarket, detailView, agent.agent)}
                      >
                        <Icon size={15} aria-hidden="true" />
                        <span><strong>{agent.strategy?.label ?? agent.agent}</strong><small>{agent.strategy?.strategy_name ?? "策略明细"}</small></span>
                      </button>
                    );
                  })}
                </nav>
              ) : null}
            </div>

            <button
              type="button"
              className={isModelIteration ? "rail-nav-item active" : "rail-nav-item"}
              aria-current={isModelIteration ? "page" : undefined}
              aria-label="模型迭代"
              onClick={() => applyWorkspace(selectedMarket, modelIterationView, modelShadowAgent)}
            >
              <BrainCircuit size={17} aria-hidden="true" />
              <strong>模型迭代</strong>
            </button>
          </div>
        </nav>

        <div className="status-stack" aria-label="运行状态">
          {isComparison ? agentOptions.map((agent) => (
            <div key={agent.agent}>
              <span>{agent.strategy?.label ?? agent.agent}</span>
              <StatusBadge status={agent.tasks.daily.status} />
            </div>
          )) : isModelIteration ? (
            <>
              <div><span>模型决策</span><StatusBadge status={runs[0]?.status ? String(runs[0].status) : undefined} /></div>
              <div><span>验证版本</span><span>{modelIteration?.candidate?.display_version ?? "待生成"}</span></div>
            </>
          ) : (
            <>
              <div><span>每日决策</span><StatusBadge status={selectedAgentSummary?.tasks.daily.status} /></div>
              <div><span>周度复盘</span><StatusBadge status={selectedAgentSummary?.tasks.weekly.status} /></div>
            </>
          )}
        </div>
        <button className="ghost-button" type="button" onClick={() => setAutoRefresh((current) => !current)} aria-pressed={autoRefresh}>
          <Activity size={16} aria-hidden="true" />{autoRefresh ? "自动刷新已开启" : "自动刷新已关闭"}
        </button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p>{selectedMarketSummary?.label ?? activeDetail?.market_label ?? selectedMarket} · {workspaceSubtitle}</p>
            <h1>{workspaceTitle}</h1>
          </div>
          <div className="topbar-actions">
            {!isComparison ? <label className="search-box"><Search size={16} aria-hidden="true" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索证券、市场或账户" aria-label="搜索证券" /></label> : null}
            <button className="icon-text-button" type="button" onClick={refresh} aria-label="刷新 dashboard" aria-busy={loading || detailLoading}><RefreshCcw className={loading || detailLoading ? "spin" : ""} size={16} aria-hidden="true" />刷新</button>
          </div>
        </header>

        {error ? <div className="error-banner"><ShieldAlert size={18} aria-hidden="true" />{error}</div> : null}
        {loading && !summary ? <Skeleton /> : null}

        {isComparison ? (
          <CompetitionPanel
            comparison={selectedMarketSummary?.comparison}
            currency={selectedMarketSummary?.currency ?? "¥"}
            onSelectAgent={(agent) => applyWorkspace(selectedMarket, detailView, agent)}
          />
        ) : (
          <div className={`strategy-workspace strategy-workspace-${selectedAgent}`}>
            {isModelIteration ? <ModelIterationLifecycle status={modelIteration} /> : null}
            <section className="metric-strip" role="region" aria-label="账户总览">
              <MetricTile label="账户净值" value={latest?.total_value_display ?? selectedAgentSummary?.nav.latest_display ?? "-"} helper={`估值日 ${latest?.date ?? selectedAgentSummary?.nav.date ?? "-"}`} icon={WalletCards} />
              <MetricTile label="累计收益" value={latest?.return_display ?? selectedAgentSummary?.nav.return_display ?? "-"} helper="已扣模拟交易成本" icon={BarChart3} tone={(latest?.return ?? selectedAgentSummary?.nav.return ?? 0) >= 0 ? "positive" : "negative"} />
              <MetricTile label="市场基准" value={formatPercent(benchmarkReturn)} helper={benchmarkReturn == null ? `${benchmarkLabel}等待首次行情` : benchmarkLabel} icon={Gauge} tone={(benchmarkReturn ?? 0) >= 0 ? "positive" : "negative"} />
              <MetricTile label={positions.length ? "持仓证券" : "计划证券"} value={String(holdingCount)} helper={positions.length ? `${activeDetail?.positions.summary.market_value_display || "-"} 已配置` : `${rawOrders.length} 笔等待成交`} icon={CircleDollarSign} />
            </section>

            <div className="prediction-workbench">
              <PredictionPanel summary={activeDetail?.prediction_summary} modelDriven={isModelIteration} />
              <div className="prediction-side-stack">
                <AlertCenter alerts={activeDetail?.alerts} />
                <ModelHealthPanel health={activeDetail?.model_health} regimes={activeDetail?.regimes} sources={activeDetail?.source_health} />
              </div>
            </div>

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
                modelDriven={isModelIteration}
              />
            ) : null}

            <PortfolioSection positions={filteredPositions} planned={orders} currency={activeDetail?.currency ?? selectedMarketSummary?.currency ?? "¥"} onSelect={openDrawer} heading={isModelIteration ? modelIteration?.portfolio_label ?? "候选模型模拟组合" : undefined} />

            <div className="analysis-grid">
              <TradeTimeline events={filteredEvents} onSelect={openDrawer} />
              <StrategyBrief strategy={strategy} reportHref={activeDetail?.weekly_report.href} />
            </div>

            <RuntimeHistory rows={runs} onSelect={openDrawer} />

            <TargetOrders rows={orders} currency={activeDetail?.currency ?? selectedMarketSummary?.currency ?? "¥"} onSelect={openDrawer} />
          </div>
        )}
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
