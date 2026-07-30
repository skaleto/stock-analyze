export const dashboardMarkets = ["a_share", "cn_qdii_etf"] as const;
export type DashboardMarket = typeof dashboardMarkets[number];
export type StrategyKey = "defensive" | "trend";
export type WorkspaceScope = "all" | DashboardMarket | "exceptions";
export type WorkspaceView =
  | "system"
  | "strategy"
  | "model-research"
  | "data-intelligence"
  | "operations";

export type WorkspaceRoute =
  | { view: "system" }
  | {
      view: "strategy";
      market: DashboardMarket;
      mode: "compare";
      strategy?: never;
    }
  | {
      view: "strategy";
      market: DashboardMarket;
      mode: "detail";
      strategy: StrategyKey;
    }
  | { view: "model-research"; market: DashboardMarket }
  | { view: "data-intelligence"; market: DashboardMarket }
  | { view: "operations"; scope: WorkspaceScope };

const strategyAgents: Record<StrategyKey, "claude" | "codex"> = {
  defensive: "claude",
  trend: "codex",
};

function isMarket(value: string | null): value is DashboardMarket {
  return dashboardMarkets.includes(value as DashboardMarket);
}

function isStrategy(value: string | null): value is StrategyKey {
  return value === "defensive" || value === "trend";
}

function strategyForAgent(value: string | null): StrategyKey | null {
  if (value === "claude") return "defensive";
  if (value === "codex") return "trend";
  return null;
}

function marketOrDefault(value: string | null): DashboardMarket {
  return isMarket(value) ? value : "cn_qdii_etf";
}

export function agentForStrategy(strategy: StrategyKey): "claude" | "codex" {
  return strategyAgents[strategy];
}

export function parseWorkspaceRoute(search: string): WorkspaceRoute {
  const params = new URLSearchParams(search);
  const rawView = params.get("view") || "system";
  const market = marketOrDefault(params.get("market"));
  const strategy = isStrategy(params.get("strategy"))
    ? params.get("strategy") as StrategyKey
    : strategyForAgent(params.get("agent"))
      ?? strategyForAgent(rawView);

  if (rawView === "strategy") {
    if (params.get("mode") === "detail") {
      return {
        view: "strategy",
        market,
        mode: "detail",
        strategy: strategy ?? "trend",
      };
    }
    return { view: "strategy", market, mode: "compare" };
  }
  if (rawView === "compare") {
    return { view: "strategy", market, mode: "compare" };
  }
  if (rawView === "detail" || rawView === "claude" || rawView === "codex") {
    return {
      view: "strategy",
      market,
      mode: "detail",
      strategy: strategy ?? "trend",
    };
  }
  if (rawView === "model-research"
    || rawView === "model-iteration"
    || rawView === "model-shadow") {
    return { view: "model-research", market };
  }
  if (rawView === "data-intelligence" || rawView === "intelligence") {
    return { view: "data-intelligence", market };
  }
  if (rawView === "operations") {
    const rawScope = params.get("scope");
    const scope: WorkspaceScope = rawScope === "exceptions"
      || rawScope === "all"
      || isMarket(rawScope)
      ? rawScope
      : "all";
    return { view: "operations", scope };
  }
  return { view: "system" };
}

export function serializeWorkspaceRoute(route: WorkspaceRoute): string {
  const params = new URLSearchParams({ view: route.view });
  if (route.view === "strategy") {
    params.set("mode", route.mode);
    params.set("market", route.market);
    if (route.mode === "detail") {
      params.set("strategy", route.strategy);
    }
  } else if (route.view === "model-research"
    || route.view === "data-intelligence") {
    params.set("market", route.market);
  } else if (route.view === "operations") {
    params.set("scope", route.scope);
  }
  return params.toString();
}

export function routeSearchMatches(search: string, route: WorkspaceRoute): boolean {
  return new URLSearchParams(search).toString()
    === serializeWorkspaceRoute(route);
}
