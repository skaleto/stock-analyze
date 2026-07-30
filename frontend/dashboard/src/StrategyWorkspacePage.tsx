import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ElementType,
} from "react";
import {
  BarChart3,
  CircleDollarSign,
  Gauge,
  Layers3,
  WalletCards,
} from "lucide-react";
import AlertCenter from "./AlertCenter";
import CompetitionPanel from "./CompetitionPanel";
import EtfResearchPanel from "./EtfResearchPanel";
import { PerformanceChart } from "./FinancialCharts";
import GovernancePanel from "./GovernancePanel";
import InstrumentDrawer from "./InstrumentDrawer";
import ModelHealthPanel from "./ModelHealthPanel";
import {
  PortfolioSection,
  RuntimeHistory,
  StrategyBrief,
  TradeTimeline,
} from "./PortfolioViews";
import PredictionPanel from "./PredictionPanel";
import { fetchSummary } from "./api";
import {
  accountLabel,
  formatFieldValue,
  formatMoney,
  formatPercent,
  sideLabel,
} from "./finance";
import type {
  DashboardSummary,
  OrderRow,
  StrategyProfile,
} from "./types";
import { useDashboardData } from "./useDashboardData";
import { useWorkspaceResource } from "./useWorkspaceResource";
import {
  agentForStrategy,
  type DashboardMarket,
  type StrategyKey,
} from "./workspaceRoute";

type Props = {
  market: DashboardMarket;
  mode: "compare" | "detail";
  strategy?: StrategyKey;
  search: string;
  refreshToken: number;
  onSelectStrategy: (strategy: StrategyKey) => void;
  onBusyChange?: (busy: boolean) => void;
};

function matchesSearch(row: OrderRow, search: string): boolean {
  const normalized = search.trim().toLowerCase();
  if (!normalized) return true;
  return Object.values(row).some((value) => (
    String(value ?? "").toLowerCase().includes(normalized)
  ));
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
  icon: ElementType;
  tone?: "neutral" | "positive" | "negative";
}) {
  return (
    <article className={`metric-tile metric-${tone}`}>
      <span className="metric-icon">
        <Icon size={18} aria-hidden="true" />
      </span>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
        <small>{helper}</small>
      </div>
    </article>
  );
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
    <section
      className="target-orders terminal-section"
      role="region"
      aria-label="目标订单"
    >
      <header className="section-heading">
        <div>
          <span className="section-kicker">
            <Layers3 size={14} aria-hidden="true" />
            NEXT ORDERS
          </span>
          <h2>目标订单</h2>
          <p>策略输出的下一交易日计划，尚未成交，不等于当前持仓</p>
        </div>
        <div className="section-stat">
          <span>待执行</span>
          <strong>{rows.length}</strong>
        </div>
      </header>
      <div className="orders-table-wrap">
        <table className="orders-table">
          <thead>
            <tr>
              <th>计划执行日</th>
              <th>证券</th>
              <th>底层市场</th>
              <th>方向</th>
              <th>份额</th>
              <th>目标金额</th>
              <th>综合评分</th>
              <th>账户</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td className="empty-cell" colSpan={8}>当前没有待执行订单</td>
              </tr>
            ) : rows.map((row) => (
              <tr
                key={`${row.account_id || "account"}-${row.code}-${row.side}`}
                tabIndex={0}
                aria-haspopup="dialog"
                onClick={(event) => onSelect(
                  row,
                  "订单",
                  event.currentTarget,
                )}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(row, "订单", event.currentTarget);
                  }
                }}
              >
                <td>{row.execute_after || row.trade_date || "-"}</td>
                <td>
                  <b>{row.name || row.code}</b>
                  <small>{row.code}</small>
                </td>
                <td>
                  {row.exposure_group || row.industry || "未分类"}
                  <small>{row.theme || "-"}</small>
                </td>
                <td>
                  <span className={`side-badge side-${row.side}`}>
                    {row.side_label || sideLabel(String(row.side || ""))}
                  </span>
                </td>
                <td>{formatFieldValue("shares", row.shares)}</td>
                <td>{formatMoney(row.target_value, currency)}</td>
                <td>{formatFieldValue("score", row.score)}</td>
                <td>
                  {row.account_label
                    || accountLabel(String(row.account_id || ""))}
                </td>
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

