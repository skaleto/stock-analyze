import type { ReactNode } from "react";
import {
  Activity,
  BarChart3,
  BrainCircuit,
  ChevronDown,
  Gauge,
  GitCompareArrows,
  Layers3,
  RadioTower,
  RefreshCcw,
  ShieldCheck,
  TrendingUp,
  Workflow,
} from "lucide-react";
import {
  dashboardMarkets,
  type DashboardMarket,
  type WorkspaceRoute,
  type WorkspaceView,
} from "./workspaceRoute";

type Props = {
  route: WorkspaceRoute;
  marketContext: DashboardMarket;
  title: string;
  subtitle: string;
  busy: boolean;
  autoRefresh: boolean;
  headerActions?: ReactNode;
  railStatus?: ReactNode;
  children: ReactNode;
  onNavigate: (route: WorkspaceRoute) => void;
  onRefresh: () => void;
  onToggleAutoRefresh: () => void;
};

const marketLabels: Record<DashboardMarket, string> = {
  a_share: "A股",
  cn_qdii_etf: "跨境ETF",
};

function defaultRoute(
  view: WorkspaceView,
  marketContext: DashboardMarket,
): WorkspaceRoute {
  if (view === "system") return { view: "system" };
  if (view === "strategy") {
    return { view: "strategy", mode: "compare", market: marketContext };
  }
  if (view === "model-research") {
    return { view };
  }
  if (view === "data-intelligence") {
    return { view };
  }
  return { view: "operations", scope: "all" };
}

function strategyMarketRoute(
  route: WorkspaceRoute,
  market: DashboardMarket,
): WorkspaceRoute {
  if (route.view !== "strategy") {
    return { view: "strategy", mode: "compare", market };
  }
  if (route.mode === "compare") {
    return { view: "strategy", mode: "compare", market };
  }
  return { ...route, market };
}

function currentPage(active: boolean): "page" | undefined {
  return active ? "page" : undefined;
}

