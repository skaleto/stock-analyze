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
const MultiAgentResearchPage = lazy(() => (
  import("./MultiAgentResearchPage").then((module) => ({
    default: module.MultiAgentResearchPage,
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
  if (route.view === "multi-agent-research") return "多角色投研";
  if (route.view === "data-intelligence") return "数据与情报";
  if (route.view === "operations") return "运行中心";
  if (route.mode === "compare") return "双策略竞技场";
  return route.strategy === "defensive"
    ? "稳健防守 策略工作台"
    : "趋势进攻 策略工作台";
}

function workspaceSubtitle(route: WorkspaceRoute): string {
  if (route.view === "system") return "双市场 · 双策略 · 研究闭环";
  if (route.view === "operations") return "全部任务 · 当前运行状态";
  if (route.view === "model-research") return "跨市场 · 训练、验收、模拟与采用";
  if (route.view === "multi-agent-research") return "项目证据 · 角色协作 · 审计留痕";
  if (route.view === "data-intelligence") return "全链路 · 数据供给与实际使用";
  const marketLabel = route.market === "a_share" ? "A股" : "跨境ETF";
  if (route.view === "strategy") return `${marketLabel} · 正式模拟策略`;
  return "";
}

function marketFromRoute(route: WorkspaceRoute): DashboardMarket | null {
  if (route.view === "strategy") {
    return route.market;
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
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
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
        strategyMarket={marketContext}
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
        focus={route.focus}
        onFocusMarket={(focus) => navigate({
          view: "model-research",
          ...(focus ? { focus } : {}),
        })}
        refreshToken={refreshToken}
      />
    );
  } else if (route.view === "multi-agent-research") {
    page = <MultiAgentResearchPage refreshToken={refreshToken} />;
  } else if (route.view === "data-intelligence") {
    page = (
      <DataIntelligencePage
        focus={route.focus}
        onFocusMarket={(focus) => navigate({
          view: "data-intelligence",
          ...(focus ? { focus } : {}),
        })}
        refreshToken={refreshToken}
      />
    );
  } else {
    page = (
      <OperationsPage
        scope={route.scope}
        refreshToken={refreshToken}
        onScopeChange={(scope) => navigate({ view: "operations", scope })}
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