export function StrategyWorkspacePage({
  market,
  mode,
  strategy,
  search,
  refreshToken,
  onSelectStrategy,
  onBusyChange,
}: Props) {
  const summaryLoader = useCallback(
    (signal: AbortSignal) => fetchSummary(signal),
    [],
  );
  const summaryResource = useWorkspaceResource<DashboardSummary>(
    `strategy:${market}`,
    true,
    summaryLoader,
  );
  const agent = agentForStrategy(strategy ?? "trend");
  const detailEnabled = mode === "detail";
  const {
    detail,
    error: detailError,
    loading: detailLoading,
    reload,
  } = useDashboardData(market, agent, detailEnabled);
  const [selectedRow, setSelectedRow] = useState<OrderRow | null>(null);
  const [selectedRowTitle, setSelectedRowTitle] = useState("明细");
  const drawerTriggerRef = useRef<HTMLElement | null>(null);
  const previousRefreshToken = useRef(refreshToken);
  const refreshMounted = useRef(false);

  useEffect(() => {
    onBusyChange?.(summaryResource.loading || detailLoading);
  }, [
    detailLoading,
    onBusyChange,
    summaryResource.loading,
  ]);

  useEffect(() => {
    if (!refreshMounted.current) {
      refreshMounted.current = true;
      previousRefreshToken.current = refreshToken;
      return;
    }
    if (previousRefreshToken.current !== refreshToken) {
      previousRefreshToken.current = refreshToken;
      summaryResource.refresh();
      if (detailEnabled) void reload();
    }
  }, [
    detailEnabled,
    refreshToken,
    reload,
    summaryResource.refresh,
  ]);

  useEffect(() => {
    setSelectedRow(null);
    drawerTriggerRef.current = null;
  }, [agent, market, mode]);

  const marketSummary = summaryResource.data?.markets.find(
    (item) => item.market === market,
  );
  const selectedAgentSummary = marketSummary?.agents.find(
    (item) => item.agent === agent,
  );

  if (summaryResource.loading && !summaryResource.data) {
    return (
      <div className="skeleton-grid" aria-label="策略工作台加载中">
        <div /><div /><div /><div />
      </div>
    );
  }

  if (mode === "compare") {
    if (!marketSummary) {
      return (
        <div className="error-banner" role="alert">
          策略对比数据不可用：{summaryResource.error ?? "unknown"}
        </div>
      );
    }
    return (
      <>
        {summaryResource.stale ? (
          <div className="stale-banner" role="status">
            摘要刷新失败，正在显示最后成功快照
          </div>
        ) : null}
        <CompetitionPanel
          comparison={marketSummary.comparison}
          currency={marketSummary.currency ?? "¥"}
          onSelectAgent={(selectedAgent) => onSelectStrategy(
            selectedAgent === "claude" ? "defensive" : "trend",
          )}
        />
      </>
    );
  }

  const positions = detail?.positions.rows ?? [];
  const rawOrders = detail?.orders.rows ?? [];
  const events = detail?.activity?.rows ?? [];
  const runs = detail?.runs.rows ?? [];
  const orders = rawOrders.filter((row) => matchesSearch(row, search));
  const filteredPositions = positions.filter(
    (row) => matchesSearch(row, search),
  );
  const filteredEvents = events.filter(
    (row) => matchesSearch(row, search),
  );
  const latest = detail?.nav.latest;
  const holdingCount = positions.length
    || rawOrders.filter((row) => row.side !== "sell").length;
  const rawBenchmarkLabel = detail?.nav.benchmark_label
    || latest?.benchmark_code;
  const benchmarkLabel = rawBenchmarkLabel && rawBenchmarkLabel !== "基准"
    ? rawBenchmarkLabel
    : market === "cn_qdii_etf"
      ? "跨境ETF组合基准"
      : "A股账户基准";
  const currency = detail?.currency ?? marketSummary?.currency ?? "¥";
  const strategyProfile = detail?.strategy ?? emptyStrategy(agent);
  const strategyLabel = selectedAgentSummary?.strategy?.label
    ?? detail?.strategy.agent_label
    ?? strategyProfile.name;

  const openDrawer = (
    row: OrderRow,
    title: string,
    trigger: HTMLElement,
  ) => {
    drawerTriggerRef.current = trigger;
    setSelectedRowTitle(title);
    setSelectedRow(row);
  };

  const closeDrawer = () => {
    const trigger = drawerTriggerRef.current;
    setSelectedRow(null);
    drawerTriggerRef.current = null;
    window.requestAnimationFrame(() => trigger?.focus());
  };

  return (
    <div className={`strategy-workspace strategy-workspace-${agent}`}>
      {detailError || summaryResource.error ? (
        <div className="error-banner" role="alert">
          {detailError ?? summaryResource.error}
        </div>
      ) : null}
      <section
        className="metric-strip"
        role="region"
        aria-label="账户总览"
      >
        <MetricTile
          label="账户净值"
          value={latest?.total_value_display
            ?? selectedAgentSummary?.nav.latest_display
            ?? "-"}
          helper={`估值日 ${
            latest?.date ?? selectedAgentSummary?.nav.date ?? "-"
          }`}
          icon={WalletCards}
        />
        <MetricTile
          label="累计收益"
          value={latest?.return_display
            ?? selectedAgentSummary?.nav.return_display
            ?? "-"}
          helper="已扣模拟交易成本"
          icon={BarChart3}
          tone={(latest?.return
            ?? selectedAgentSummary?.nav.return
            ?? 0) >= 0 ? "positive" : "negative"}
        />
        <MetricTile
          label="市场基准"
          value={formatPercent(latest?.benchmark_return)}
          helper={benchmarkLabel}
          icon={Gauge}
          tone={(latest?.benchmark_return ?? 0) >= 0
            ? "positive"
            : "negative"}
        />
        <MetricTile
          label={positions.length ? "持仓证券" : "计划证券"}
          value={String(holdingCount)}
          helper={positions.length
            ? `${detail?.positions.summary.market_value_display || "-"} 已配置`
            : `${rawOrders.length} 笔等待成交`}
          icon={CircleDollarSign}
        />
      </section>
      <section role="region" aria-label="当前持仓">
        <PortfolioSection
          positions={filteredPositions}
          planned={orders}
          currency={currency}
          onSelect={openDrawer}
        />
      </section>
      <GovernancePanel data={detail?.governance} />
      <section
        className="performance-section terminal-section"
        role="region"
        aria-label="净值与基准"
      >
        <header className="section-heading">
          <div>
            <span className="section-kicker">
              <BarChart3 size={14} aria-hidden="true" />
              PERFORMANCE
            </span>
            <h2>净值与市场基准</h2>
            <p>鼠标移动可查看每个交易日的组合收益、基准收益和超额收益</p>
          </div>
          <div className="section-stat">
            <span>数据更新时间</span>
            <strong>
              {String(detail?.generated_at ?? "-").replace("T", " ")}
            </strong>
          </div>
        </header>
        <PerformanceChart
          points={detail?.nav.series ?? []}
          benchmarkLabel={benchmarkLabel}
        />
      </section>
      <div className="prediction-workbench">
        <PredictionPanel
          summary={detail?.prediction_summary}
          modelDriven={false}
        />
        <div className="prediction-side-stack">
          <AlertCenter alerts={detail?.alerts} />
          <ModelHealthPanel
            health={detail?.model_health}
            regimes={detail?.regimes}
            sources={detail?.source_health}
          />
        </div>
      </div>
      {market === "cn_qdii_etf" ? (
        <EtfResearchPanel
          selection={detail?.selection}
          lookthrough={detail?.lookthrough}
          research={detail?.research}
          modelDriven={false}
        />
      ) : null}
      <div className="analysis-grid">
        <TradeTimeline events={filteredEvents} onSelect={openDrawer} />
        <StrategyBrief
          strategy={strategyProfile}
          reportHref={detail?.weekly_report.href}
        />
      </div>
      <RuntimeHistory rows={runs} onSelect={openDrawer} />
      <TargetOrders
        rows={orders}
        currency={currency}
        onSelect={openDrawer}
      />
      {selectedRow ? (
        <InstrumentDrawer
          row={selectedRow}
          title={selectedRowTitle}
          market={market}
          agent={agent}
          strategyLabel={strategyLabel}
          onClose={closeDrawer}
        />
      ) : null}
    </div>
  );
}