export function WorkspaceShell({
  route,
  marketContext,
  title,
  subtitle,
  busy,
  autoRefresh,
  headerActions,
  railStatus,
  children,
  onNavigate,
  onRefresh,
  onToggleAutoRefresh,
}: Props) {
  const strategyOpen = route.view === "strategy";

  return (
    <main className="app-shell">
      <aside className="left-rail">
        <div className="brand-lockup">
          <span>
            <Gauge size={18} aria-hidden="true" />
          </span>
          <div>
            <strong>Stock Analyze</strong>
            <p>国内投资模拟终端</p>
          </div>
        </div>

        <nav className="rail-analysis-nav" aria-label="工作区">
          <span className="rail-nav-label">全局工作区</span>
          <div className="rail-nav-list">
            <button
              type="button"
              className={route.view === "system"
                ? "rail-nav-item active"
                : "rail-nav-item"}
              aria-current={currentPage(route.view === "system")}
              onClick={() => onNavigate(defaultRoute("system", marketContext))}
            >
              <Workflow size={17} aria-hidden="true" />
              <strong>决策总览</strong>
            </button>

            <button
              type="button"
              className={route.view === "model-research"
                ? "rail-nav-item active"
                : "rail-nav-item"}
              aria-current={currentPage(route.view === "model-research")}
              onClick={() => onNavigate(
                defaultRoute("model-research", marketContext),
              )}
            >
              <BrainCircuit size={17} aria-hidden="true" />
              <strong>模型研究</strong>
            </button>
            <button
              type="button"
              className={route.view === "data-intelligence"
                ? "rail-nav-item active"
                : "rail-nav-item"}
              aria-current={currentPage(route.view === "data-intelligence")}
              onClick={() => onNavigate(
                defaultRoute("data-intelligence", marketContext),
              )}
            >
              <RadioTower size={17} aria-hidden="true" />
              <strong>数据与情报</strong>
            </button>
            <button
              type="button"
              className={route.view === "operations"
                ? "rail-nav-item active"
                : "rail-nav-item"}
              aria-current={currentPage(route.view === "operations")}
              onClick={() => onNavigate(
                defaultRoute("operations", marketContext),
              )}
            >
              <Activity size={17} aria-hidden="true" />
              <strong>运行中心</strong>
            </button>

            <span className="rail-nav-label rail-nav-section-label">
              模拟策略
            </span>
            <div className={strategyOpen
              ? "rail-menu-branch active"
              : "rail-menu-branch"}
            >
              <button
                type="button"
                className={strategyOpen
                  ? "rail-nav-item active"
                  : "rail-nav-item"}
                aria-expanded={strategyOpen}
                onClick={() => onNavigate(
                  defaultRoute("strategy", marketContext),
                )}
              >
                <BarChart3 size={17} aria-hidden="true" />
                <strong>策略工作台</strong>
                <ChevronDown
                  className="rail-nav-chevron"
                  size={15}
                  aria-hidden="true"
                />
              </button>
              {strategyOpen ? (
                <div className="rail-workspace-children">
                  <nav className="rail-market-switch" aria-label="策略市场">
                    {dashboardMarkets.map((market) => (
                      <button
                        key={market}
                        type="button"
                        className={route.market === market ? "active" : ""}
                        aria-pressed={route.market === market}
                        onClick={() => onNavigate(
                          strategyMarketRoute(route, market),
                        )}
                      >
                        {marketLabels[market]}
                      </button>
                    ))}
                  </nav>
                  <button
                    type="button"
                    className={route.mode === "compare"
                      ? "rail-sub-item active"
                      : "rail-sub-item"}
                    aria-current={currentPage(route.mode === "compare")}
                    onClick={() => onNavigate({
                      view: "strategy",
                      mode: "compare",
                      market: route.market,
                    })}
                  >
                    <GitCompareArrows size={15} aria-hidden="true" />
                    策略对比
                  </button>
                  <span className="rail-sub-heading">
                    <Layers3 size={15} aria-hidden="true" />
                    单策略分析
                  </span>
                  <button
                    type="button"
                    className={route.mode === "detail"
                      && route.strategy === "defensive"
                      ? "rail-strategy-item active"
                      : "rail-strategy-item"}
                    aria-current={currentPage(
                      route.mode === "detail"
                      && route.strategy === "defensive",
                    )}
                    onClick={() => onNavigate({
                      view: "strategy",
                      mode: "detail",
                      market: route.market,
                      strategy: "defensive",
                    })}
                  >
                    <ShieldCheck size={15} aria-hidden="true" />
                    稳健防守
                  </button>
                  <button
                    type="button"
                    className={route.mode === "detail"
                      && route.strategy === "trend"
                      ? "rail-strategy-item active"
                      : "rail-strategy-item"}
                    aria-current={currentPage(
                      route.mode === "detail"
                      && route.strategy === "trend",
                    )}
                    onClick={() => onNavigate({
                      view: "strategy",
                      mode: "detail",
                      market: route.market,
                      strategy: "trend",
                    })}
                  >
                    <TrendingUp size={15} aria-hidden="true" />
                    趋势进攻
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </nav>

        <div className="status-stack">{railStatus}</div>
        <button
          className="ghost-button"
          type="button"
          aria-pressed={autoRefresh}
          onClick={onToggleAutoRefresh}
        >
          <Activity size={16} aria-hidden="true" />
          {autoRefresh ? "自动刷新已开启" : "自动刷新已关闭"}
        </button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p>{subtitle}</p>
            <h1>{title}</h1>
          </div>
          <div className="topbar-actions">
            {headerActions}
            <button
              className="icon-text-button"
              type="button"
              aria-label="刷新 dashboard"
              aria-busy={busy}
              onClick={onRefresh}
            >
              <RefreshCcw
                className={busy ? "spin" : ""}
                size={16}
                aria-hidden="true"
              />
              刷新
            </button>
          </div>
        </header>
        {children}
      </section>
    </main>
  );
}
