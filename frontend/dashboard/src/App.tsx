import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Search } from "lucide-react";
import { WorkspaceShell } from "./WorkspaceShell";
import {
  parseWorkspaceRoute,
  routeSearchMatches,
  serializeWorkspaceRoute,
  type DashboardMarket,
  type WorkspaceRoute,
} from "./workspaceRoute";

const SystemOverviewPanel = lazy(() => import("./SystemOverviewPanel"));
const StrategyWorkspacePage = lazy(() => (
  import("./StrategyWorkspacePage").then((module) => ({
    default: module.StrategyWorkspacePage,
  }))
));
const ModelResearchPage = lazy(() => (
  import("./ModelResearchPage").then((module) => ({
    default: module.ModelResearchPage,
  }))
));
const DataIntelligencePage = lazy(() => (
  import("./DataIntelligencePage").then((module) => ({
    default: module.DataIntelligencePage,
  }))
));
const OperationsPage = lazy(() => (
  import("./OperationsPage").then((module) => ({
    default: module.OperationsPage,
  }))
));

function WorkspacePageSkeleton() {
  return (
    <div className="skeleton-grid" aria-label="工作区加载中">
      <div /><div /><div /><div />
    </div>
  );
}

function workspaceTitle(route: WorkspaceRoute): string {
  if (route.view === "system") return "投研决策总览";
  if (route.view === "model-research") return "模型研究";
  if (route.view === "data-intelligence") return "数据与情报";
  if (route.view === "operations") return "运行中心";
  if (route.mode === "compare") return "双策略竞技场";
  return route.strategy === "defensive"
    ? "稳健防守 策略工作台"
    : "趋势进攻 策略工作台";
}

function workspaceSubtitle(route: WorkspaceRoute): string {
  if (route.view === "system") return "双市场 · 双策略 · 研究闭环";
  if (route.view === "operations") {
    const labels = {
      all: "全部市场",
      a_share: "A股",
      cn_qdii_etf: "跨境ETF",
      exceptions: "仅异常",
    };
    return labels[route.scope];
  }
  const marketLabel = route.market === "a_share" ? "A股" : "跨境ETF";
  if (route.view === "strategy") return `${marketLabel} · 正式模拟策略`;
  if (route.view === "model-research") {
    return `${marketLabel} · 训练、验收、模拟与采用`;
  }
  return `${marketLabel} · 数据供给与实际使用`;
}

function marketFromRoute(route: WorkspaceRoute): DashboardMarket | null {
  if (
    route.view === "strategy"
    || route.view === "model-research"
    || route.view === "data-intelligence"
  ) {
    return route.market;
  }
  if (
    route.view === "operations"
    && (route.scope === "a_share" || route.scope === "cn_qdii_etf")
  ) {
    return route.scope;
  }
  return null;
}

export default function App() {
  const [route, setRoute] = useState<WorkspaceRoute>(() => (
    parseWorkspaceRoute(window.location.search)
  ));
  const [marketContext, setMarketContext] = useState<DashboardMarket>(() => (
    marketFromRoute(parseWorkspaceRoute(window.location.search))
      ?? "cn_qdii_etf"
  ));
  const [search, setSearch] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshToken, setRefreshToken] = useState(0);
  const [pageBusy, setPageBusy] = useState(false);

  const navigate = useCallback((
    next: WorkspaceRoute,
    mode: "push" | "replace" = "push",
  ) => {
    const matches = routeSearchMatches(window.location.search, next);
    setRoute(next);
    setSearch("");
    setPageBusy(false);
    if (mode === "push" && matches) return;
    const url = `${window.location.pathname}?${serializeWorkspaceRoute(next)}`;
    window.history[
      mode === "push" ? "pushState" : "replaceState"
    ]({}, "", url);
  }, []);

  useEffect(() => {
    if (!routeSearchMatches(window.location.search, route)) {
      navigate(route, "replace");
    }
  }, [navigate, route]);

  useEffect(() => {
    const restore = () => {
      setRoute(parseWorkspaceRoute(window.location.search));
      setSearch("");
      setPageBusy(false);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  useEffect(() => {
    const nextMarket = marketFromRoute(route);
    if (nextMarket) setMarketContext(nextMarket);
  }, [route]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(
      () => setRefreshToken((value) => value + 1),
      60_000,
    );
    return () => window.clearInterval(timer);
  }, [autoRefresh]);

  let page: ReactNode;
  if (route.view === "system") {
    page = (
      <SystemOverviewPanel
        refreshToken={refreshToken}
        onNavigate={navigate}
      />
    );
  } else if (route.view === "strategy") {
    page = (
      <StrategyWorkspacePage
        market={route.market}
        mode={route.mode}
        strategy={route.strategy}
        search={search}
        onSelectStrategy={(strategy) => navigate({
          view: "strategy",
          mode: "detail",
          market: route.market,
          strategy,
        })}
        refreshToken={refreshToken}
        onBusyChange={setPageBusy}
      />
    );
  } else if (route.view === "model-research") {
    page = (
      <ModelResearchPage
        market={route.market}
        refreshToken={refreshToken}
      />
    );
  } else if (route.view === "data-intelligence") {
    page = (
      <DataIntelligencePage
        market={route.market}
        refreshToken={refreshToken}
      />
    );
  } else {
    page = (
      <OperationsPage
        scope={route.scope}
        refreshToken={refreshToken}
      />
    );
  }

  return (
    <WorkspaceShell
      route={route}
      marketContext={marketContext}
      title={workspaceTitle(route)}
      subtitle={workspaceSubtitle(route)}
      busy={pageBusy}
      autoRefresh={autoRefresh}
      onNavigate={navigate}
      onRefresh={() => setRefreshToken((value) => value + 1)}
      onToggleAutoRefresh={() => setAutoRefresh((value) => !value)}
      headerActions={
        route.view === "strategy" && route.mode === "detail" ? (
          <label className="search-box">
            <Search size={16} aria-hidden="true" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索证券、市场或账户"
              aria-label="搜索证券"
            />
          </label>
        ) : null
      }
    >
      <Suspense fallback={<WorkspacePageSkeleton />}>
        {page}
      </Suspense>
    </WorkspaceShell>
  );
}
