# Dashboard Five-Workspace Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Dashboard around five stable, evidence-backed workspaces while preserving the existing dark terminal style and the current strategy portfolio experience.

**Architecture:** A pure route adapter and reusable workspace shell own navigation and URL compatibility. Three bounded read-only backend resources compose existing research, intelligence, lineage, run-ledger, and systemd evidence into page-specific contracts; three React pages consume only their own resource and lazy-load detail evidence. Strategy trading, model training, factor calculation, simulation, and systemd scheduling remain unchanged.

**Tech Stack:** Python 3 standard library, pandas, existing Stock Analyze domain readers, React 18, TypeScript 5.6, Vite, Vitest, Testing Library, lucide-react, existing CSS tokens, systemd, unittest.

---

## Delivery Boundaries

- The design source of truth is `docs/superpowers/specs/2026-07-30-dashboard-five-workspace-design.md`.
- Do not change factor weights, model gates, selection rules, paper orders, account state, timers, or service execution commands.
- Do not edit `frontend/dashboard/src/PortfolioViews.tsx`; `PortfolioSection`, its grouping, rows, and drawers must remain unchanged.
- Do not expose `claude`, `codex`, or `model_shadow` in canonical browser URLs or visible workspace labels.
- Do not turn the operations page into a service-control surface.
- Do not infer adoption from the mere existence of a factor. Adoption evidence must come from strategy overlays, model feature manifests, decision lineage, or model registries.
- Keep formal overlay factors (`pe`, `low_volatility_60`, and peers) distinct from research feature-registry names (`pe_ttm`, `realized_volatility_20`, and peers); report both evidence sets instead of intersecting them as though they were one namespace.
- Keep all initial tables bounded to 20 rows and all three new resource payloads below 250 KB.
- Treat zero selected factors, zero selected securities, waiting for an upstream task, and a healthy historical backlog as valid states.

## File Map

**Create**

- `frontend/dashboard/src/workspaceRoute.ts` — public URL contract and legacy-route adapter.
- `frontend/dashboard/src/workspaceRoute.test.ts` — pure route compatibility tests.
- `frontend/dashboard/src/useWorkspaceResource.ts` — abortable, stale-preserving resource loader.
- `frontend/dashboard/src/useWorkspaceResource.test.tsx` — loader cancellation and stale-state tests.
- `frontend/dashboard/src/WorkspaceShell.tsx` — stable rail, market/scope control, and header.
- `frontend/dashboard/src/WorkspaceShell.test.tsx` — five-workspace hierarchy tests.
- `frontend/dashboard/src/workspaceTypes.ts` — shared workspace and endpoint contracts.
- `frontend/dashboard/src/WorkspacePrimitives.tsx` — status badge, detail panel, and bounded table.
- `frontend/dashboard/src/StageFlow.tsx` — selectable horizontal/vertical stage flow.
- `frontend/dashboard/src/StageFlow.test.tsx` — stage selection and status semantics.
- `frontend/dashboard/src/ModelResearchPage.tsx` — five-stage research progress page.
- `frontend/dashboard/src/ModelResearchPage.test.tsx` — model page behavior and empty-state tests.
- `frontend/dashboard/src/DataIntelligencePage.tsx` — structured-data and text-intelligence lanes.
- `frontend/dashboard/src/DataIntelligencePage.test.tsx` — node drill-down and usage-matrix tests.
- `frontend/dashboard/src/OperationsPage.tsx` — read-only daily chain, queues, timers, history, and interventions.
- `frontend/dashboard/src/OperationsPage.test.tsx` — operations status, tabs, and detail tests.
- `frontend/dashboard/src/StrategyWorkspacePage.tsx` — existing comparison/detail composition extracted from `App`.
- `frontend/dashboard/src/StrategyWorkspacePage.test.tsx` — request-boundary and portfolio regression tests.
- `stock_analyze/dashboard_workspace_api.py` — model, data/intelligence, and operations page resources.
- `stock_analyze/dashboard_runtime.py` — allowlisted systemd snapshot reader.
- `tests/test_dashboard_workspace_api.py` — backend contracts, evidence, bounds, and payload tests.
- `tests/test_dashboard_runtime.py` — systemd parsing, allowlist, and degradation tests.

**Modify**

- `frontend/dashboard/src/App.tsx` — route state and five-page dispatch only.
- `frontend/dashboard/src/api.ts` — three new fetch functions.
- `frontend/dashboard/src/SystemOverviewPanel.tsx` — canonical route destinations and self-contained overview data.
- `frontend/dashboard/src/SystemOverviewPanel.test.tsx` — no internal-agent navigation.
- `frontend/dashboard/src/IntelligencePanel.tsx` — reusable evidence-ledger display mode.
- `frontend/dashboard/src/IntelligencePanel.test.tsx` — ledger-only rendering.
- `frontend/dashboard/src/styles.css` — workspace shell, flow, detail, and responsive rules using existing tokens.
- `stock_analyze/dashboard_api.py` — add market summaries to the existing system overview only.
- `stock_analyze/cli.py` — allowlist and serve the three new resources.
- `tests/test_cli_dashboard_routes.py` — route smoke tests.
- `scripts/deploy-app-to-ecs.sh` — include the two new Python test modules in the remote gate.
- `scripts/system-audit.sh` — exercise the three new resource canaries.
- `docs/system-harness.md` — endpoint and runtime evidence contract.
- `docs/system-overview.md` — five-workspace navigation and operator interpretation.

### Task 1: Introduce the Public Workspace Route Contract

**Files:**
- Create: `frontend/dashboard/src/workspaceRoute.ts`
- Create: `frontend/dashboard/src/workspaceRoute.test.ts`

- [ ] **Step 1: Write the failing route tests**

```ts
import { describe, expect, it } from "vitest";
import {
  agentForStrategy,
  parseWorkspaceRoute,
  serializeWorkspaceRoute,
} from "./workspaceRoute";

describe("workspace route contract", () => {
  it.each([
    ["?view=compare&market=a_share&agent=codex", {
      view: "strategy", mode: "compare", market: "a_share",
    }],
    ["?view=detail&market=a_share&agent=claude", {
      view: "strategy", mode: "detail", market: "a_share", strategy: "defensive",
    }],
    ["?view=model-shadow&market=cn_qdii_etf&agent=model_shadow", {
      view: "model-research", market: "cn_qdii_etf",
    }],
    ["?view=model-iteration&market=a_share", {
      view: "model-research", market: "a_share",
    }],
    ["?view=intelligence&market=a_share&agent=model_shadow", {
      view: "data-intelligence", market: "a_share",
    }],
  ])("migrates %s", (search, expected) => {
    const route = parseWorkspaceRoute(search);
    expect(route).toEqual(expected);
    expect(serializeWorkspaceRoute(route)).not.toContain("agent=");
  });

  it("normalizes invalid parameters to the decision overview", () => {
    expect(parseWorkspaceRoute("?view=unknown&market=hk&scope=broken")).toEqual({
      view: "system",
    });
  });

  it("keeps operations scope independent from market selection", () => {
    expect(parseWorkspaceRoute("?view=operations&scope=exceptions")).toEqual({
      view: "operations",
      scope: "exceptions",
    });
  });

  it("maps public strategy keys only at the API boundary", () => {
    expect(agentForStrategy("defensive")).toBe("claude");
    expect(agentForStrategy("trend")).toBe("codex");
  });
});
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run:

```bash
cd frontend/dashboard
npm test -- workspaceRoute.test.ts
```

Expected: FAIL with `Cannot find module './workspaceRoute'`.

- [ ] **Step 3: Implement the route parser and serializer**

```ts
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
      mode: "compare" | "detail";
      strategy?: StrategyKey;
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
      params.set("strategy", route.strategy ?? "trend");
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
```

- [ ] **Step 4: Run the route tests**

Run:

```bash
cd frontend/dashboard
npm test -- workspaceRoute.test.ts
```

Expected: PASS, including removal of every legacy `agent` parameter.

- [ ] **Step 5: Commit the route boundary**

```bash
git add frontend/dashboard/src/workspaceRoute.ts frontend/dashboard/src/workspaceRoute.test.ts
git commit -m "feat: add canonical dashboard workspace routes"
```

### Task 2: Add an Abortable, Stale-Preserving Workspace Loader

**Files:**
- Create: `frontend/dashboard/src/useWorkspaceResource.ts`
- Create: `frontend/dashboard/src/useWorkspaceResource.test.tsx`

- [ ] **Step 1: Write the failing loader tests**

```tsx
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useWorkspaceResource } from "./useWorkspaceResource";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe("useWorkspaceResource", () => {
  it("aborts the previous request when the key changes", async () => {
    const calls: AbortSignal[] = [];
    const loader = vi.fn((signal: AbortSignal) => {
      calls.push(signal);
      return Promise.resolve({ value: calls.length });
    });
    const { rerender } = renderHook(
      ({ key }) => useWorkspaceResource(key, true, loader),
      { initialProps: { key: "a_share" } },
    );

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));
    rerender({ key: "cn_qdii_etf" });
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    expect(calls[0].aborted).toBe(true);
  });

  it("keeps the last successful snapshot when refresh fails", async () => {
    const second = deferred<{ value: number }>();
    const loader = vi.fn()
      .mockResolvedValueOnce({ value: 7 })
      .mockImplementationOnce(() => second.promise);
    const { result } = renderHook(() =>
      useWorkspaceResource("a_share", true, loader),
    );

    await waitFor(() => expect(result.current.data).toEqual({ value: 7 }));
    act(() => result.current.refresh());
    second.reject(new Error("runtime unavailable"));
    await waitFor(() => expect(result.current.error).toBe("runtime unavailable"));
    expect(result.current.data).toEqual({ value: 7 });
    expect(result.current.stale).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run:

```bash
cd frontend/dashboard
npm test -- useWorkspaceResource.test.tsx
```

Expected: FAIL with `Cannot find module './useWorkspaceResource'`.

- [ ] **Step 3: Implement the resource loader**

```ts
import { useCallback, useEffect, useRef, useState } from "react";

type Loader<T> = (signal: AbortSignal) => Promise<T>;

type ResourceState<T> = {
  key: string;
  data: T | null;
  loading: boolean;
  error: string | null;
  stale: boolean;
};

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

export function useWorkspaceResource<T>(
  key: string,
  enabled: boolean,
  loader: Loader<T>,
) {
  const [state, setState] = useState<ResourceState<T>>({
    key: "",
    data: null,
    loading: false,
    error: null,
    stale: false,
  });
  const abortRef = useRef<AbortController | null>(null);
  const requestRef = useRef(0);

  const load = useCallback((preserve: boolean) => {
    abortRef.current?.abort();
    if (!enabled) {
      requestRef.current += 1;
      setState({
        key: "",
        data: null,
        loading: false,
        error: null,
        stale: false,
      });
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const requestId = ++requestRef.current;
    setState((current) => ({
      key,
      data: preserve && current.key === key ? current.data : null,
      loading: true,
      error: null,
      stale: false,
    }));
    loader(controller.signal)
      .then((data) => {
        if (requestRef.current === requestId && !controller.signal.aborted) {
          setState({
            key,
            data,
            loading: false,
            error: null,
            stale: false,
          });
        }
      })
      .catch((reason: unknown) => {
        if (requestRef.current === requestId && !controller.signal.aborted) {
          setState((current) => ({
            key,
            data: current.key === key ? current.data : null,
            loading: false,
            error: message(reason),
            stale: current.key === key && current.data !== null,
          }));
        }
      });
  }, [enabled, key, loader]);

  useEffect(() => {
    load(false);
    return () => {
      abortRef.current?.abort();
      requestRef.current += 1;
    };
  }, [load]);

  const active = state.key === key ? state : {
    key,
    data: null,
    loading: false,
    error: null,
    stale: false,
  };
  return {
    ...active,
    refresh: useCallback(() => load(true), [load]),
  };
}
```

- [ ] **Step 4: Run the loader tests**

Run:

```bash
cd frontend/dashboard
npm test -- useWorkspaceResource.test.tsx
```

Expected: PASS with both cancellation and stale-data assertions.

- [ ] **Step 5: Commit the loader**

```bash
git add frontend/dashboard/src/useWorkspaceResource.ts frontend/dashboard/src/useWorkspaceResource.test.tsx
git commit -m "feat: add resilient dashboard workspace loader"
```

### Task 3: Build the Stable Five-Workspace Shell

**Files:**
- Create: `frontend/dashboard/src/WorkspaceShell.tsx`
- Create: `frontend/dashboard/src/WorkspaceShell.test.tsx`
- Modify: `frontend/dashboard/src/styles.css`

- [ ] **Step 1: Write the failing shell tests**

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceShell } from "./WorkspaceShell";

describe("WorkspaceShell", () => {
  it("renders five stable top-level workspaces and the strategy hierarchy", () => {
    render(
      <WorkspaceShell
        route={{ view: "strategy", mode: "detail", market: "a_share", strategy: "defensive" }}
        marketContext="a_share"
        title="稳健防守"
        subtitle="A股"
        busy={false}
        autoRefresh
        onNavigate={vi.fn()}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );
    const nav = screen.getByRole("navigation", { name: "工作区" });
    expect(within(nav).getByText("决策总览")).toBeInTheDocument();
    expect(within(nav).getByText("策略工作台")).toBeInTheDocument();
    expect(within(nav).getByText("模型研究")).toBeInTheDocument();
    expect(within(nav).getByText("数据与情报")).toBeInTheDocument();
    expect(within(nav).getByText("运行中心")).toBeInTheDocument();
    expect(within(nav).getByText("策略对比")).toBeInTheDocument();
    expect(within(nav).getByText("单策略分析")).toBeInTheDocument();
    expect(within(nav).getByText("稳健防守")).toBeInTheDocument();
    expect(within(nav).getByText("趋势进攻")).toBeInTheDocument();
  });

  it("keeps the scope control in the same rail slot", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShell
        route={{ view: "operations", scope: "all" }}
        marketContext="cn_qdii_etf"
        title="运行中心"
        subtitle="全部市场"
        busy={false}
        autoRefresh
        onNavigate={onNavigate}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );
    const scope = screen.getByRole("navigation", { name: "市场范围" });
    await user.click(within(scope).getByRole("button", { name: "仅异常" }));
    expect(onNavigate).toHaveBeenCalledWith({
      view: "operations",
      scope: "exceptions",
    });
  });

  it("preserves the selected market when switching workspaces", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShell
        route={{ view: "strategy", mode: "compare", market: "a_share" }}
        marketContext="a_share"
        title="策略对比"
        subtitle="A股"
        busy={false}
        autoRefresh
        onNavigate={onNavigate}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );
    await user.click(screen.getByRole("button", { name: "模型研究" }));
    expect(onNavigate).toHaveBeenCalledWith({
      view: "model-research",
      market: "a_share",
    });
  });
});
```

- [ ] **Step 2: Run the shell test and verify the missing module failure**

Run:

```bash
cd frontend/dashboard
npm test -- WorkspaceShell.test.tsx
```

Expected: FAIL with `Cannot find module './WorkspaceShell'`.

- [ ] **Step 3: Implement the shell and navigation tree**

```tsx
import type { ReactNode } from "react";
import {
  Activity,
  BarChart3,
  BrainCircuit,
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
  market: DashboardMarket,
): WorkspaceRoute {
  if (view === "system") return { view: "system" };
  if (view === "strategy") {
    return { view: "strategy", mode: "compare", market };
  }
  if (view === "model-research") {
    return { view, market };
  }
  if (view === "data-intelligence") {
    return { view, market };
  }
  return { view: "operations", scope: "all" };
}

function marketRoute(route: WorkspaceRoute, market: DashboardMarket): WorkspaceRoute {
  if (route.view === "system") {
    return { view: "strategy", mode: "compare", market };
  }
  if (route.view === "operations") {
    return { view: "operations", scope: market };
  }
  return { ...route, market };
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
  const activeMarket = route.view === "strategy"
    || route.view === "model-research"
    || route.view === "data-intelligence"
    ? route.market
    : null;
  return (
    <main className="app-shell">
      <aside className="left-rail">
        <div className="brand-lockup">
          <span><Gauge size={18} aria-hidden="true" /></span>
          <div><strong>Stock Analyze</strong><p>国内投资模拟终端</p></div>
        </div>

        <nav className="control-group workspace-scope-slot" aria-label="市场范围">
          <label>{route.view === "operations" ? "运行范围" : "投资市场"}</label>
          <div className="segmented">
            {route.view === "operations" ? (
              <>
                <button type="button" className={route.scope === "all" ? "active" : ""} onClick={() => onNavigate({ view: "operations", scope: "all" })}>全部</button>
                <button type="button" className={route.scope === "a_share" ? "active" : ""} onClick={() => onNavigate({ view: "operations", scope: "a_share" })}>A股</button>
                <button type="button" className={route.scope === "cn_qdii_etf" ? "active" : ""} onClick={() => onNavigate({ view: "operations", scope: "cn_qdii_etf" })}>跨境ETF</button>
                <button type="button" className={route.scope === "exceptions" ? "active" : ""} onClick={() => onNavigate({ view: "operations", scope: "exceptions" })}>仅异常</button>
              </>
            ) : dashboardMarkets.map((market) => (
              <button
                key={market}
                type="button"
                className={activeMarket === market ? "active" : ""}
                onClick={() => onNavigate(marketRoute(route, market))}
              >
                {marketLabels[market]}
              </button>
            ))}
          </div>
        </nav>

        <nav className="rail-analysis-nav" aria-label="工作区">
          <span className="rail-nav-label">工作区</span>
          <div className="rail-nav-list">
            <button type="button" className={route.view === "system" ? "rail-nav-item active" : "rail-nav-item"} onClick={() => onNavigate(defaultRoute("system", marketContext))}>
              <Workflow size={17} aria-hidden="true" /><strong>决策总览</strong>
            </button>
            <div className={strategyOpen ? "rail-menu-branch active" : "rail-menu-branch"}>
              <button type="button" className={strategyOpen ? "rail-nav-item active" : "rail-nav-item"} aria-expanded={strategyOpen} onClick={() => onNavigate(defaultRoute("strategy", marketContext))}>
                <BarChart3 size={17} aria-hidden="true" /><strong>策略工作台</strong>
              </button>
              {strategyOpen ? (
                <div className="rail-workspace-children">
                  <button type="button" className={route.mode === "compare" ? "rail-sub-item active" : "rail-sub-item"} onClick={() => onNavigate({ view: "strategy", mode: "compare", market: route.market })}>
                    <GitCompareArrows size={15} aria-hidden="true" />策略对比
                  </button>
                  <span className="rail-sub-heading"><Layers3 size={15} aria-hidden="true" />单策略分析</span>
                  <button type="button" className={route.mode === "detail" && route.strategy === "defensive" ? "rail-strategy-item active" : "rail-strategy-item"} onClick={() => onNavigate({ view: "strategy", mode: "detail", market: route.market, strategy: "defensive" })}>
                    <ShieldCheck size={15} aria-hidden="true" />稳健防守
                  </button>
                  <button type="button" className={route.mode === "detail" && route.strategy === "trend" ? "rail-strategy-item active" : "rail-strategy-item"} onClick={() => onNavigate({ view: "strategy", mode: "detail", market: route.market, strategy: "trend" })}>
                    <TrendingUp size={15} aria-hidden="true" />趋势进攻
                  </button>
                </div>
              ) : null}
            </div>
            <button type="button" className={route.view === "model-research" ? "rail-nav-item active" : "rail-nav-item"} onClick={() => onNavigate(defaultRoute("model-research", marketContext))}>
              <BrainCircuit size={17} aria-hidden="true" /><strong>模型研究</strong>
            </button>
            <button type="button" className={route.view === "data-intelligence" ? "rail-nav-item active" : "rail-nav-item"} onClick={() => onNavigate(defaultRoute("data-intelligence", marketContext))}>
              <RadioTower size={17} aria-hidden="true" /><strong>数据与情报</strong>
            </button>
            <button type="button" className={route.view === "operations" ? "rail-nav-item active" : "rail-nav-item"} onClick={() => onNavigate(defaultRoute("operations", marketContext))}>
              <Activity size={17} aria-hidden="true" /><strong>运行中心</strong>
            </button>
          </div>
        </nav>

        <div className="status-stack">{railStatus}</div>
        <button className="ghost-button" type="button" onClick={onToggleAutoRefresh} aria-pressed={autoRefresh}>
          <Activity size={16} aria-hidden="true" />{autoRefresh ? "自动刷新已开启" : "自动刷新已关闭"}
        </button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div><p>{subtitle}</p><h1>{title}</h1></div>
          <div className="topbar-actions">
            {headerActions}
            <button className="icon-text-button" type="button" onClick={onRefresh} aria-label="刷新 dashboard" aria-busy={busy}>
              <RefreshCcw className={busy ? "spin" : ""} size={16} aria-hidden="true" />刷新
            </button>
          </div>
        </header>
        {children}
      </section>
    </main>
  );
}
```

- [ ] **Step 4: Add only the shell layout rules needed by the tests**

```css
.workspace-scope-slot {
  min-height: 104px;
}

.rail-workspace-children {
  display: grid;
  gap: 4px;
  padding: 4px 0 8px 30px;
}

.rail-sub-item,
.rail-strategy-item {
  min-width: 0;
  min-height: 34px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: var(--radius);
  background: transparent;
  color: var(--muted);
  text-align: left;
}

.rail-sub-item.active,
.rail-strategy-item.active {
  border-color: var(--line-strong);
  background: var(--panel-3);
  color: var(--text);
}

.rail-sub-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  color: var(--faint);
  font-size: 12px;
}
```

- [ ] **Step 5: Run the shell tests**

Run:

```bash
cd frontend/dashboard
npm test -- WorkspaceShell.test.tsx
```

Expected: PASS with all five top-level workspaces and stable scope controls.

- [ ] **Step 6: Commit the shell**

```bash
git add frontend/dashboard/src/WorkspaceShell.tsx frontend/dashboard/src/WorkspaceShell.test.tsx frontend/dashboard/src/styles.css
git commit -m "feat: add stable dashboard workspace shell"
```

### Task 4: Add Shared Status, Flow, Detail, and Table Primitives

**Files:**
- Create: `frontend/dashboard/src/workspaceTypes.ts`
- Create: `frontend/dashboard/src/WorkspacePrimitives.tsx`
- Create: `frontend/dashboard/src/StageFlow.tsx`
- Create: `frontend/dashboard/src/StageFlow.test.tsx`

- [ ] **Step 1: Write the failing stage-flow test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StageFlow } from "./StageFlow";

describe("StageFlow", () => {
  it("selects a stage without treating a research state as a failure", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <StageFlow
        ariaLabel="模型研究进度"
        selectedKey="training"
        onSelect={onSelect}
        stages={[
          { key: "data", label: "数据准备", status: "success", primary: "6 个来源", secondary: "72 个特征" },
          { key: "training", label: "模型训练", status: "research", primary: "4 个版本", secondary: "最近训练 07-30" },
        ]}
      />,
    );
    expect(screen.getByText("研究中")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /数据准备/ }));
    expect(onSelect).toHaveBeenCalledWith("data");
  });
});
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run:

```bash
cd frontend/dashboard
npm test -- StageFlow.test.tsx
```

Expected: FAIL with `Cannot find module './StageFlow'`.

- [ ] **Step 3: Define shared workspace contracts**

```ts
export type WorkspaceStatus =
  | "success"
  | "running"
  | "waiting_schedule"
  | "waiting_upstream"
  | "failed"
  | "skipped"
  | "research"
  | "empty"
  | "unavailable";

export type WorkspaceStage = {
  key: string;
  label: string;
  status: WorkspaceStatus;
  primary: string;
  secondary: string;
  updatedAt?: string | null;
  issues?: string[];
};

export type WorkspaceMetric = {
  label: string;
  value: string;
  tone?: "default" | "positive" | "negative" | "warning";
};

export type BoundedColumn<T> = {
  key: string;
  label: string;
  render: (row: T) => string;
};
```

- [ ] **Step 4: Implement the reusable primitives**

```tsx
import type { ReactNode } from "react";
import {
  CheckCircle2,
  Clock3,
  FlaskConical,
  PauseCircle,
  PlayCircle,
  ShieldAlert,
  SkipForward,
  X,
} from "lucide-react";
import type {
  BoundedColumn,
  WorkspaceStatus,
} from "./workspaceTypes";

const statusMeta: Record<WorkspaceStatus, {
  label: string;
  tone: string;
  icon: typeof CheckCircle2;
}> = {
  success: { label: "成功", tone: "ok", icon: CheckCircle2 },
  running: { label: "运行中", tone: "active", icon: PlayCircle },
  waiting_schedule: { label: "等待计划时间", tone: "muted", icon: Clock3 },
  waiting_upstream: { label: "等待上游", tone: "muted", icon: PauseCircle },
  failed: { label: "失败", tone: "warn", icon: ShieldAlert },
  skipped: { label: "已跳过", tone: "muted", icon: SkipForward },
  research: { label: "研究中", tone: "research", icon: FlaskConical },
  empty: { label: "暂无数据", tone: "muted", icon: Clock3 },
  unavailable: { label: "状态不可用", tone: "warn", icon: ShieldAlert },
};

export function WorkspaceStatusBadge({ status }: { status: WorkspaceStatus }) {
  const meta = statusMeta[status];
  const Icon = meta.icon;
  return (
    <span className={`workspace-status status-${meta.tone}`}>
      <Icon size={14} aria-hidden="true" />{meta.label}
    </span>
  );
}

export function DetailPanel({
  title,
  status,
  updatedAt,
  onClose,
  children,
}: {
  title: string;
  status: WorkspaceStatus;
  updatedAt?: string | null;
  onClose?: () => void;
  children: ReactNode;
}) {
  return (
    <section className="workspace-detail-panel" aria-label={`${title}详情`}>
      <header>
        <div><h2>{title}</h2><WorkspaceStatusBadge status={status} /></div>
        <div>
          {updatedAt ? <time>{updatedAt.replace("T", " ")}</time> : null}
          {onClose ? <button type="button" onClick={onClose} aria-label={`关闭${title}详情`}><X size={16} /></button> : null}
        </div>
      </header>
      <div className="workspace-detail-body">{children}</div>
    </section>
  );
}

export function BoundedTable<T>({
  rows,
  columns,
  rowKey,
  emptyLabel,
}: {
  rows: T[];
  columns: BoundedColumn<T>[];
  rowKey: (row: T) => string;
  emptyLabel: string;
}) {
  const bounded = rows.slice(0, 20);
  return (
    <div className="bounded-table-wrap">
      <table className="bounded-table">
        <thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
        <tbody>
          {bounded.length === 0 ? (
            <tr><td colSpan={columns.length} className="empty-cell">{emptyLabel}</td></tr>
          ) : bounded.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column) => <td key={column.key}>{column.render(row)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 5: Implement the selectable flow**

```tsx
import { ArrowRight } from "lucide-react";
import { WorkspaceStatusBadge } from "./WorkspacePrimitives";
import type { WorkspaceStage } from "./workspaceTypes";

export function StageFlow({
  stages,
  selectedKey,
  ariaLabel,
  onSelect,
}: {
  stages: WorkspaceStage[];
  selectedKey: string;
  ariaLabel: string;
  onSelect: (key: string) => void;
}) {
  return (
    <div className="stage-flow" role="group" aria-label={ariaLabel}>
      {stages.map((stage, index) => (
        <div className="stage-flow-item" key={stage.key}>
          <button
            type="button"
            className={selectedKey === stage.key ? "stage-node active" : "stage-node"}
            aria-pressed={selectedKey === stage.key}
            aria-label={`${stage.label} ${stage.primary}`}
            onClick={() => onSelect(stage.key)}
          >
            <span className="stage-index">{String(index + 1).padStart(2, "0")}</span>
            <strong>{stage.label}</strong>
            <WorkspaceStatusBadge status={stage.status} />
            <b>{stage.primary}</b>
            <small>{stage.secondary}</small>
          </button>
          {index < stages.length - 1 ? <ArrowRight className="stage-link" size={17} aria-hidden="true" /> : null}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 6: Run the primitive tests**

Run:

```bash
cd frontend/dashboard
npm test -- StageFlow.test.tsx
```

Expected: PASS and the research state is rendered as `研究中`.

- [ ] **Step 7: Commit the shared primitives**

```bash
git add frontend/dashboard/src/workspaceTypes.ts frontend/dashboard/src/WorkspacePrimitives.tsx frontend/dashboard/src/StageFlow.tsx frontend/dashboard/src/StageFlow.test.tsx
git commit -m "feat: add dashboard workspace flow primitives"
```

### Task 5: Add the Bounded Model Research Resource

**Files:**
- Create: `stock_analyze/dashboard_workspace_api.py`
- Create: `tests/test_dashboard_workspace_api.py`
- Modify: `stock_analyze/cli.py:2305-2560`
- Modify: `tests/test_cli_dashboard_routes.py:86-106`

- [ ] **Step 1: Write the failing model-resource contract test**

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stock_analyze.dashboard_workspace_api import (
    build_dashboard_model_research_data,
)


class DashboardWorkspaceApiTests(unittest.TestCase):
    def test_model_research_reports_five_evidence_backed_stages(self) -> None:
        models = {
            "status": "available",
            "models": [
                {
                    "model_version": "A20-V005",
                    "horizon": 20,
                    "sample_support": 4200,
                    "feature_columns": [
                        "momentum_20",
                        "event_net_strength_5d",
                    ],
                    "trained_at": "2026-07-29T23:00:00",
                    "metrics": {
                        "candidate_feature_count": 72,
                        "point_in_time_audit": True,
                        "rank_ic": 0.021,
                        "brier_score": 0.61,
                    },
                    "gate_passed": False,
                    "gate_reasons": ["rank_ic_below_floor"],
                    "shadow_cycles": 0,
                    "shadow_cycles_remaining": 12,
                    "is_champion": False,
                }
            ],
        }
        iteration = {
            "status": "available",
            "candidate": {
                "model_version": "A20-V005",
                "display_version": "A20-V005",
                "shadow_cycles": 0,
                "shadow_cycles_remaining": 12,
            },
            "champion": None,
            "candidate_rows": 31,
            "eligible_rows": 0,
            "selected_count": 0,
            "cash_only": True,
            "cash_reason": "probability_gate_not_met",
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            return_value=models,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value=iteration,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_research_source_health",
            return_value=[{"source": "market", "status": "available", "rows": 1000}],
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value=[],
        ):
            payload = build_dashboard_model_research_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        self.assertEqual(
            [item["key"] for item in payload["stages"]],
            ["data", "training", "validation", "simulation", "adoption"],
        )
        self.assertEqual(payload["dataPreparation"]["candidateFeatureCount"], 72)
        self.assertEqual(payload["dataPreparation"]["intelligenceFeatureCount"], 1)
        self.assertEqual(payload["validation"]["passed"], 0)
        self.assertEqual(
            payload["validation"]["models"][0]["gateReasons"],
            ["rank_ic_below_floor"],
        )
        self.assertEqual(payload["simulation"]["decision"]["selectedCount"], 0)
        self.assertEqual(payload["adoption"]["champions"], [])
        self.assertEqual(payload["adoption"]["rollbackCandidates"], [])
        self.assertLess(
            len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            250_000,
        )
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run:

```bash
python3 -m unittest tests.test_dashboard_workspace_api -v
```

Expected: FAIL with `ModuleNotFoundError: stock_analyze.dashboard_workspace_api`.

- [ ] **Step 3: Implement the model resource helpers and builder**

```python
"""Bounded resources for the five-workspace React dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import competition
from . import dashboard_aggregator as agg
from .dashboard_api import _latest_strategy_model_usage
from .research.feature_registry import INTELLIGENCE_FEATURES


MODEL_METRIC_KEYS = (
    "rank_ic",
    "mean_rank_ic",
    "icir",
    "brier_score",
    "auc",
    "hit_rate_lift",
    "net_excess_return",
    "turnover",
)


def _generated_at() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _root(repo_root: str | Path | None) -> Path:
    return Path(repo_root) if repo_root is not None else Path.cwd()


def _check_market(market: str) -> None:
    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)


def _algorithm_family(model: dict[str, Any]) -> str:
    explicit = str(
        model.get("algorithm_family")
        or model.get("model_family")
        or ""
    ).strip()
    if explicit:
        return explicit
    return "boosting_ensemble" if model.get("use_boosting") else "multinomial_logit"


def _model_registry_record(
    root: Path,
    market: str,
    horizon: int,
    model_version: str,
) -> dict[str, Any]:
    model_root = root / "data" / "research" / "models" / market / str(horizon)
    registry_path = model_root / "registry.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        registry = {}
    models = registry.get("models")
    models = models if isinstance(models, dict) else {}
    record = models.get(model_version)
    return record if isinstance(record, dict) else {}


def _model_artifact_ref(
    root: Path,
    market: str,
    horizon: int,
    model_version: str,
    registry_record: dict[str, Any],
) -> str | None:
    model_root = root / "data" / "research" / "models" / market / str(horizon)
    registered = str(registry_record.get("artifact") or "").strip()
    if registered:
        candidate = Path(registered)
        try:
            return str(candidate.relative_to(root))
        except ValueError:
            return candidate.name
    matches = sorted(model_root.glob(f"*-{model_version}.joblib"))
    if not matches:
        return None
    try:
        return str(matches[-1].relative_to(root))
    except ValueError:
        return matches[-1].name


def _model_rows(root: Path, market: str) -> list[dict[str, Any]]:
    health = agg._read_model_health(root, market)
    rows: list[dict[str, Any]] = []
    for raw in health.get("models") or []:
        metrics = raw.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        feature_columns = sorted(
            {str(value) for value in raw.get("feature_columns") or [] if value}
        )
        horizon = int(raw.get("horizon") or 0)
        model_version = str(raw.get("model_version") or "")
        registry_record = _model_registry_record(
            root,
            market,
            horizon,
            model_version,
        )
        artifact_ref = _model_artifact_ref(
            root,
            market,
            horizon,
            model_version,
            registry_record,
        )
        rows.append(
            {
                "modelVersion": model_version,
                "horizon": horizon,
                "algorithmFamily": _algorithm_family(raw),
                "trainedAt": (
                    raw.get("trained_at")
                    or raw.get("created_at")
                    or registry_record.get("registered_at")
                ),
                "sampleSupport": int(raw.get("sample_support") or 0),
                "featureColumns": feature_columns,
                "artifactRef": artifact_ref,
                "artifactStatus": "available" if artifact_ref else "missing",
                "gatePassed": raw.get("gate_passed") is True,
                "gateReasons": list(raw.get("gate_reasons") or []),
                "shadowCycles": int(raw.get("shadow_cycles") or 0),
                "shadowCyclesRemaining": int(
                    raw.get("shadow_cycles_remaining") or 0
                ),
                "isChampion": raw.get("is_champion") is True,
                "pointInTimeAudit": metrics.get("point_in_time_audit"),
                "candidateFeatureCount": int(
                    metrics.get("candidate_feature_count")
                    or len(feature_columns)
                ),
                "metrics": {
                    key: metrics.get(key)
                    for key in MODEL_METRIC_KEYS
                    if key in metrics
                },
            }
        )
    return sorted(rows, key=lambda row: (row["horizon"], row["modelVersion"]))


def build_dashboard_model_research_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
) -> dict[str, Any]:
    _check_market(market)
    root = _root(repo_root)
    models = _model_rows(root, market)
    selected_features = sorted(
        {
            feature
            for model in models
            for feature in model["featureColumns"]
        }
    )
    intelligence_names = {item.name for item in INTELLIGENCE_FEATURES}
    intelligence_features = sorted(set(selected_features) & intelligence_names)
    source_health = agg._read_research_source_health(root, market)
    iteration = agg._read_model_iteration_status(root, market)
    usage = [
        row
        for row in _latest_strategy_model_usage(root)
        if row.get("market") == market
    ]
    champions = [
        {
            "modelVersion": row["modelVersion"],
            "horizon": row["horizon"],
            "trainedAt": row["trainedAt"],
        }
        for row in models
        if row["isChampion"]
    ]
    rollback_candidates = [
        {
            "modelVersion": str(row.get("model_version") or ""),
            "displayVersion": str(
                row.get("display_version")
                or row.get("model_version")
                or ""
            ),
            "outcome": str(row.get("outcome") or ""),
            "endedAt": row.get("ended_at"),
        }
        for row in reversed(iteration.get("version_history") or [])
        if row.get("model_version")
    ][:5]
    candidate = iteration.get("candidate") or {}
    required_cycles = int(candidate.get("shadow_cycles") or 0) + int(
        candidate.get("shadow_cycles_remaining") or 0
    )
    passed = sum(1 for row in models if row["gatePassed"])
    candidate_count = max(
        (row["candidateFeatureCount"] for row in models),
        default=0,
    )
    audited = [
        row["pointInTimeAudit"]
        for row in models
        if row["pointInTimeAudit"] is not None
    ]
    point_in_time_status = (
        "passed"
        if audited and all(value is True for value in audited)
        else "failed"
        if audited
        else "unavailable"
    )
    active_usage = sum(1 for row in usage if row.get("status") == "active")
    stages = [
        {
            "key": "data",
            "label": "数据准备",
            "status": "success" if selected_features else "unavailable",
            "primary": f"{len(selected_features)} 个已选特征",
            "secondary": f"{len(source_health)} 个来源状态",
        },
        {
            "key": "training",
            "label": "模型训练",
            "status": "success" if models else "empty",
            "primary": f"{len(models)} 个研究版本",
            "secondary": f"{sum(row['sampleSupport'] for row in models)} 条样本支持",
        },
        {
            "key": "validation",
            "label": "测试验收",
            "status": "success" if passed else "research",
            "primary": f"{passed} / {len(models)} 通过",
            "secondary": f"{sum(len(row['gateReasons']) for row in models)} 个阻塞项",
        },
        {
            "key": "simulation",
            "label": "模拟运行",
            "status": "running" if candidate else "waiting_upstream",
            "primary": str(candidate.get("display_version") or "等待候选"),
            "secondary": (
                f"{int(candidate.get('shadow_cycles') or 0)} / "
                f"{required_cycles or 12} 个观察周期"
            ),
        },
        {
            "key": "adoption",
            "label": "正式采用",
            "status": "success" if champions and active_usage else "waiting_upstream",
            "primary": f"{len(champions)} 个 Champion",
            "secondary": f"{active_usage} 个正式策略账户已采用",
        },
    ]
    return agg._json_safe(
        {
            "generated_at": _generated_at(),
            "market": market,
            "market_label": agg.MARKET_LABELS.get(market, market),
            "stages": stages,
            "dataPreparation": {
                "sources": source_health,
                "candidateFeatureCount": candidate_count,
                "selectedFeatureCount": len(selected_features),
                "structuredFeatureCount": (
                    len(selected_features) - len(intelligence_features)
                ),
                "intelligenceFeatureCount": len(intelligence_features),
                "selectedFeatures": selected_features,
                "pointInTimeAudit": point_in_time_status,
                "gaps": [
                    str(row.get("source"))
                    for row in source_health
                    if row.get("failed")
                    or row.get("status") in {"source_unavailable", "failed"}
                ],
            },
            "training": {"models": models},
            "validation": {
                "passed": passed,
                "total": len(models),
                "models": models,
            },
            "simulation": {
                "status": iteration.get("status") or "unavailable",
                "candidate": candidate or None,
                "predictionAsOf": iteration.get("prediction_as_of"),
                "predictionStatus": (
                    "available"
                    if iteration.get("prediction_as_of")
                    else "missing"
                ),
                "cyclesCompleted": int(candidate.get("shadow_cycles") or 0),
                "cyclesRequired": required_cycles or 12,
                "decision": {
                    "candidateRows": int(iteration.get("candidate_rows") or 0),
                    "eligibleRows": int(iteration.get("eligible_rows") or 0),
                    "selectedCount": int(iteration.get("selected_count") or 0),
                    "tradesExecuted": int(iteration.get("trades_executed") or 0),
                    "pendingOrders": int(iteration.get("pending_orders") or 0),
                    "cashOnly": bool(iteration.get("cash_only")),
                    "cashReason": iteration.get("cash_reason"),
                    "diagnostics": iteration.get("decision_diagnostics"),
                },
            },
            "adoption": {
                "champions": champions,
                "rollbackCandidates": rollback_candidates,
                "strategyUsage": usage,
            },
        }
    )
```

- [ ] **Step 4: Register the endpoint in the HTTP allowlist and handler**

Add the path:

```python
"/api/dashboard/model-research.json",
```

Import and dispatch it inside `_serve_dashboard_api`:

```python
from .dashboard_workspace_api import (
    build_dashboard_model_research_data,
)

if canonical_path == "/api/dashboard/model-research.json":
    return build_dashboard_model_research_data(
        repo_root=repo_root,
        market=market,
    )
```

Extend the route smoke loop:

```python
for resource in (
    "system-overview",
    "model-research",
    "overview",
    "performance",
    "portfolio",
    "predictions",
    "research",
    "operations",
):
    self.assertTrue(_is_dashboard_api_path(f"/api/dashboard/{resource}.json"))
```

- [ ] **Step 5: Run the backend tests**

Run:

```bash
python3 -m unittest tests.test_dashboard_workspace_api tests.test_cli_dashboard_routes -v
```

Expected: PASS with the five stages, explicit gate reasons, valid zero-selection state, route, and payload bound.

- [ ] **Step 6: Commit the model resource**

```bash
git add stock_analyze/dashboard_workspace_api.py tests/test_dashboard_workspace_api.py stock_analyze/cli.py tests/test_cli_dashboard_routes.py
git commit -m "feat: add bounded model research dashboard resource"
```

### Task 6: Build the Model Research Page

**Files:**
- Modify: `frontend/dashboard/src/workspaceTypes.ts`
- Modify: `frontend/dashboard/src/api.ts`
- Create: `frontend/dashboard/src/ModelResearchPage.tsx`
- Create: `frontend/dashboard/src/ModelResearchPage.test.tsx`

- [ ] **Step 1: Add the failing model-page tests**

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ModelResearchPage } from "./ModelResearchPage";

vi.mock("./api", () => ({
  fetchModelResearch: vi.fn().mockResolvedValue({
    generated_at: "2026-07-30T13:00:00",
    market: "a_share",
    market_label: "A股",
    stages: [
      { key: "data", label: "数据准备", status: "success", primary: "48 个已选特征", secondary: "6 个来源状态" },
      { key: "training", label: "模型训练", status: "success", primary: "4 个研究版本", secondary: "16800 条样本支持" },
      { key: "validation", label: "测试验收", status: "research", primary: "0 / 4 通过", secondary: "7 个阻塞项" },
      { key: "simulation", label: "模拟运行", status: "running", primary: "A20-V005", secondary: "0 / 12 个观察周期" },
      { key: "adoption", label: "正式采用", status: "waiting_upstream", primary: "0 个 Champion", secondary: "0 个正式策略账户已采用" },
    ],
    dataPreparation: {
      sources: [{ source: "market", status: "available", rows: 1000 }],
      candidateFeatureCount: 72,
      selectedFeatureCount: 48,
      structuredFeatureCount: 47,
      intelligenceFeatureCount: 1,
      selectedFeatures: ["momentum_20", "event_net_strength_5d"],
      pointInTimeAudit: "passed",
      gaps: [],
    },
    training: { models: [] },
    validation: {
      passed: 0,
      total: 4,
      models: [{
        modelVersion: "A20-V005",
        horizon: 20,
        algorithmFamily: "boosting_ensemble",
        trainedAt: "2026-07-29T23:00:00",
        sampleSupport: 4200,
        featureColumns: ["momentum_20"],
        artifactRef: "data/research/models/a_share/20/run-A20-V005.joblib",
        artifactStatus: "available",
        gatePassed: false,
        gateReasons: ["rank_ic_below_floor"],
        shadowCycles: 0,
        shadowCyclesRemaining: 12,
        isChampion: false,
        pointInTimeAudit: true,
        candidateFeatureCount: 72,
        metrics: { rank_ic: 0.021 },
      }],
    },
    simulation: {
      status: "available",
      candidate: { display_version: "A20-V005" },
      predictionAsOf: null,
      predictionStatus: "missing",
      cyclesCompleted: 0,
      cyclesRequired: 12,
      decision: {
        candidateRows: 31,
        eligibleRows: 0,
        selectedCount: 0,
        tradesExecuted: 0,
        pendingOrders: 0,
        cashOnly: true,
        cashReason: "probability_gate_not_met",
        diagnostics: null,
      },
    },
    adoption: { champions: [], rollbackCandidates: [], strategyUsage: [] },
  }),
}));

describe("ModelResearchPage", () => {
  it("shows actual stage state and gate failures", async () => {
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);
    expect(await screen.findByText("0 / 4 通过")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /测试验收/ }));
    const detail = screen.getByRole("region", { name: "测试验收详情" });
    expect(within(detail).getByText("A20-V005")).toBeInTheDocument();
    expect(within(detail).getByText(/rank_ic_below_floor/)).toBeInTheDocument();
  });

  it("shows zero selected securities as a decision, not an error", async () => {
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);
    await screen.findByText("A20-V005");
    await user.click(screen.getByRole("button", { name: /模拟运行/ }));
    expect(screen.getByText("0 个入选")).toBeInTheDocument();
    expect(screen.getByText("probability_gate_not_met")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the page test and verify the missing export failure**

Run:

```bash
cd frontend/dashboard
npm test -- ModelResearchPage.test.tsx
```

Expected: FAIL because `ModelResearchPage` and `fetchModelResearch` do not exist.

- [ ] **Step 3: Add the model resource types**

Append exact contracts to `workspaceTypes.ts`:

```ts
export type ModelResearchModel = {
  modelVersion: string;
  horizon: number;
  algorithmFamily: string;
  trainedAt?: string | null;
  sampleSupport: number;
  featureColumns: string[];
  artifactRef?: string | null;
  artifactStatus: string;
  gatePassed: boolean;
  gateReasons: string[];
  shadowCycles: number;
  shadowCyclesRemaining: number;
  isChampion: boolean;
  pointInTimeAudit?: boolean | null;
  candidateFeatureCount: number;
  metrics: Record<string, number | string | boolean | null>;
};

export type ModelResearchData = {
  generated_at: string;
  market: string;
  market_label: string;
  stages: WorkspaceStage[];
  dataPreparation: {
    sources: { source: string; status: string; rows?: number; failed?: boolean; error?: string }[];
    candidateFeatureCount: number;
    selectedFeatureCount: number;
    structuredFeatureCount: number;
    intelligenceFeatureCount: number;
    selectedFeatures: string[];
    pointInTimeAudit: string;
    gaps: string[];
  };
  training: { models: ModelResearchModel[] };
  validation: { passed: number; total: number; models: ModelResearchModel[] };
  simulation: {
    status: string;
    candidate: Record<string, unknown> | null;
    predictionAsOf?: string | null;
    predictionStatus: string;
    cyclesCompleted: number;
    cyclesRequired: number;
    decision: {
      candidateRows: number;
      eligibleRows: number;
      selectedCount: number;
      tradesExecuted: number;
      pendingOrders: number;
      cashOnly: boolean;
      cashReason?: string | null;
      diagnostics?: Record<string, unknown> | null;
    };
  };
  adoption: {
    champions: { modelVersion: string; horizon: number; trainedAt?: string | null }[];
    rollbackCandidates: {
      modelVersion: string;
      displayVersion: string;
      outcome: string;
      endedAt?: string | null;
    }[];
    strategyUsage: {
      strategy_label: string;
      as_of?: string | null;
      status: string;
      applied_candidates: number;
      candidate_coverage: number;
      model_versions: Record<string, string>;
      fallback_reason: string;
      accounts?: number;
    }[];
  };
};
```

- [ ] **Step 4: Add the bounded fetch function**

```ts
import type { ModelResearchData } from "./workspaceTypes";

export function fetchModelResearch(
  market: string,
  signal?: AbortSignal,
): Promise<ModelResearchData> {
  const params = new URLSearchParams({ market });
  return fetchJson<ModelResearchData>(
    `/api/dashboard/model-research.json?${params.toString()}`,
    signal,
  );
}
```

- [ ] **Step 5: Implement the five-stage page**

```tsx
import { useCallback, useEffect, useState } from "react";
import { fetchModelResearch } from "./api";
import { StageFlow } from "./StageFlow";
import {
  BoundedTable,
  DetailPanel,
} from "./WorkspacePrimitives";
import { useWorkspaceResource } from "./useWorkspaceResource";
import type {
  ModelResearchData,
  ModelResearchModel,
} from "./workspaceTypes";

function value(value: unknown): string {
  if (value == null || value === "") return "-";
  if (typeof value === "number") return value.toFixed(4);
  return String(value);
}

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    rank_ic_below_floor: "Rank IC 未达到门槛",
    icir_below_floor: "ICIR 稳定性未达到门槛",
    brier_above_ceiling: "概率校准误差超过上限",
    probability_gate_not_met: "上涨概率未达到入选门槛",
  };
  return labels[reason] ? `${labels[reason]}（${reason}）` : reason;
}

export function ModelResearchPage({
  market,
  refreshToken,
}: {
  market: string;
  refreshToken: number;
}) {
  const loader = useCallback(
    (signal: AbortSignal) => fetchModelResearch(market, signal),
    [market],
  );
  const resource = useWorkspaceResource<ModelResearchData>(
    market,
    true,
    loader,
  );
  const [selected, setSelected] = useState("data");
  useEffect(() => {
    setSelected("data");
  }, [market]);
  useEffect(() => {
    if (refreshToken > 0) resource.refresh();
  }, [refreshToken]);

  if (resource.loading && !resource.data) {
    return <div className="skeleton-grid" aria-label="模型研究加载中"><div /><div /><div /><div /><div /></div>;
  }
  if (!resource.data) {
    return <div className="error-banner" role="alert">模型研究数据不可用：{resource.error ?? "unknown"}</div>;
  }
  const data = resource.data;
  const stage = data.stages.find((item) => item.key === selected) ?? data.stages[0];
  return (
    <section className="workspace-page model-research-page" aria-label="模型研究">
      {resource.stale ? <div className="stale-banner">刷新失败，显示 {data.generated_at.replace("T", " ")} 的最后成功快照</div> : null}
      <StageFlow stages={data.stages} selectedKey={stage.key} ariaLabel="模型研究进度" onSelect={setSelected} />
      <DetailPanel title={stage.label} status={stage.status} updatedAt={data.generated_at}>
        {stage.key === "data" ? (
          <div className="detail-stack">
            <dl className="workspace-metric-grid">
              <div><dt>候选特征</dt><dd>{data.dataPreparation.candidateFeatureCount}</dd></div>
              <div><dt>已选特征</dt><dd>{data.dataPreparation.selectedFeatureCount}</dd></div>
              <div><dt>结构化特征</dt><dd>{data.dataPreparation.structuredFeatureCount}</dd></div>
              <div><dt>情报特征</dt><dd>{data.dataPreparation.intelligenceFeatureCount}</dd></div>
              <div><dt>点时审计</dt><dd>{data.dataPreparation.pointInTimeAudit}</dd></div>
            </dl>
            <BoundedTable
              rows={data.dataPreparation.sources}
              rowKey={(row) => row.source}
              emptyLabel="尚无数据源健康记录"
              columns={[
                { key: "source", label: "来源", render: (row) => row.source },
                { key: "status", label: "状态", render: (row) => row.status },
                { key: "rows", label: "记录数", render: (row) => value(row.rows) },
                { key: "error", label: "缺口", render: (row) => row.error ?? "-" },
              ]}
            />
          </div>
        ) : null}
        {stage.key === "training" ? (
          <ModelTable models={data.training.models} validation={false} />
        ) : null}
        {stage.key === "validation" ? (
          <ModelTable models={data.validation.models} validation />
        ) : null}
        {stage.key === "simulation" ? (
          <dl className="workspace-metric-grid">
            <div><dt>候选版本</dt><dd>{String(data.simulation.candidate?.display_version ?? "等待候选")}</dd></div>
            <div><dt>预测产物</dt><dd>{data.simulation.predictionStatus} · {data.simulation.predictionAsOf ?? "-"}</dd></div>
            <div><dt>观察周期</dt><dd>{data.simulation.cyclesCompleted} / {data.simulation.cyclesRequired}</dd></div>
            <div><dt>候选证券</dt><dd>{data.simulation.decision.candidateRows}</dd></div>
            <div><dt>通过门槛</dt><dd>{data.simulation.decision.eligibleRows}</dd></div>
            <div><dt>本期决策</dt><dd>{data.simulation.decision.selectedCount} 个入选</dd></div>
            <div><dt>保持现金原因</dt><dd>{data.simulation.decision.cashReason ? reasonLabel(data.simulation.decision.cashReason) : "-"}</dd></div>
          </dl>
        ) : null}
        {stage.key === "adoption" ? (
          <div className="detail-stack">
            <strong>{data.adoption.champions.length ? `${data.adoption.champions.length} 个 Champion` : "正式策略仍由规则驱动"}</strong>
            <span>
              可回滚版本：
              {data.adoption.rollbackCandidates.map((row) => row.displayVersion).join("、") || "暂无"}
            </span>
            <BoundedTable
              rows={data.adoption.strategyUsage}
              rowKey={(row) => row.strategy_label}
              emptyLabel="尚无正式采用记录"
              columns={[
                { key: "strategy", label: "正式策略", render: (row) => row.strategy_label },
                { key: "status", label: "采用状态", render: (row) => row.status },
                { key: "version", label: "正式版本", render: (row) => Object.values(row.model_versions).join("、") || "未采用" },
                { key: "date", label: "采用日期", render: (row) => row.as_of ?? "-" },
                { key: "coverage", label: "模型覆盖率", render: (row) => `${(row.candidate_coverage * 100).toFixed(1)}%` },
                { key: "reason", label: "未采用原因", render: (row) => row.fallback_reason || "-" },
              ]}
            />
          </div>
        ) : null}
      </DetailPanel>
    </section>
  );
}

function ModelTable({
  models,
  validation,
}: {
  models: ModelResearchModel[];
  validation: boolean;
}) {
  return (
    <BoundedTable
      rows={models}
      rowKey={(row) => `${row.horizon}:${row.modelVersion}`}
      emptyLabel="尚无研究模型产物"
      columns={[
        { key: "version", label: "版本", render: (row) => row.modelVersion || "-" },
        { key: "horizon", label: "周期", render: (row) => `${row.horizon} 日` },
        { key: "algorithm", label: "算法族", render: (row) => row.algorithmFamily },
        { key: "samples", label: "样本", render: (row) => String(row.sampleSupport) },
        { key: "rank_ic", label: "Rank IC", render: (row) => value(row.metrics.rank_ic ?? row.metrics.mean_rank_ic) },
        { key: "icir", label: "ICIR", render: (row) => value(row.metrics.icir) },
        { key: "brier", label: "Brier", render: (row) => value(row.metrics.brier_score) },
        { key: "auc", label: "AUC", render: (row) => value(row.metrics.auc) },
        { key: "lift", label: "命中率提升", render: (row) => value(row.metrics.hit_rate_lift) },
        { key: "net", label: "扣费超额收益", render: (row) => value(row.metrics.net_excess_return) },
        { key: "turnover", label: "换手", render: (row) => value(row.metrics.turnover) },
        {
          key: "result",
          label: validation ? "验收结果" : "模型产物",
          render: (row) => validation
            ? row.gatePassed ? "通过" : row.gateReasons.map(reasonLabel).join("；") || "未通过"
            : row.artifactRef ?? row.artifactStatus,
        },
      ]}
    />
  );
}
```

- [ ] **Step 6: Run the model-page tests**

Run:

```bash
cd frontend/dashboard
npm test -- ModelResearchPage.test.tsx
```

Expected: PASS with stage selection, explicit gate failure, and valid zero-selection state.

- [ ] **Step 7: Commit the model page**

```bash
git add frontend/dashboard/src/workspaceTypes.ts frontend/dashboard/src/api.ts frontend/dashboard/src/ModelResearchPage.tsx frontend/dashboard/src/ModelResearchPage.test.tsx
git commit -m "feat: add model research workspace"
```

### Task 7: Add the Structured Data and Intelligence Supply Resource

**Files:**
- Modify: `stock_analyze/dashboard_workspace_api.py`
- Modify: `tests/test_dashboard_workspace_api.py`
- Modify: `stock_analyze/cli.py:2305-2560`
- Modify: `tests/test_cli_dashboard_routes.py:86-106`

- [ ] **Step 1: Add the failing data-supply evidence test**

Add alongside the existing test-module imports:

```python
import pandas as pd
```

Add these methods inside the existing `DashboardWorkspaceApiTests` class:

```python
    def test_data_intelligence_usage_requires_explicit_consumption_evidence(
        self,
    ) -> None:
        profiles = {
            "defensive": {
                "label": "稳健防守",
                "factors": ["roe", "low_volatility_60"],
            },
            "trend": {
                "label": "趋势进攻",
                "factors": ["momentum_20"],
            },
        }
        model_health = {
            "status": "available",
            "models": [
                {
                    "model_version": "A20-V005",
                    "horizon": 20,
                    "feature_columns": [
                        "momentum_20",
                        "event_net_strength_5d",
                    ],
                    "metrics": {"point_in_time_audit": True},
                }
            ],
        }
        intelligence = {
            "pipeline": {
                "status": "available",
                "documents": 584598,
                "stages": {
                    "catalogued": 584598,
                    "pdfReady": 23243,
                    "parsed": 6888,
                    "semanticCompleted": 35,
                    "canonicalEvents": 12,
                },
                "backlog": {
                    "download": 561355,
                    "parse": 16355,
                    "semantic": 6853,
                    "total": 584563,
                },
                "sources": [],
                "artifactWorkers": {"status": "available"},
            },
            "extraction": {
                "status": "available",
                "semanticRuns": {"succeeded": 35},
                "decisions": {
                    "canonical": 12,
                    "no_event": 20,
                    "quarantined": 2,
                    "failed": 1,
                },
                "latestBatch": None,
                "contract": {"profileId": "a-share-announcement-v1"},
            },
            "factorSupply": {
                "status": "available",
                "suppliedFactors": 23,
                "modelEligible": False,
                "modelEligibleFactors": [],
                "factors": [],
            },
            "modelImpact": {
                "status": "available",
                "adopted": False,
                "activeFactors": [],
                "iterationFactors": [],
                "reason": "no_factor_passed_gate",
            },
            "decisions": {
                "canonical": 12,
                "no_event": 20,
                "quarantined": 2,
                "failed": 1,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = (
                Path(tmp)
                / "data"
                / "research"
                / "features"
                / "a_share"
                / "20260729.parquet"
            )
            snapshot.parent.mkdir(parents=True)
            pd.DataFrame(
                {"trade_date": ["20230711", "20260729"]}
            ).to_parquet(snapshot, index=False)
            with mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value=profiles,
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value=model_health,
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={"candidate": {"model_version": "A20-V005"}},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=intelligence,
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[],
            ):
                payload = build_dashboard_data_intelligence_data(
                    repo_root=Path(tmp),
                    market="a_share",
                )

        usage = {row["consumerKey"]: row for row in payload["usageMatrix"]}
        self.assertEqual(usage["defensive"]["traditionalFactors"]["count"], 2)
        self.assertEqual(usage["defensive"]["intelligenceFactors"]["count"], 0)
        self.assertEqual(usage["research_model"]["intelligenceFactors"]["count"], 1)
        self.assertEqual(
            usage["research_model"]["intelligenceFactors"]["evidence"],
            ["model_feature_manifest:A20-V005"],
        )
        self.assertEqual(payload["structured"]["coverage"]["rangeStart"], "20230711")
        self.assertEqual(payload["structured"]["coverage"]["rangeEnd"], "20260729")
        self.assertEqual(payload["structured"]["coverage"]["latestTradeDate"], "20260729")
        adjusted = next(
            row
            for row in payload["structured"]["sources"]
            if row["source"] == "adjusted_ohlcv"
        )
        self.assertEqual(adjusted["selectedModelFeatureCount"], 1)
        self.assertEqual(adjusted["activeStrategyFactorCount"], 2)
        self.assertIn("研究模型 A20-V005", adjusted["useLocations"])
        self.assertEqual(payload["structured"]["quality"]["pointInTimeAuditedModels"], 1)
        self.assertEqual(payload["intelligence"]["pipeline"]["documents"], 584598)
        self.assertNotIn("rowsByDecision", payload["intelligence"])

    def test_formal_factor_sources_cover_every_non_sentiment_overlay_factor(
        self,
    ) -> None:
        for market in ("a_share", "cn_qdii_etf"):
            with self.subTest(market=market):
                expected = (
                    set(AVAILABLE_FACTORS_BY_MARKET[market])
                    - set(SENTIMENT_FACTORS)
                )
                mapped = set().union(
                    *FORMAL_FACTOR_SOURCES[market].values()
                )
                self.assertEqual(mapped, expected)
```

Add the imports:

```python
from stock_analyze.dashboard_workspace_api import (
    FORMAL_FACTOR_SOURCES,
    build_dashboard_data_intelligence_data,
    build_dashboard_model_research_data,
)
from stock_analyze.overlay_guard import (
    AVAILABLE_FACTORS_BY_MARKET,
    SENTIMENT_FACTORS,
)
```

- [ ] **Step 2: Run the test and verify the missing builder failure**

Run:

```bash
python3 -m unittest tests.test_dashboard_workspace_api.DashboardWorkspaceApiTests.test_data_intelligence_usage_requires_explicit_consumption_evidence -v
```

Expected: FAIL because `build_dashboard_data_intelligence_data` is not defined.

- [ ] **Step 3: Implement the evidence readers**

```python
import pandas as pd

from .dashboard_api import (
    _latest_strategy_model_usage,
    build_dashboard_intelligence_data,
)
from .overlay_guard import AVAILABLE_FACTORS_BY_MARKET, SENTIMENT_FACTORS
from .research.feature_registry import DEFAULT_REGISTRY, INTELLIGENCE_FEATURES


PUBLIC_STRATEGIES = (
    ("defensive", "claude", "稳健防守"),
    ("trend", "codex", "趋势进攻"),
)

FORMAL_FACTOR_SOURCES = {
    "a_share": {
        "tushare_daily_basic": {"pe", "pb"},
        "tushare_fina_indicator_announced": {
            "roe",
            "gross_margin",
            "debt_ratio",
            "net_profit_growth",
        },
        "adjusted_ohlcv": {
            "momentum_20",
            "momentum_60",
            "low_volatility_60",
        },
        "tushare_dividend": {"dividend_yield"},
    },
    "cn_qdii_etf": {
        "fund_daily_adjusted_ohlcv": {
            "momentum_20",
            "momentum_60",
            "low_volatility_60",
            "avg_amount_20",
        },
        "fund_nav": {"discount_premium"},
    },
}


def _public_strategy_profiles(
    root: Path,
    market: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for public_key, agent, fallback_label in PUBLIC_STRATEGIES:
        paths = agg._resolve_dashboard_paths(market, agent, root)
        try:
            profile = agg._dashboard_strategy_profile(
                paths,
                root=root,
                market=market,
                agent=agent,
            )
        except (OSError, TypeError, ValueError, agg.DashboardDataError):
            profile = {}
        factors = [
            str(item.get("key"))
            for item in profile.get("factors") or []
            if isinstance(item, dict) and item.get("key")
        ]
        result[public_key] = {
            "label": str(profile.get("agent_label") or fallback_label),
            "factors": sorted(set(factors)),
        }
    return result


def _model_feature_evidence(
    root: Path,
    market: str,
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    health = agg._read_model_health(root, market)
    models = list(health.get("models") or [])
    by_version = {
        str(row.get("model_version")): sorted(
            {str(value) for value in row.get("feature_columns") or [] if value}
        )
        for row in models
        if row.get("model_version")
    }
    audited = sum(
        1
        for row in models
        if isinstance(row.get("metrics"), dict)
        and row["metrics"].get("point_in_time_audit") is True
    )
    return by_version, {
        "status": "available" if models else "unavailable",
        "modelCount": len(models),
        "pointInTimeAuditedModels": audited,
        "pointInTimeFailedModels": max(0, len(models) - audited),
        "missingRateStatus": "not_recorded",
        "outlierStatus": "not_recorded",
    }


def _usage_cell(
    features: set[str],
    eligible: set[str],
    evidence: list[str],
    *,
    observing: bool = False,
) -> dict[str, Any]:
    used = sorted(features & eligible)
    status = "used" if used else "observing" if observing else "not_used"
    return {
        "status": status,
        "count": len(used),
        "features": used,
        "evidence": sorted(set(evidence)) if used or observing else [],
    }


def _structured_snapshot_coverage(root: Path, market: str) -> dict[str, Any]:
    paths = sorted(
        (root / "data" / "research" / "features" / market).glob(
            "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].parquet"
        )
    )
    if not paths:
        return {
            "status": "not_recorded",
            "rangeStart": None,
            "rangeEnd": None,
            "latestTradeDate": None,
            "latestSnapshot": None,
        }
    latest = paths[-1]
    try:
        trade_dates = (
            pd.read_parquet(latest, columns=["trade_date"])["trade_date"]
            .dropna()
            .astype("string")
            .str.replace("-", "", regex=False)
            .str[:8]
        )
    except (FileNotFoundError, KeyError, OSError, ValueError):
        trade_dates = pd.Series(dtype="string")
    try:
        artifact = str(latest.relative_to(root))
    except ValueError:
        artifact = latest.name
    return {
        "status": "available" if not trade_dates.empty else "partial",
        "rangeStart": (
            str(trade_dates.min()) if not trade_dates.empty else None
        ),
        "rangeEnd": (
            str(trade_dates.max()) if not trade_dates.empty else None
        ),
        "latestTradeDate": (
            str(trade_dates.max()) if not trade_dates.empty else None
        ),
        "latestSnapshot": artifact,
    }
```

- [ ] **Step 4: Implement the two lanes and actual-usage matrix**

```python
def build_dashboard_data_intelligence_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
) -> dict[str, Any]:
    _check_market(market)
    root = _root(repo_root)
    profiles = _public_strategy_profiles(root, market)
    model_features, quality = _model_feature_evidence(root, market)
    coverage = _structured_snapshot_coverage(root, market)
    all_model_features = {
        feature for features in model_features.values() for feature in features
    }
    intelligence_names = {
        item.name for item in INTELLIGENCE_FEATURES
    } | set(SENTIMENT_FACTORS)
    research_traditional_names = {
        item.name
        for item in DEFAULT_REGISTRY
        if market in item.markets and item.family != "market_intelligence"
    }
    formal_traditional_names = (
        set(AVAILABLE_FACTORS_BY_MARKET.get(market, set()))
        - set(SENTIMENT_FACTORS)
    )
    active_formal_factors = {
        factor
        for profile in profiles.values()
        for factor in profile["factors"]
        if factor in formal_traditional_names
    }
    selected_research_traditional = (
        all_model_features & research_traditional_names
    )
    source_groups: dict[str, dict[str, Any]] = {}
    for definition in DEFAULT_REGISTRY:
        if (
            market not in definition.markets
            or definition.family == "market_intelligence"
        ):
            continue
        row = source_groups.setdefault(
            definition.source,
            {
                "source": definition.source,
                "researchFeatureCount": 0,
                "selectedModelFeatureCount": 0,
                "strategyFactorCount": 0,
                "activeStrategyFactorCount": 0,
                "status": "declared",
                "useLocations": [],
            },
        )
        row["researchFeatureCount"] += 1
        if definition.name in all_model_features:
            row["selectedModelFeatureCount"] += 1
            row["status"] = "used"
            row["useLocations"].extend(
                f"研究模型 {version}"
                for version, features in model_features.items()
                if definition.name in features
            )
    for source, factor_names in FORMAL_FACTOR_SOURCES.get(market, {}).items():
        row = source_groups.setdefault(
            source,
            {
                "source": source,
                "researchFeatureCount": 0,
                "selectedModelFeatureCount": 0,
                "strategyFactorCount": 0,
                "activeStrategyFactorCount": 0,
                "status": "declared",
                "useLocations": [],
            },
        )
        row["strategyFactorCount"] += len(factor_names)
        active_names = {
            factor
            for profile in profiles.values()
            for factor in profile["factors"]
            if factor in factor_names
        }
        row["activeStrategyFactorCount"] += len(active_names)
        if active_names:
            row["status"] = "used"
        row["useLocations"].extend(
            profile["label"]
            for profile in profiles.values()
            if set(profile["factors"]) & factor_names
        )
    for row in source_groups.values():
        row["useLocations"] = sorted(set(row["useLocations"]))
    family_groups: dict[str, dict[str, Any]] = {}
    for definition in DEFAULT_REGISTRY:
        if (
            market not in definition.markets
            or definition.family == "market_intelligence"
        ):
            continue
        row = family_groups.setdefault(
            definition.family,
            {
                "family": definition.family,
                "definedFeatureCount": 0,
                "selectedFeatureCount": 0,
            },
        )
        row["definedFeatureCount"] += 1
        row["selectedFeatureCount"] += int(
            definition.name in all_model_features
        )

    intelligence = build_dashboard_intelligence_data(
        repo_root=root,
        market=market,
        agent="codex",
        limit=1,
    )
    factor_supply = intelligence["factorSupply"]
    iteration = agg._read_model_iteration_status(root, market)
    candidate = iteration.get("candidate") or {}
    candidate_version = str(candidate.get("model_version") or "")
    candidate_features = set(model_features.get(candidate_version, []))
    formal_usage = [
        row
        for row in _latest_strategy_model_usage(root)
        if row.get("market") == market
    ]

    usage_matrix: list[dict[str, Any]] = []
    for public_key, agent, fallback_label in PUBLIC_STRATEGIES:
        profile = profiles.get(public_key) or {
            "label": fallback_label,
            "factors": [],
        }
        factors = set(profile["factors"])
        usage_row = next(
            (
                row
                for row in formal_usage
                if row.get("agent") == agent
            ),
            {},
        )
        applied_versions = {
            str(value)
            for value in (usage_row.get("model_versions") or {}).values()
            if value
        }
        applied_model_features = {
            feature
            for version in applied_versions
            for feature in model_features.get(version, [])
        }
        combined_features = factors | applied_model_features
        input_evidence = ["strategy_overlay"] + [
            f"decision_lineage:{version}"
            for version in sorted(applied_versions)
        ]
        usage_matrix.append(
            {
                "consumerKey": public_key,
                "consumerLabel": profile["label"],
                "structuredData": _usage_cell(
                    combined_features,
                    formal_traditional_names | research_traditional_names,
                    input_evidence,
                ),
                "traditionalFactors": _usage_cell(
                    combined_features,
                    formal_traditional_names | research_traditional_names,
                    input_evidence,
                ),
                "intelligenceFactors": _usage_cell(
                    combined_features,
                    intelligence_names,
                    input_evidence,
                ),
                "impact": (
                    f"本期模型采用 {int(usage_row.get('applied_candidates') or 0)} 个候选"
                    if usage_row.get("status") == "active"
                    else "本期规则驱动"
                ),
            }
        )

    usage_matrix.extend(
        [
            {
                "consumerKey": "research_model",
                "consumerLabel": "研究模型",
                "structuredData": _usage_cell(
                    all_model_features,
                    research_traditional_names,
                    [
                        f"model_feature_manifest:{version}"
                        for version in sorted(model_features)
                    ],
                ),
                "traditionalFactors": _usage_cell(
                    all_model_features,
                    research_traditional_names,
                    [
                        f"model_feature_manifest:{version}"
                        for version in sorted(model_features)
                    ],
                ),
                "intelligenceFactors": _usage_cell(
                    all_model_features,
                    intelligence_names,
                    [
                        f"model_feature_manifest:{version}"
                        for version, features in sorted(model_features.items())
                        if set(features) & intelligence_names
                    ],
                    observing=bool(factor_supply.get("suppliedFactors")),
                ),
                "impact": (
                    f"{len(all_model_features)} 个训练特征，"
                    f"{len(all_model_features & intelligence_names)} 个来自情报"
                ),
            },
            {
                "consumerKey": "candidate_simulation",
                "consumerLabel": "候选模拟账户",
                "structuredData": _usage_cell(
                    candidate_features,
                    research_traditional_names,
                    (
                        [f"candidate_registry:{candidate_version}"]
                        if candidate_version else []
                    ),
                ),
                "traditionalFactors": _usage_cell(
                    candidate_features,
                    research_traditional_names,
                    [f"candidate_registry:{candidate_version}"],
                ),
                "intelligenceFactors": _usage_cell(
                    candidate_features,
                    intelligence_names,
                    [f"candidate_registry:{candidate_version}"],
                ),
                "impact": (
                    f"本期 {int(iteration.get('selected_count') or 0)} 个入选，"
                    f"{int(iteration.get('trades_executed') or 0)} 笔成交"
                ),
            },
        ]
    )

    structured_stages = [
        {
            "key": "sources",
            "label": "行情与财务",
            "status": "success" if any(row["status"] == "used" for row in source_groups.values()) else "research",
            "primary": f"{len(source_groups)} 个数据源",
            "secondary": (
                f"{sum(row['selectedModelFeatureCount'] for row in source_groups.values())} 个模型特征 · "
                f"{sum(row['activeStrategyFactorCount'] for row in source_groups.values())} 个策略因子"
            ),
        },
        {
            "key": "quality",
            "label": "清洗与质量",
            "status": "success" if model_features else "unavailable",
            "primary": (
                f"{quality['pointInTimeAuditedModels']} / "
                f"{quality['modelCount']} 个模型通过点时审计"
            ),
            "secondary": "点时证据来自模型元数据",
        },
        {
            "key": "traditional",
            "label": "传统量化因子",
            "status": (
                "success"
                if active_formal_factors or selected_research_traditional
                else "research"
            ),
            "primary": (
                f"{len(active_formal_factors)} 个正式策略 · "
                f"{len(selected_research_traditional)} 个研究模型"
            ),
            "secondary": (
                f"{len(formal_traditional_names)} 个策略可用 · "
                f"{len(research_traditional_names)} 个研究定义"
            ),
        },
    ]
    intelligence_stages = [
        {
            "key": "documents",
            "label": "公告与政策",
            "status": "success" if intelligence["pipeline"]["documents"] else "empty",
            "primary": f"{intelligence['pipeline']['documents']} 篇目录",
            "secondary": f"{len(intelligence['pipeline']['sources'])} 个来源",
        },
        {
            "key": "artifacts",
            "label": "下载与解析",
            "status": "running" if intelligence["pipeline"]["backlog"]["total"] else "success",
            "primary": f"{intelligence['pipeline']['stages']['parsed']} 篇已解析",
            "secondary": f"{intelligence['pipeline']['backlog']['total']} 篇积压",
        },
        {
            "key": "semantic",
            "label": "语义事件",
            "status": "success" if intelligence["pipeline"]["stages"]["semanticCompleted"] else "research",
            "primary": f"{intelligence['pipeline']['stages']['canonicalEvents']} 个标准事件",
            "secondary": f"{intelligence['decisions']['failed']} 个失败",
        },
        {
            "key": "intelligence_factors",
            "label": "情报因子",
            "status": "success" if factor_supply.get("modelEligible") else "research",
            "primary": f"{factor_supply.get('suppliedFactors', 0)} 个已计算",
            "secondary": f"{len(factor_supply.get('modelEligibleFactors') or [])} 个可入模",
        },
    ]
    return agg._json_safe(
        {
            "generated_at": _generated_at(),
            "market": market,
            "market_label": agg.MARKET_LABELS.get(market, market),
            "structured": {
                "stages": structured_stages,
                "sources": sorted(source_groups.values(), key=lambda row: row["source"]),
                "coverage": coverage,
                "factorGroups": sorted(family_groups.values(), key=lambda row: row["family"]),
                "selectedFeatures": sorted(selected_research_traditional),
                "quality": quality,
            },
            "intelligence": {
                "stages": intelligence_stages,
                "pipeline": intelligence["pipeline"],
                "extraction": intelligence["extraction"],
                "factorSupply": factor_supply,
                "modelImpact": intelligence["modelImpact"],
                "decisions": intelligence["decisions"],
            },
            "usageMatrix": usage_matrix,
        }
    )
```

- [ ] **Step 5: Register the data-intelligence resource**

Add to `_is_dashboard_api_path`:

```python
"/api/dashboard/data-intelligence.json",
```

Add to the workspace import and dispatch:

```python
from .dashboard_workspace_api import (
    build_dashboard_data_intelligence_data,
    build_dashboard_model_research_data,
)

if canonical_path == "/api/dashboard/data-intelligence.json":
    return build_dashboard_data_intelligence_data(
        repo_root=repo_root,
        market=market,
    )
```

Add `"data-intelligence"` to the route smoke-test resource tuple.

- [ ] **Step 6: Run focused backend tests**

Run:

```bash
python3 -m unittest tests.test_dashboard_workspace_api tests.test_cli_dashboard_routes -v
```

Expected: PASS; formal strategy, research model, and candidate account usage are backed by distinct evidence arrays.

- [ ] **Step 7: Commit the data-supply resource**

```bash
git add stock_analyze/dashboard_workspace_api.py tests/test_dashboard_workspace_api.py stock_analyze/cli.py tests/test_cli_dashboard_routes.py
git commit -m "feat: add data and intelligence supply resource"
```

### Task 8: Build the Data and Intelligence Workspace

**Files:**
- Modify: `frontend/dashboard/src/workspaceTypes.ts`
- Modify: `frontend/dashboard/src/api.ts`
- Create: `frontend/dashboard/src/DataIntelligencePage.tsx`
- Create: `frontend/dashboard/src/DataIntelligencePage.test.tsx`
- Modify: `frontend/dashboard/src/IntelligencePanel.tsx`
- Modify: `frontend/dashboard/src/IntelligencePanel.test.tsx`

- [ ] **Step 1: Write the failing supply-lane and usage-matrix test**

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DataIntelligencePage } from "./DataIntelligencePage";

vi.mock("./api", () => ({
  fetchDataIntelligence: vi.fn().mockResolvedValue({
    generated_at: "2026-07-30T13:00:00",
    market: "a_share",
    market_label: "A股",
    structured: {
      stages: [
        { key: "sources", label: "行情与财务", status: "success", primary: "6 个数据源", secondary: "47 个已选特征" },
        { key: "quality", label: "清洗与质量", status: "success", primary: "4 个模型清单", secondary: "点时证据来自模型元数据" },
        { key: "traditional", label: "传统量化因子", status: "success", primary: "47 个已选", secondary: "72 个已定义" },
      ],
      sources: [{
        source: "adjusted_ohlcv",
        researchFeatureCount: 32,
        selectedModelFeatureCount: 20,
        strategyFactorCount: 3,
        activeStrategyFactorCount: 2,
        status: "used",
        useLocations: ["研究模型 A20-V005", "稳健防守", "趋势进攻"],
      }],
      coverage: {
        status: "available",
        rangeStart: "20230711",
        rangeEnd: "20260729",
        latestTradeDate: "20260729",
        latestSnapshot: "data/research/features/a_share/20260729.parquet",
      },
      factorGroups: [{ family: "technical", definedFeatureCount: 32, selectedFeatureCount: 20 }],
      selectedFeatures: ["momentum_20"],
      quality: {
        status: "available",
        modelCount: 4,
        pointInTimeAuditedModels: 4,
        pointInTimeFailedModels: 0,
        missingRateStatus: "not_recorded",
        outlierStatus: "not_recorded",
      },
    },
    intelligence: {
      stages: [
        { key: "documents", label: "公告与政策", status: "success", primary: "584598 篇目录", secondary: "2 个来源" },
        { key: "artifacts", label: "下载与解析", status: "running", primary: "6888 篇已解析", secondary: "577710 篇积压" },
        { key: "semantic", label: "语义事件", status: "success", primary: "12 个标准事件", secondary: "1 个失败" },
        { key: "intelligence_factors", label: "情报因子", status: "research", primary: "23 个已计算", secondary: "0 个可入模" },
      ],
      pipeline: {
        status: "available",
        documents: 584598,
        stages: { catalogued: 584598, pdfReady: 23243, parsed: 6888, semanticCompleted: 35, canonicalEvents: 12 },
        backlog: { download: 561355, parse: 16355, semantic: 6853, total: 584563 },
        sources: [],
        artifactWorkers: { status: "available" },
      },
      extraction: { status: "available", semanticRuns: {}, decisions: {}, latestBatch: null, contract: { profileId: "a-share-announcement-v1" } },
      factorSupply: { status: "available", suppliedFactors: 23, modelEligible: false, modelEligibleFactors: [], factors: [] },
      modelImpact: { status: "available", adopted: false, reason: "no_factor_passed_gate" },
      decisions: { canonical: 12, no_event: 20, quarantined: 2, failed: 1 },
    },
    usageMatrix: [
      {
        consumerKey: "research_model",
        consumerLabel: "研究模型",
        structuredData: { status: "used", count: 47, evidence: ["model_feature_manifest:A20-V005"] },
        traditionalFactors: { status: "used", count: 47, features: ["momentum_20"], evidence: ["model_feature_manifest:A20-V005"] },
        intelligenceFactors: { status: "used", count: 1, features: ["event_net_strength_5d"], evidence: ["model_feature_manifest:A20-V005"] },
        impact: "48 个训练特征，1 个来自情报",
      },
    ],
  }),
}));

vi.mock("./IntelligencePanel", () => ({
  IntelligencePanel: ({ mode }: { mode: string }) => <div>情报证据模式：{mode}</div>,
}));

describe("DataIntelligencePage", () => {
  it("drills into actual node data and shows explicit usage evidence", async () => {
    const user = userEvent.setup();
    render(<DataIntelligencePage market="a_share" refreshToken={0} />);
    expect(await screen.findByText("结构化数据")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /行情与财务/ }));
    expect(screen.getByText("adjusted_ohlcv")).toBeInTheDocument();
    expect(screen.getByText("2026-07-29")).toBeInTheDocument();
    expect(screen.getByText("研究模型 A20-V005、稳健防守、趋势进攻")).toBeInTheDocument();
    const matrix = screen.getByRole("table", { name: "实际使用去向" });
    expect(within(matrix).getByText("研究模型")).toBeInTheDocument();
    expect(within(matrix).getByText("48 个训练特征，1 个来自情报")).toBeInTheDocument();
  });

  it("lazy-loads the existing evidence ledger only after semantic drill-down", async () => {
    const user = userEvent.setup();
    render(<DataIntelligencePage market="a_share" refreshToken={0} />);
    await screen.findByText("文本情报");
    expect(screen.queryByText("情报证据模式：ledger")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /语义事件/ }));
    expect(screen.getByText("情报证据模式：ledger")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test and verify missing page/fetch failures**

Run:

```bash
cd frontend/dashboard
npm test -- DataIntelligencePage.test.tsx
```

Expected: FAIL because the page and fetch function do not exist.

- [ ] **Step 3: Add the resource contracts and fetcher**

```ts
export type UsageEvidenceCell = {
  status: "used" | "not_used" | "observing" | "unavailable" | string;
  count: number;
  features?: string[];
  evidence: string[];
};

export type DataIntelligenceData = {
  generated_at: string;
  market: string;
  market_label: string;
  structured: {
    stages: WorkspaceStage[];
    sources: {
      source: string;
      researchFeatureCount: number;
      selectedModelFeatureCount: number;
      strategyFactorCount: number;
      activeStrategyFactorCount: number;
      status: string;
      useLocations: string[];
    }[];
    coverage: {
      status: string;
      rangeStart: string | null;
      rangeEnd: string | null;
      latestTradeDate: string | null;
      latestSnapshot: string | null;
    };
    factorGroups: {
      family: string;
      definedFeatureCount: number;
      selectedFeatureCount: number;
    }[];
    selectedFeatures: string[];
    quality: {
      status: string;
      modelCount: number;
      pointInTimeAuditedModels: number;
      pointInTimeFailedModels: number;
      missingRateStatus: string;
      outlierStatus: string;
    };
  };
  intelligence: {
    stages: WorkspaceStage[];
    pipeline: import("./types").IntelligenceSummary["pipeline"];
    extraction: import("./types").IntelligenceSummary["extraction"];
    factorSupply: import("./types").IntelligenceSummary["factorSupply"];
    modelImpact: import("./types").IntelligenceSummary["modelImpact"];
    decisions: import("./types").IntelligenceSummary["decisions"];
  };
  usageMatrix: {
    consumerKey: string;
    consumerLabel: string;
    structuredData: UsageEvidenceCell;
    traditionalFactors: UsageEvidenceCell;
    intelligenceFactors: UsageEvidenceCell;
    impact: string;
  }[];
};
```

```ts
import type {
  DataIntelligenceData,
  ModelResearchData,
} from "./workspaceTypes";

export function fetchDataIntelligence(
  market: string,
  signal?: AbortSignal,
): Promise<DataIntelligenceData> {
  const params = new URLSearchParams({ market });
  return fetchJson<DataIntelligenceData>(
    `/api/dashboard/data-intelligence.json?${params.toString()}`,
    signal,
  );
}
```

- [ ] **Step 4: Add a ledger-only mode to the existing intelligence component**

Change the prop contract:

```tsx
type IntelligencePanelProps = {
  intelligence: MarketIntelligence;
  eager?: boolean;
  refreshToken?: number;
  standalone?: boolean;
  mode?: "full" | "ledger";
};
```

Default and gate the existing overview bands:

```tsx
export function IntelligencePanel({
  intelligence,
  eager = false,
  refreshToken = 0,
  standalone = false,
  mode = "full",
}: IntelligencePanelProps) {
  const showOverview = mode === "full";
```

Add `hidden={!showOverview}` to the five existing overview containers:

```tsx
<header
  className="section-heading intelligence-heading"
  hidden={!showOverview}
>

<div
  className="intelligence-flow"
  aria-label="语料到模型四层链路"
  hidden={!showOverview}
>

<section
  className="intelligence-band"
  aria-labelledby="source-freshness-title"
  hidden={!showOverview}
>

<section
  className="intelligence-band semantic-batch"
  aria-labelledby="latest-batch-title"
  hidden={!showOverview}
>

<div className="factor-impact-grid" hidden={!showOverview}>
```

Do not add `hidden` to the existing `decision-ledger` section or its detail/document drawer. In `IntelligencePanel.test.tsx`, render `mode="ledger"` and assert `语义决策明细` is present while `情报链路总览` is absent.

- [ ] **Step 5: Implement the two-lane page and matrix**

```tsx
import { useCallback, useEffect, useState } from "react";
import { fetchDataIntelligence } from "./api";
import { IntelligencePanel } from "./IntelligencePanel";
import { StageFlow } from "./StageFlow";
import { BoundedTable, DetailPanel } from "./WorkspacePrimitives";
import { useWorkspaceResource } from "./useWorkspaceResource";
import type { DataIntelligenceData, WorkspaceStage } from "./workspaceTypes";

function usageLabel(status: string): string {
  return {
    used: "已使用",
    not_used: "未使用",
    observing: "观察中",
    unavailable: "状态不可用",
  }[status] ?? status;
}

function dateLabel(value: string | null): string {
  if (!value) return "未记录";
  return /^\d{8}$/.test(value)
    ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
    : value;
}

export function DataIntelligencePage({
  market,
  refreshToken,
}: {
  market: string;
  refreshToken: number;
}) {
  const loader = useCallback(
    (signal: AbortSignal) => fetchDataIntelligence(market, signal),
    [market],
  );
  const resource = useWorkspaceResource<DataIntelligenceData>(market, true, loader);
  const [selected, setSelected] = useState("structured:sources");
  useEffect(() => setSelected("structured:sources"), [market]);
  useEffect(() => {
    if (refreshToken > 0) resource.refresh();
  }, [refreshToken]);

  if (resource.loading && !resource.data) {
    return <div className="skeleton-grid" aria-label="数据与情报加载中"><div /><div /><div /><div /></div>;
  }
  if (!resource.data) {
    return <div className="error-banner" role="alert">数据与情报不可用：{resource.error ?? "unknown"}</div>;
  }
  const data = resource.data;
  const [lane, key] = selected.split(":");
  const stages = lane === "structured"
    ? data.structured.stages
    : data.intelligence.stages;
  const active = stages.find((stage) => stage.key === key) ?? stages[0];
  const select = (nextLane: "structured" | "intelligence") =>
    (nextKey: string) => setSelected(`${nextLane}:${nextKey}`);

  return (
    <section className="workspace-page data-intelligence-page" aria-label="数据与情报">
      {resource.stale ? <div className="stale-banner">刷新失败，显示 {data.generated_at.replace("T", " ")} 的最后成功快照</div> : null}
      <div className="supply-lanes">
        <section aria-labelledby="structured-lane-title">
          <h2 id="structured-lane-title">结构化数据</h2>
          <StageFlow stages={data.structured.stages} selectedKey={lane === "structured" ? key : ""} ariaLabel="结构化数据供给链" onSelect={select("structured")} />
        </section>
        <section aria-labelledby="intelligence-lane-title">
          <h2 id="intelligence-lane-title">文本情报</h2>
          <StageFlow stages={data.intelligence.stages} selectedKey={lane === "intelligence" ? key : ""} ariaLabel="文本情报供给链" onSelect={select("intelligence")} />
        </section>
      </div>
      <SupplyDetail data={data} lane={lane} stage={active} market={market} refreshToken={refreshToken} />
      <section className="usage-matrix-section">
        <header className="section-heading"><div><h2>实际使用去向</h2></div></header>
        <table className="bounded-table" aria-label="实际使用去向">
          <thead><tr><th>使用对象</th><th>结构化数据</th><th>传统因子</th><th>情报因子</th><th>当前影响</th></tr></thead>
          <tbody>
            {data.usageMatrix.map((row) => (
              <tr key={row.consumerKey}>
                <td><strong>{row.consumerLabel}</strong></td>
                <td>{usageLabel(row.structuredData.status)} · {row.structuredData.count}</td>
                <td>{usageLabel(row.traditionalFactors.status)} · {row.traditionalFactors.count}</td>
                <td>{usageLabel(row.intelligenceFactors.status)} · {row.intelligenceFactors.count}</td>
                <td>{row.impact}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </section>
  );
}

function SupplyDetail({
  data,
  lane,
  stage,
  market,
  refreshToken,
}: {
  data: DataIntelligenceData;
  lane: string;
  stage: WorkspaceStage;
  market: string;
  refreshToken: number;
}) {
  if (lane === "intelligence" && stage.key === "semantic") {
    return (
      <IntelligencePanel
        intelligence={{ market, agent: "model_shadow" }}
        eager
        refreshToken={refreshToken}
        mode="ledger"
      />
    );
  }
  return (
    <DetailPanel title={stage.label} status={stage.status} updatedAt={data.generated_at}>
      {lane === "structured" && stage.key === "sources" ? (
        <>
          <dl className="workspace-metric-grid">
            <div>
              <dt>数据覆盖区间</dt>
              <dd>{dateLabel(data.structured.coverage.rangeStart)} 至 {dateLabel(data.structured.coverage.rangeEnd)}</dd>
            </div>
            <div>
              <dt>最近交易日</dt>
              <dd>{dateLabel(data.structured.coverage.latestTradeDate)}</dd>
            </div>
            <div>
              <dt>最新快照</dt>
              <dd>{data.structured.coverage.latestSnapshot ?? "未记录"}</dd>
            </div>
          </dl>
          <BoundedTable
            rows={data.structured.sources}
            rowKey={(row) => row.source}
            emptyLabel="尚无结构化数据源记录"
            columns={[
              { key: "source", label: "数据源", render: (row) => row.source },
              { key: "status", label: "状态", render: (row) => row.status },
              { key: "research", label: "研究特征", render: (row) => `${row.selectedModelFeatureCount} / ${row.researchFeatureCount}` },
              { key: "strategy", label: "正式策略因子", render: (row) => `${row.activeStrategyFactorCount} / ${row.strategyFactorCount}` },
              { key: "usedBy", label: "使用位置", render: (row) => row.useLocations.join("、") || "尚未使用" },
            ]}
          />
        </>
      ) : null}
      {lane === "structured" && stage.key === "quality" ? (
        <dl className="workspace-metric-grid">
          <div><dt>模型清单</dt><dd>{data.structured.quality.modelCount}</dd></div>
          <div><dt>点时审计通过</dt><dd>{data.structured.quality.pointInTimeAuditedModels}</dd></div>
          <div><dt>点时审计未通过</dt><dd>{data.structured.quality.pointInTimeFailedModels}</dd></div>
          <div><dt>缺失率产物</dt><dd>{data.structured.quality.missingRateStatus}</dd></div>
          <div><dt>异常值产物</dt><dd>{data.structured.quality.outlierStatus}</dd></div>
        </dl>
      ) : null}
      {lane === "structured" && stage.key === "traditional" ? (
        <BoundedTable
          rows={data.structured.factorGroups}
          rowKey={(row) => row.family}
          emptyLabel="尚无因子分组记录"
          columns={[
            { key: "family", label: "因子组", render: (row) => row.family },
            { key: "defined", label: "已定义", render: (row) => String(row.definedFeatureCount) },
            { key: "selected", label: "已选入模型", render: (row) => String(row.selectedFeatureCount) },
          ]}
        />
      ) : null}
      {lane === "intelligence" && stage.key === "documents" ? (
        <BoundedTable
          rows={data.intelligence.pipeline.sources}
          rowKey={(row) => row.source}
          emptyLabel="尚无情报来源运行记录"
          columns={[
            { key: "source", label: "来源", render: (row) => row.source },
            { key: "freshness", label: "每日增量新鲜度", render: (row) => row.freshnessStatus },
            { key: "published", label: "最新发布日期", render: (row) => row.latestPublishedAt ?? "-" },
            { key: "ingested", label: "最近拉取", render: (row) => row.lastIngestedAt ?? "-" },
            { key: "cursor", label: "增量游标", render: (row) => row.cursor ?? "-" },
          ]}
        />
      ) : null}
      {lane === "intelligence" && stage.key === "artifacts" ? (
        <dl className="workspace-metric-grid">
          <div><dt>公告目录</dt><dd>{data.intelligence.pipeline.documents}</dd></div>
          <div><dt>PDF 就绪</dt><dd>{data.intelligence.pipeline.stages.pdfReady}</dd></div>
          <div><dt>已解析</dt><dd>{data.intelligence.pipeline.stages.parsed}</dd></div>
          <div><dt>下载回填积压</dt><dd>{data.intelligence.pipeline.backlog.download}</dd></div>
          <div><dt>解析回填积压</dt><dd>{data.intelligence.pipeline.backlog.parse}</dd></div>
          <div><dt>Worker 状态</dt><dd>{data.intelligence.pipeline.artifactWorkers.status}</dd></div>
        </dl>
      ) : null}
      {lane === "intelligence" && stage.key === "intelligence_factors" ? (
        <BoundedTable
          rows={data.intelligence.factorSupply.factors}
          rowKey={(row) => row.name}
          emptyLabel="尚无情报因子验证记录"
          columns={[
            { key: "name", label: "因子", render: (row) => row.name },
            { key: "state", label: "生命周期", render: (row) => row.state },
            { key: "coverage", label: "覆盖率", render: (row) => row.coverage == null ? "-" : `${(row.coverage * 100).toFixed(1)}%` },
            { key: "activation", label: "激活率", render: (row) => row.activationRate == null ? "-" : `${(row.activationRate * 100).toFixed(1)}%` },
            { key: "ic", label: "平均 Rank IC", render: (row) => row.meanRankIc == null ? "-" : row.meanRankIc.toFixed(4) },
            { key: "recommendation", label: "采用建议", render: (row) => row.recommendation ?? "-" },
          ]}
        />
      ) : null}
    </DetailPanel>
  );
}
```

- [ ] **Step 6: Run the data/intelligence tests**

Run:

```bash
cd frontend/dashboard
npm test -- DataIntelligencePage.test.tsx IntelligencePanel.test.tsx
```

Expected: PASS; the initial page uses one bounded summary, and the evidence ledger loads only after semantic drill-down.

- [ ] **Step 7: Commit the data/intelligence page**

```bash
git add frontend/dashboard/src/workspaceTypes.ts frontend/dashboard/src/api.ts frontend/dashboard/src/DataIntelligencePage.tsx frontend/dashboard/src/DataIntelligencePage.test.tsx frontend/dashboard/src/IntelligencePanel.tsx frontend/dashboard/src/IntelligencePanel.test.tsx
git commit -m "feat: add data and intelligence workspace"
```

### Task 9: Add an Allowlisted Systemd Runtime Reader

**Files:**
- Create: `stock_analyze/dashboard_runtime.py`
- Create: `tests/test_dashboard_runtime.py`

- [ ] **Step 1: Write failing allowlist, parsing, and degradation tests**

```python
from __future__ import annotations

import subprocess
import unittest

from stock_analyze.dashboard_runtime import (
    RUNTIME_SERVICE_UNITS,
    RUNTIME_TIMER_UNITS,
    read_dashboard_runtime,
)


class DashboardRuntimeTests(unittest.TestCase):
    def test_reads_only_fixed_allowlisted_units(self) -> None:
        calls: list[list[str]] = []

        def runner(command, **_kwargs):
            calls.append(list(command))
            units = [
                value
                for value in command
                if value.endswith(".service") or value.endswith(".timer")
            ]
            blocks = []
            for unit in units:
                if unit.endswith(".timer"):
                    blocks.append(
                        "\n".join(
                            [
                                f"Id={unit}",
                                "ActiveState=active",
                                "LastTriggerUSec=Wed 2026-07-30 12:30:00 CST",
                                "NextElapseUSecRealtime=Wed 2026-07-30 16:30:00 CST",
                            ]
                        )
                    )
                else:
                    blocks.append(
                        "\n".join(
                            [
                                f"Id={unit}",
                                "ActiveState=inactive",
                                "SubState=dead",
                                "Result=success",
                                "ExecMainStatus=0",
                                "ExecMainStartTimestamp=Wed 2026-07-30 12:30:00 CST",
                                "ExecMainExitTimestamp=Wed 2026-07-30 12:31:00 CST",
                            ]
                        )
                    )
            return subprocess.CompletedProcess(command, 0, "\n\n".join(blocks), "")

        payload = read_dashboard_runtime(runner=runner, cache={})

        requested = {
            value
            for call in calls
            for value in call
            if value.endswith(".service") or value.endswith(".timer")
        }
        self.assertEqual(
            requested,
            set(RUNTIME_SERVICE_UNITS) | set(RUNTIME_TIMER_UNITS),
        )
        self.assertEqual(payload["status"], "available")
        self.assertEqual(
            payload["services"]["stock-analyze-intelligence.service"]["result"],
            "success",
        )

    def test_missing_systemctl_degrades_without_raising(self) -> None:
        def runner(_command, **_kwargs):
            raise FileNotFoundError("systemctl")

        payload = read_dashboard_runtime(runner=runner, cache={})

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason"], "runtime_status_unavailable")
        self.assertEqual(payload["services"], {})
        self.assertEqual(payload["timers"], {})

    def test_failure_returns_the_last_successful_snapshot_as_stale(self) -> None:
        cache: dict = {}
        calls = 0

        def runner(command, **_kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                unit = next(
                    value
                    for value in command
                    if value.endswith(".service") or value.endswith(".timer")
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"Id={unit}\nActiveState=active\nResult=success\nExecMainStatus=0",
                    "",
                )
            raise OSError("systemd bus unavailable")

        first = read_dashboard_runtime(runner=runner, cache=cache)
        second = read_dashboard_runtime(runner=runner, cache=cache)

        self.assertEqual(first["status"], "available")
        self.assertEqual(second["status"], "unavailable")
        self.assertEqual(second["last_known_at"], first["generated_at"])
        self.assertTrue(second["services"] or second["timers"])
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run:

```bash
python3 -m unittest tests.test_dashboard_runtime -v
```

Expected: FAIL with `ModuleNotFoundError: stock_analyze.dashboard_runtime`.

- [ ] **Step 3: Implement the fixed inventory and batch parser**

```python
"""Read-only, allowlisted systemd snapshots for the Dashboard."""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from typing import Any, Callable


RUNTIME_SERVICE_UNITS = (
    "stock-analyze-intelligence.service",
    "stock-analyze-market-data.service",
    "stock-analyze-research.service",
    "stock-analyze-model-iteration.service",
    "stock-analyze-claude-daily.service",
    "stock-analyze-codex-daily.service",
    "stock-analyze-claude-cn-qdii-etf-daily.service",
    "stock-analyze-codex-cn-qdii-etf-daily.service",
    "stock-analyze-aggregate-dashboard.service",
    "stock-analyze-daily-summary.service",
    "stock-analyze-intelligence-artifact-backfill.service",
    "stock-analyze-intelligence-reconcile.service",
    "stock-analyze-intelligence-semantic.service",
    "stock-analyze-ifind-source-audit.service",
    "stock-analyze-weekly-trigger.service",
    "stock-analyze-claude-weekly.service",
    "stock-analyze-codex-weekly.service",
    "stock-analyze-claude-cn-qdii-etf-weekly.service",
    "stock-analyze-codex-cn-qdii-etf-weekly.service",
    "stock-analyze-qdii-research.service",
    "stock-analyze-model-training.service",
    "stock-analyze-monthly-review.service",
    "stock-analyze-weekly-summary.service",
    "stock-analyze-monthly-summary.service",
)

RUNTIME_TIMER_UNITS = (
    "stock-analyze-market-data.timer",
    "stock-analyze-weekly-trigger.timer",
    "stock-analyze-monthly-review.timer",
    "stock-analyze-claude-cn-qdii-etf-weekly.timer",
    "stock-analyze-codex-cn-qdii-etf-weekly.timer",
    "stock-analyze-qdii-research.timer",
    "stock-analyze-model-training.timer",
    "stock-analyze-daily-summary.timer",
    "stock-analyze-weekly-summary.timer",
    "stock-analyze-monthly-summary.timer",
    "stock-analyze-intelligence.timer",
    "stock-analyze-intelligence-reconcile.timer",
    "stock-analyze-intelligence-artifact-backfill.timer",
    "stock-analyze-intelligence-semantic.timer",
    "stock-analyze-ifind-source-audit.timer",
)

SERVICE_PROPERTIES = (
    "Id",
    "ActiveState",
    "SubState",
    "Result",
    "ExecMainStatus",
    "ExecMainStartTimestamp",
    "ExecMainExitTimestamp",
)
TIMER_PROPERTIES = (
    "Id",
    "ActiveState",
    "LastTriggerUSec",
    "NextElapseUSecRealtime",
)

_RUNTIME_CACHE: dict[str, Any] = {}
_RUNTIME_CACHE_LOCK = threading.Lock()


def _parse_show(output: str) -> dict[str, dict[str, str]]:
    parsed: dict[str, dict[str, str]] = {}
    for block in output.strip().split("\n\n"):
        row = {}
        for line in block.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                row[key] = value
        unit = row.get("Id")
        if unit:
            parsed[unit] = row
    return parsed


def _show(
    units: tuple[str, ...],
    properties: tuple[str, ...],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, dict[str, str]]:
    command = [
        "systemctl",
        "show",
        "--no-pager",
        f"--property={','.join(properties)}",
        *units,
    ]
    result = runner(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "systemctl_show_failed")
    return _parse_show(result.stdout)


def _project_service(row: dict[str, str]) -> dict[str, Any]:
    exit_status = row.get("ExecMainStatus")
    return {
        "activeState": row.get("ActiveState") or "unknown",
        "subState": row.get("SubState") or "unknown",
        "result": row.get("Result") or "unknown",
        "exitStatus": int(exit_status) if str(exit_status).isdigit() else None,
        "startedAt": row.get("ExecMainStartTimestamp") or None,
        "finishedAt": row.get("ExecMainExitTimestamp") or None,
    }


def _project_timer(row: dict[str, str]) -> dict[str, Any]:
    return {
        "activeState": row.get("ActiveState") or "unknown",
        "lastTriggerAt": row.get("LastTriggerUSec") or None,
        "nextTriggerAt": row.get("NextElapseUSecRealtime") or None,
    }


def read_dashboard_runtime(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now().isoformat(timespec="seconds")
    runtime_cache = _RUNTIME_CACHE if cache is None else cache
    try:
        services = _show(
            RUNTIME_SERVICE_UNITS,
            SERVICE_PROPERTIES,
            runner=runner,
        )
        timers = _show(
            RUNTIME_TIMER_UNITS,
            TIMER_PROPERTIES,
            runner=runner,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        if cache is None:
            with _RUNTIME_CACHE_LOCK:
                previous = dict(runtime_cache.get("last_successful") or {})
        else:
            previous = dict(runtime_cache.get("last_successful") or {})
        return {
            "status": "unavailable",
            "generated_at": generated_at,
            "last_known_at": previous.get("generated_at"),
            "reason": "runtime_status_unavailable",
            "services": previous.get("services") or {},
            "timers": previous.get("timers") or {},
        }
    payload = {
        "status": "available",
        "generated_at": generated_at,
        "last_known_at": generated_at,
        "reason": None,
        "services": {
            unit: _project_service(services[unit])
            for unit in RUNTIME_SERVICE_UNITS
            if unit in services
        },
        "timers": {
            unit: _project_timer(timers[unit])
            for unit in RUNTIME_TIMER_UNITS
            if unit in timers
        },
    }
    if cache is None:
        with _RUNTIME_CACHE_LOCK:
            runtime_cache["last_successful"] = payload
    else:
        runtime_cache["last_successful"] = payload
    return payload
```

- [ ] **Step 4: Add the special lock-skip assertion**

Add this method inside `DashboardRuntimeTests`:

```python
    def test_artifact_worker_exit_75_is_preserved_for_status_mapping(self) -> None:
        from stock_analyze.dashboard_runtime import _project_service

        row = _project_service(
            {
                "ActiveState": "inactive",
                "SubState": "dead",
                "Result": "success",
                "ExecMainStatus": "75",
            }
        )

        self.assertEqual(row["result"], "success")
        self.assertEqual(row["exitStatus"], 75)
```

- [ ] **Step 5: Run the runtime tests**

Run:

```bash
python3 -m unittest tests.test_dashboard_runtime -v
```

Expected: PASS; only fixed units are read, two batch calls are made, and local non-systemd hosts degrade cleanly.

- [ ] **Step 6: Commit the runtime reader**

```bash
git add stock_analyze/dashboard_runtime.py tests/test_dashboard_runtime.py
git commit -m "feat: add allowlisted dashboard runtime reader"
```

### Task 10: Add the Operations Center Resource

**Files:**
- Modify: `stock_analyze/dashboard_workspace_api.py`
- Modify: `tests/test_dashboard_workspace_api.py`
- Modify: `stock_analyze/cli.py:2305-2560`
- Modify: `tests/test_cli_dashboard_routes.py:86-106`

- [ ] **Step 1: Add failing status-semantics and bounded-history tests**

Add this method inside `DashboardWorkspaceApiTests`:

```python
    def test_operations_center_distinguishes_waiting_skip_and_failure(self) -> None:
        runtime = {
            "status": "available",
            "generated_at": "2026-07-30T13:30:00",
            "last_known_at": "2026-07-30T13:30:00",
            "reason": None,
            "services": {
                "stock-analyze-intelligence.service": {
                    "activeState": "inactive",
                    "subState": "dead",
                    "result": "success",
                    "exitStatus": 0,
                    "startedAt": "Wed 2026-07-30 12:30:00 CST",
                    "finishedAt": "Wed 2026-07-30 12:31:00 CST",
                },
                "stock-analyze-market-data.service": {
                    "activeState": "inactive",
                    "subState": "dead",
                    "result": "success",
                    "exitStatus": 0,
                    "startedAt": "Tue 2026-07-29 18:30:00 CST",
                    "finishedAt": "Tue 2026-07-29 18:31:00 CST",
                },
                "stock-analyze-intelligence-artifact-backfill.service": {
                    "activeState": "inactive",
                    "subState": "dead",
                    "result": "success",
                    "exitStatus": 75,
                    "startedAt": "Wed 2026-07-30 13:20:00 CST",
                    "finishedAt": "Wed 2026-07-30 13:20:01 CST",
                },
            },
            "timers": {
                "stock-analyze-market-data.timer": {
                    "activeState": "active",
                    "lastTriggerAt": "Tue 2026-07-29 18:30:00 CST",
                    "nextTriggerAt": "Wed 2026-07-30 18:30:00 CST",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value={
                "pipeline": {
                    "status": "available",
                    "backlog": {"download": 100, "parse": 20, "semantic": 10, "total": 130},
                    "artifactWorkers": {
                        "status": "available",
                        "latestFinishedAt": "2026-07-30T13:20:01",
                    },
                }
            },
        ):
            payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="all",
                now=datetime(2026, 7, 30, 13, 30, 0),
            )

        statuses = {row["key"]: row["status"] for row in payload["mainChain"]}
        self.assertEqual(statuses["intelligence"], "success")
        self.assertEqual(statuses["market_snapshot"], "waiting_schedule")
        workers = {row["key"]: row for row in payload["backgroundWorkers"]}
        self.assertEqual(workers["artifact_backfill"]["status"], "skipped")
        self.assertLessEqual(len(payload["recentRuns"]), 20)
        self.assertEqual(payload["interventions"], [])
```

Add imports:

```python
from datetime import datetime

from stock_analyze.dashboard_workspace_api import (
    build_dashboard_data_intelligence_data,
    build_dashboard_model_research_data,
    build_dashboard_operations_center_data,
)
```

- [ ] **Step 2: Run the test and verify the missing builder failure**

Run:

```bash
python3 -m unittest tests.test_dashboard_workspace_api.DashboardWorkspaceApiTests.test_operations_center_distinguishes_waiting_skip_and_failure -v
```

Expected: FAIL because `build_dashboard_operations_center_data` is not defined.

- [ ] **Step 3: Add the runtime inventory and status mapping**

```python
import re
import shutil
import csv
from collections import deque

from .dashboard_runtime import read_dashboard_runtime


MAIN_CHAIN_SPECS = (
    (
        "intelligence",
        "情报增量",
        ("stock-analyze-intelligence.service",),
        "stock-analyze-intelligence.timer",
    ),
    (
        "market_snapshot",
        "行情与研究快照",
        ("stock-analyze-market-data.service",),
        "stock-analyze-market-data.timer",
    ),
    (
        "research",
        "特征、预测与评估",
        ("stock-analyze-research.service",),
        None,
    ),
    (
        "simulation",
        "双策略及候选模型模拟",
        (
            "stock-analyze-model-iteration.service",
            "stock-analyze-claude-daily.service",
            "stock-analyze-codex-daily.service",
            "stock-analyze-claude-cn-qdii-etf-daily.service",
            "stock-analyze-codex-cn-qdii-etf-daily.service",
        ),
        None,
    ),
    (
        "publish",
        "Dashboard 聚合与通知",
        (
            "stock-analyze-aggregate-dashboard.service",
            "stock-analyze-daily-summary.service",
        ),
        "stock-analyze-daily-summary.timer",
    ),
)

BACKGROUND_SPECS = (
    (
        "artifact_backfill",
        "PDF 下载与解析回填",
        "stock-analyze-intelligence-artifact-backfill.service",
        "stock-analyze-intelligence-artifact-backfill.timer",
    ),
    (
        "reconcile",
        "情报对账",
        "stock-analyze-intelligence-reconcile.service",
        "stock-analyze-intelligence-reconcile.timer",
    ),
    (
        "semantic",
        "LLM 语义抽取",
        "stock-analyze-intelligence-semantic.service",
        "stock-analyze-intelligence-semantic.timer",
    ),
)

TIMER_CADENCE = {
    "stock-analyze-market-data.timer": "daily",
    "stock-analyze-daily-summary.timer": "daily",
    "stock-analyze-intelligence.timer": "daily",
    "stock-analyze-intelligence-reconcile.timer": "daily",
    "stock-analyze-intelligence-artifact-backfill.timer": "daily",
    "stock-analyze-intelligence-semantic.timer": "daily",
    "stock-analyze-ifind-source-audit.timer": "daily",
    "stock-analyze-weekly-trigger.timer": "weekly",
    "stock-analyze-claude-cn-qdii-etf-weekly.timer": "weekly",
    "stock-analyze-codex-cn-qdii-etf-weekly.timer": "weekly",
    "stock-analyze-qdii-research.timer": "weekly",
    "stock-analyze-weekly-summary.timer": "weekly",
    "stock-analyze-monthly-review.timer": "monthly",
    "stock-analyze-model-training.timer": "monthly",
    "stock-analyze-monthly-summary.timer": "monthly",
}

TIMER_LABELS = {
    "stock-analyze-market-data.timer": "行情与研究日链",
    "stock-analyze-daily-summary.timer": "每日运行摘要",
    "stock-analyze-intelligence.timer": "情报增量采集",
    "stock-analyze-intelligence-reconcile.timer": "情报对账",
    "stock-analyze-intelligence-artifact-backfill.timer": "PDF 下载解析回填",
    "stock-analyze-intelligence-semantic.timer": "LLM 语义抽取",
    "stock-analyze-ifind-source-audit.timer": "iFinD 数据源审计",
    "stock-analyze-weekly-trigger.timer": "A股周度复盘",
    "stock-analyze-claude-cn-qdii-etf-weekly.timer": "跨境ETF稳健防守周度复盘",
    "stock-analyze-codex-cn-qdii-etf-weekly.timer": "跨境ETF趋势进攻周度复盘",
    "stock-analyze-qdii-research.timer": "跨境ETF周度研究",
    "stock-analyze-weekly-summary.timer": "每周运行摘要",
    "stock-analyze-monthly-review.timer": "月度策略复盘",
    "stock-analyze-model-training.timer": "月度模型训练",
    "stock-analyze-monthly-summary.timer": "每月运行摘要",
}


def _timestamp_date(value: object) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else None


def _service_status(
    row: dict[str, Any] | None,
    *,
    today: str,
) -> str:
    if not row:
        return "waiting_upstream"
    if row.get("activeState") == "active":
        return "running"
    ran_today = _timestamp_date(row.get("startedAt")) == today
    if ran_today and row.get("result") == "success" and row.get("exitStatus") == 75:
        return "skipped"
    if ran_today and (
        row.get("result") not in {"success", "unknown"}
        or row.get("exitStatus") not in {0, None}
    ):
        return "failed"
    if ran_today and row.get("result") == "success":
        return "success"
    return "waiting_schedule"


def _combine_status(statuses: list[str], *, upstream_ready: bool) -> str:
    if "failed" in statuses:
        return "failed"
    if "running" in statuses:
        return "running"
    if statuses and all(value == "success" for value in statuses):
        return "success"
    if statuses and all(value == "skipped" for value in statuses):
        return "skipped"
    if any(value in {"success", "skipped"} for value in statuses):
        return "running"
    if not upstream_ready:
        return "waiting_upstream"
    return "waiting_schedule"
```

- [ ] **Step 4: Add bounded run-ledger and intervention readers**

```python
def _recent_strategy_runs(
    root: Path,
    scope: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    markets = (
        [scope]
        if scope in competition.MARKETS
        else list(competition.MARKETS)
    )
    rows: list[dict[str, Any]] = []
    for market in markets:
        for public_key, agent, label in PUBLIC_STRATEGIES:
            path = root / "data" / market / agent / "runs.csv"
            if not path.exists() or path.stat().st_size == 0:
                continue
            try:
                with path.open(
                    "r",
                    encoding="utf-8-sig",
                    newline="",
                ) as handle:
                    raw_rows = deque(
                        csv.DictReader(handle),
                        maxlen=limit,
                    )
            except (OSError, csv.Error):
                continue
            for raw in raw_rows:
                rows.append(
                    {
                        "runId": str(raw.get("run_id") or ""),
                        "market": market,
                        "strategyKey": public_key,
                        "strategyLabel": label,
                        "command": str(raw.get("command") or ""),
                        "status": str(raw.get("status") or "unknown"),
                        "startedAt": str(raw.get("started_at") or ""),
                        "finishedAt": str(raw.get("finished_at") or ""),
                        "durationMs": int(raw.get("duration_ms") or 0),
                        "errorSummary": str(raw.get("error_summary") or ""),
                    }
                )
    rows.sort(
        key=lambda row: (row["startedAt"], row["runId"]),
        reverse=True,
    )
    if scope == "exceptions":
        rows = [row for row in rows if row["status"] != "success"]
    return rows[:limit]


def _interventions(
    recent_runs: list[dict[str, Any]],
    *,
    disk_ratio: float,
    artifact_workers: dict[str, Any],
    backlog_total: int,
    now: datetime,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if disk_ratio >= 0.85:
        items.append(
            {
                "key": "disk_capacity",
                "severity": "critical",
                "title": "磁盘使用率超过 85%",
                "evidence": f"{disk_ratio:.1%}",
            }
        )
    credential_terms = (
        "credential",
        "unauthorized",
        "forbidden",
        "invalid token",
        "凭据",
        "密钥",
    )
    for row in recent_runs:
        error = row["errorSummary"].lower()
        if row["status"] != "success" and any(
            term in error for term in credential_terms
        ):
            items.append(
                {
                    "key": f"credential:{row['runId']}",
                    "severity": "critical",
                    "title": f"{row['strategyLabel']} 凭据失效",
                    "evidence": row["errorSummary"][:200],
                }
            )
            break
    latest_finished = artifact_workers.get("latestFinishedAt")
    if backlog_total > 0 and latest_finished:
        try:
            parsed = datetime.fromisoformat(
                str(latest_finished).replace("Z", "+00:00")
            )
            current = now
            if parsed.tzinfo is not None and current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None and current.tzinfo is not None:
                parsed = parsed.replace(tzinfo=current.tzinfo)
            age_hours = (current - parsed).total_seconds() / 3600.0
        except ValueError:
            age_hours = 0.0
        if age_hours > 24 and int(artifact_workers.get("activeLeases") or 0) == 0:
            items.append(
                {
                    "key": "artifact_worker_stale",
                    "severity": "critical",
                    "title": "PDF 回填超过 24 小时没有完成记录",
                    "evidence": str(latest_finished),
                }
            )
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in recent_runs:
        grouped.setdefault(
            (row["market"], row["strategyKey"], row["command"]),
            [],
        ).append(row)
    for key, rows in grouped.items():
        consecutive = 0
        for row in rows:
            if row["status"] == "success":
                break
            consecutive += 1
        if consecutive >= 2:
            items.append(
                {
                    "key": "consecutive_failure:" + ":".join(key),
                    "severity": "critical",
                    "title": f"{rows[0]['strategyLabel']} {rows[0]['command']} 连续失败",
                    "evidence": f"{consecutive} 次",
                }
            )
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        unique[item["key"]] = item
    return list(unique.values())
```

- [ ] **Step 5: Implement the operations builder**

```python
def build_dashboard_operations_center_data(
    *,
    repo_root: str | Path | None = None,
    scope: str = "all",
    now: datetime | None = None,
) -> dict[str, Any]:
    if scope not in {"all", "a_share", "cn_qdii_etf", "exceptions"}:
        from .dashboard_http import InvalidDashboardQuery
        raise InvalidDashboardQuery("scope must be all, a_share, cn_qdii_etf, or exceptions")
    root = _root(repo_root)
    current = now or datetime.now()
    today = current.date().isoformat()
    runtime = read_dashboard_runtime()
    services = runtime.get("services") or {}
    timers = runtime.get("timers") or {}
    upstream_ready = True
    main_chain = []
    for key, label, units, timer_unit in MAIN_CHAIN_SPECS:
        statuses = [
            _service_status(services.get(unit), today=today)
            for unit in units
        ]
        status = _combine_status(statuses, upstream_ready=upstream_ready)
        timer = timers.get(timer_unit) if timer_unit else None
        main_chain.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "primary": (
                    f"{sum(value == 'success' for value in statuses)} / "
                    f"{len(statuses)} 个任务完成"
                ),
                "secondary": (
                    f"下次 {timer.get('nextTriggerAt')}"
                    if timer and timer.get("nextTriggerAt")
                    else "由上游成功后自动触发"
                ),
                "units": [
                    {
                        "unit": unit,
                        "status": statuses[index],
                        **(services.get(unit) or {}),
                    }
                    for index, unit in enumerate(units)
                ],
            }
        )
        upstream_ready = status == "success"

    intelligence = build_dashboard_intelligence_data(
        repo_root=root,
        market="a_share",
        agent="codex",
        limit=1,
    )
    background_workers = []
    for key, label, service_unit, timer_unit in BACKGROUND_SPECS:
        service = services.get(service_unit)
        status = _service_status(service, today=today)
        timer = timers.get(timer_unit) or {}
        background_workers.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "serviceUnit": service_unit,
                "timerUnit": timer_unit,
                "lastResult": (service or {}).get("result"),
                "startedAt": (service or {}).get("startedAt"),
                "finishedAt": (service or {}).get("finishedAt"),
                "nextTriggerAt": timer.get("nextTriggerAt"),
                "backlog": (
                    intelligence["pipeline"]["backlog"]
                    if key == "artifact_backfill"
                    else None
                ),
            }
        )

    schedules = {"daily": [], "weekly": [], "monthly": []}
    for unit, timer in timers.items():
        cadence = TIMER_CADENCE.get(unit)
        if cadence is None:
            continue
        schedules[cadence].append(
            {
                "unit": unit,
                "label": TIMER_LABELS[unit],
                "status": (
                    "active"
                    if timer.get("activeState") == "active"
                    else "unavailable"
                ),
                "lastTriggerAt": timer.get("lastTriggerAt"),
                "nextTriggerAt": timer.get("nextTriggerAt"),
                "automation": "automatic",
            }
        )
    for rows in schedules.values():
        rows.sort(key=lambda row: (str(row["nextTriggerAt"]), row["unit"]))

    recent_runs = _recent_strategy_runs(root, scope)
    try:
        disk = shutil.disk_usage(root)
        disk_ratio = disk.used / disk.total if disk.total else 0.0
    except OSError:
        disk_ratio = 0.0
    payload = {
        "generated_at": _generated_at(),
        "scope": scope,
        "runtime": {
            "status": runtime.get("status") or "unavailable",
            "lastKnownAt": runtime.get("last_known_at"),
            "reason": runtime.get("reason"),
        },
        "mainChain": main_chain,
        "backgroundWorkers": background_workers,
        "schedules": schedules,
        "recentRuns": recent_runs,
        "interventions": _interventions(
            recent_runs,
            disk_ratio=disk_ratio,
            artifact_workers=intelligence["pipeline"]["artifactWorkers"],
            backlog_total=int(
                intelligence["pipeline"]["backlog"]["total"]
            ),
            now=current,
        ),
    }
    if scope == "exceptions":
        payload["mainChain"] = [
            row
            for row in main_chain
            if row["status"] in {"failed", "unavailable"}
        ]
        payload["backgroundWorkers"] = [
            row
            for row in background_workers
            if row["status"] in {"failed", "unavailable"}
        ]
    return agg._json_safe(payload)
```

- [ ] **Step 6: Register the operations-center endpoint**

Add to `_is_dashboard_api_path`:

```python
"/api/dashboard/operations-center.json",
```

Add the builder import and dispatch before reading `market`/`agent` dependent resources:

```python
from .dashboard_workspace_api import (
    build_dashboard_data_intelligence_data,
    build_dashboard_model_research_data,
    build_dashboard_operations_center_data,
)

if canonical_path == "/api/dashboard/operations-center.json":
    scope = (params.get("scope") or ["all"])[0]
    return build_dashboard_operations_center_data(
        repo_root=repo_root,
        scope=scope,
    )
```

Add `"operations-center"` to the route smoke-test resource tuple.

- [ ] **Step 7: Run backend tests**

Run:

```bash
python3 -m unittest tests.test_dashboard_runtime tests.test_dashboard_workspace_api tests.test_cli_dashboard_routes -v
```

Expected: PASS; a prior-day success is not shown as today’s completion, exit 75 is skipped, and ordinary backlog produces no intervention.

- [ ] **Step 8: Commit the operations resource**

```bash
git add stock_analyze/dashboard_workspace_api.py tests/test_dashboard_workspace_api.py stock_analyze/cli.py tests/test_cli_dashboard_routes.py
git commit -m "feat: add read-only operations center resource"
```

### Task 11: Build the Read-Only Operations Workspace

**Files:**
- Modify: `frontend/dashboard/src/workspaceTypes.ts`
- Modify: `frontend/dashboard/src/api.ts`
- Create: `frontend/dashboard/src/OperationsPage.tsx`
- Create: `frontend/dashboard/src/OperationsPage.test.tsx`

- [ ] **Step 1: Write failing operations interaction tests**

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OperationsPage } from "./OperationsPage";

vi.mock("./api", () => ({
  fetchOperationsCenter: vi.fn().mockResolvedValue({
    generated_at: "2026-07-30T13:30:00",
    scope: "all",
    runtime: { status: "available", lastKnownAt: "2026-07-30T13:30:00", reason: null },
    mainChain: [
      { key: "intelligence", label: "情报增量", status: "success", primary: "1 / 1 个任务完成", secondary: "下次 16:30", units: [{ unit: "stock-analyze-intelligence.service", status: "success", result: "success" }] },
      { key: "market_snapshot", label: "行情与研究快照", status: "waiting_schedule", primary: "0 / 1 个任务完成", secondary: "下次 18:30", units: [] },
    ],
    backgroundWorkers: [
      { key: "artifact_backfill", label: "PDF 下载与解析回填", status: "running", serviceUnit: "stock-analyze-intelligence-artifact-backfill.service", timerUnit: "stock-analyze-intelligence-artifact-backfill.timer", lastResult: "success", nextTriggerAt: "13:40", backlog: { download: 100, parse: 20, semantic: 10, total: 130 } },
    ],
    schedules: {
      daily: [{ unit: "stock-analyze-market-data.timer", label: "行情与研究日链", status: "active", lastTriggerAt: "07-29 18:30", nextTriggerAt: "07-30 18:30", automation: "automatic" }],
      weekly: [{ unit: "stock-analyze-weekly-trigger.timer", label: "A股周度复盘", status: "active", lastTriggerAt: "07-26 10:00", nextTriggerAt: "08-02 10:00", automation: "automatic" }],
      monthly: [],
    },
    recentRuns: [{ runId: "daily-1", market: "a_share", strategyKey: "trend", strategyLabel: "趋势进攻", command: "run-daily", status: "success", startedAt: "2026-07-29T18:40:00", finishedAt: "2026-07-29T18:41:00", durationMs: 60000, errorSummary: "" }],
    interventions: [],
  }),
}));

describe("OperationsPage", () => {
  it("shows waiting as waiting and drills into a task without control buttons", async () => {
    const user = userEvent.setup();
    render(<OperationsPage scope="all" refreshToken={0} />);
    expect(await screen.findByText("等待计划时间")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /情报增量/ }));
    expect(screen.getByText("stock-analyze-intelligence.service")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /启动|停止|重跑/ })).not.toBeInTheDocument();
  });

  it("switches daily and weekly schedules", async () => {
    const user = userEvent.setup();
    render(<OperationsPage scope="all" refreshToken={0} />);
    await screen.findByText("今日主任务链");
    const tabs = screen.getByRole("tablist", { name: "周期计划" });
    await user.click(within(tabs).getByRole("tab", { name: "每周" }));
    expect(screen.getByText("stock-analyze-weekly-trigger.timer")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test and verify missing page/fetch failures**

Run:

```bash
cd frontend/dashboard
npm test -- OperationsPage.test.tsx
```

Expected: FAIL because `OperationsPage` and `fetchOperationsCenter` do not exist.

- [ ] **Step 3: Add the operations contracts and fetcher**

```ts
export type OperationsUnit = {
  unit: string;
  status: WorkspaceStatus;
  activeState?: string;
  subState?: string;
  result?: string;
  exitStatus?: number | null;
  startedAt?: string | null;
  finishedAt?: string | null;
};

export type OperationsCenterData = {
  generated_at: string;
  scope: string;
  runtime: {
    status: string;
    lastKnownAt?: string | null;
    reason?: string | null;
  };
  mainChain: (WorkspaceStage & { units: OperationsUnit[] })[];
  backgroundWorkers: {
    key: string;
    label: string;
    status: WorkspaceStatus;
    serviceUnit: string;
    timerUnit: string;
    lastResult?: string | null;
    startedAt?: string | null;
    finishedAt?: string | null;
    nextTriggerAt?: string | null;
    backlog?: Record<string, number> | null;
  }[];
  schedules: Record<"daily" | "weekly" | "monthly", {
    unit: string;
    label: string;
    status: string;
    lastTriggerAt?: string | null;
    nextTriggerAt?: string | null;
    automation: string;
  }[]>;
  recentRuns: {
    runId: string;
    market: string;
    strategyKey: string;
    strategyLabel: string;
    command: string;
    status: string;
    startedAt: string;
    finishedAt: string;
    durationMs: number;
    errorSummary: string;
  }[];
  interventions: {
    key: string;
    severity: string;
    title: string;
    evidence: string;
  }[];
};
```

```ts
import type {
  DataIntelligenceData,
  ModelResearchData,
  OperationsCenterData,
} from "./workspaceTypes";

export function fetchOperationsCenter(
  scope: string,
  signal?: AbortSignal,
): Promise<OperationsCenterData> {
  const params = new URLSearchParams({ scope });
  return fetchJson<OperationsCenterData>(
    `/api/dashboard/operations-center.json?${params.toString()}`,
    signal,
  );
}
```

- [ ] **Step 4: Implement the operations page**

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchOperationsCenter } from "./api";
import { StageFlow } from "./StageFlow";
import {
  BoundedTable,
  DetailPanel,
  WorkspaceStatusBadge,
} from "./WorkspacePrimitives";
import { useWorkspaceResource } from "./useWorkspaceResource";
import type { OperationsCenterData } from "./workspaceTypes";

type Cadence = "daily" | "weekly" | "monthly";

export function OperationsPage({
  scope,
  refreshToken,
}: {
  scope: string;
  refreshToken: number;
}) {
  const loader = useCallback(
    (signal: AbortSignal) => fetchOperationsCenter(scope, signal),
    [scope],
  );
  const resource = useWorkspaceResource<OperationsCenterData>(scope, true, loader);
  const [selected, setSelected] = useState("intelligence");
  const [cadence, setCadence] = useState<Cadence>("daily");
  useEffect(() => setSelected("intelligence"), [scope]);
  useEffect(() => {
    if (refreshToken > 0) resource.refresh();
  }, [refreshToken]);

  const selectedTask = useMemo(
    () => resource.data?.mainChain.find((row) => row.key === selected) ?? null,
    [resource.data, selected],
  );
  if (resource.loading && !resource.data) {
    return <div className="skeleton-grid" aria-label="运行中心加载中"><div /><div /><div /><div /></div>;
  }
  if (!resource.data) {
    return <div className="error-banner" role="alert">运行状态不可用：{resource.error ?? "unknown"}</div>;
  }
  const data = resource.data;
  return (
    <section className="workspace-page operations-page" aria-label="运行中心">
      {data.runtime.status === "unavailable" ? (
        <div className="stale-banner">systemd 状态不可用，运行历史仍显示最后已知记录</div>
      ) : null}
      <section aria-labelledby="daily-chain-title">
        <header className="section-heading"><div><h2 id="daily-chain-title">今日主任务链</h2></div></header>
        <StageFlow stages={data.mainChain} selectedKey={selected} ariaLabel="今日主任务链" onSelect={setSelected} />
      </section>
      {selectedTask ? (
        <DetailPanel title={selectedTask.label} status={selectedTask.status} updatedAt={data.generated_at}>
          <BoundedTable
            rows={selectedTask.units}
            rowKey={(row) => row.unit}
            emptyLabel="等待上游触发，尚无本日执行记录"
            columns={[
              { key: "unit", label: "任务", render: (row) => row.unit },
              { key: "status", label: "状态", render: (row) => row.status },
              { key: "result", label: "结果", render: (row) => row.result ?? "-" },
              { key: "started", label: "开始", render: (row) => row.startedAt ?? "-" },
              { key: "finished", label: "结束", render: (row) => row.finishedAt ?? "-" },
            ]}
          />
        </DetailPanel>
      ) : null}
      <section className="background-worker-section" aria-labelledby="background-worker-title">
        <header className="section-heading"><div><h2 id="background-worker-title">后台队列</h2></div></header>
        <div className="background-worker-grid">
          {data.backgroundWorkers.map((worker) => (
            <article key={worker.key}>
              <header><strong>{worker.label}</strong><WorkspaceStatusBadge status={worker.status} /></header>
              <dl>
                <div><dt>上次结果</dt><dd>{worker.lastResult ?? "-"}</dd></div>
                <div><dt>下次触发</dt><dd>{worker.nextTriggerAt ?? "-"}</dd></div>
                <div><dt>当前积压</dt><dd>{worker.backlog?.total ?? 0}</dd></div>
              </dl>
            </article>
          ))}
        </div>
      </section>
      <section className="schedule-section" aria-labelledby="schedule-title">
        <header className="section-heading"><div><h2 id="schedule-title">周期计划</h2></div></header>
        <div role="tablist" aria-label="周期计划" className="workspace-tabs">
          {(["daily", "weekly", "monthly"] as Cadence[]).map((key) => (
            <button key={key} type="button" role="tab" aria-selected={cadence === key} className={cadence === key ? "active" : ""} onClick={() => setCadence(key)}>
              {{ daily: "每日", weekly: "每周", monthly: "每月" }[key]}
            </button>
          ))}
        </div>
        <BoundedTable
          rows={data.schedules[cadence]}
          rowKey={(row) => row.unit}
          emptyLabel="当前周期没有已安装计划"
          columns={[
            { key: "unit", label: "任务", render: (row) => `${row.label} · ${row.unit}` },
            { key: "status", label: "状态", render: (row) => row.status },
            { key: "last", label: "上次触发", render: (row) => row.lastTriggerAt ?? "-" },
            { key: "next", label: "下次触发", render: (row) => row.nextTriggerAt ?? "-" },
            { key: "automation", label: "执行方式", render: (row) => row.automation },
          ]}
        />
      </section>
      <section className="operations-lower-grid">
        <div>
          <header className="section-heading"><div><h2>最近运行</h2></div></header>
          <BoundedTable
            rows={data.recentRuns}
            rowKey={(row) => row.runId}
            emptyLabel="没有匹配的运行记录"
            columns={[
              { key: "time", label: "开始", render: (row) => row.startedAt },
              { key: "market", label: "市场", render: (row) => row.market },
              { key: "strategy", label: "策略", render: (row) => row.strategyLabel },
              { key: "command", label: "任务", render: (row) => row.command },
              { key: "status", label: "结果", render: (row) => row.status },
            ]}
          />
        </div>
        <div className="intervention-panel">
          <header className="section-heading"><div><h2>需要你介入</h2></div></header>
          {data.interventions.length === 0 ? <p>当前无需人工介入</p> : data.interventions.map((item) => (
            <article key={item.key}><strong>{item.title}</strong><small>{item.evidence}</small></article>
          ))}
        </div>
      </section>
    </section>
  );
}
```

- [ ] **Step 5: Run the operations-page tests**

Run:

```bash
cd frontend/dashboard
npm test -- OperationsPage.test.tsx
```

Expected: PASS; waiting remains non-failure, timer tabs switch data, and no service-control command appears.

- [ ] **Step 6: Commit the operations page**

```bash
git add frontend/dashboard/src/workspaceTypes.ts frontend/dashboard/src/api.ts frontend/dashboard/src/OperationsPage.tsx frontend/dashboard/src/OperationsPage.test.tsx
git commit -m "feat: add read-only operations workspace"
```

### Task 12: Integrate the Five Workspaces and Preserve the Strategy Workbench

**Files:**
- Create: `frontend/dashboard/src/StrategyWorkspacePage.tsx`
- Create: `frontend/dashboard/src/StrategyWorkspacePage.test.tsx`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/App.test.tsx`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `stock_analyze/dashboard_api.py:1384-1432`
- Modify: `tests/test_dashboard_resource_api.py:677-748`
- Modify: `frontend/dashboard/src/SystemOverviewPanel.tsx`
- Modify: `frontend/dashboard/src/SystemOverviewPanel.test.tsx`

- [ ] **Step 1: Add failing integration tests for request boundaries and canonical URLs**

Mock the page components at the top of `App.test.tsx` so this test isolates routing from resource rendering:

```tsx
vi.mock("./ModelResearchPage", () => ({
  ModelResearchPage: ({ market }: { market: string }) => <div>模型研究页面 {market}</div>,
}));
vi.mock("./DataIntelligencePage", () => ({
  DataIntelligencePage: ({ market }: { market: string }) => <div>数据与情报页面 {market}</div>,
}));
vi.mock("./OperationsPage", () => ({
  OperationsPage: ({ scope }: { scope: string }) => <div>运行中心页面 {scope}</div>,
}));

it.each([
  ["?view=model-research&market=a_share", "模型研究页面 a_share"],
  ["?view=data-intelligence&market=a_share", "数据与情报页面 a_share"],
  ["?view=operations&scope=all", "运行中心页面 all"],
])("dispatches one canonical workspace for %s", async (search, expectedText) => {
  window.history.replaceState({}, "", `/app.html${search}`);

  render(<App />);
  expect(await screen.findByText(expectedText)).toBeInTheDocument();
  expect(screen.queryAllByText(/页面/)).toHaveLength(1);
});

it("canonicalizes legacy model and agent URLs once", async () => {
  window.history.replaceState({}, "", "/app.html?view=model-shadow&market=a_share&agent=model_shadow");
  render(<App />);
  await waitFor(() => {
    expect(window.location.search).toBe("?view=model-research&market=a_share");
  });
  expect(window.location.search).not.toContain("agent=");
});
```

Add a regression assertion to `StrategyWorkspacePage.test.tsx`:

```tsx
it("renders the existing portfolio section before performance and target orders", async () => {
  render(
    <StrategyWorkspacePage
      market="cn_qdii_etf"
      mode="detail"
      strategy="trend"
      search=""
      onSelectStrategy={vi.fn()}
      refreshToken={0}
    />,
  );
  const portfolio = await screen.findByRole("region", { name: "当前持仓" });
  const performance = screen.getByRole("region", { name: "净值与基准" });
  const orders = screen.getByRole("region", { name: "目标订单" });
  expect(portfolio.compareDocumentPosition(performance) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(performance.compareDocumentPosition(orders) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
```

- [ ] **Step 2: Run the integration tests and verify legacy composition failures**

Run:

```bash
cd frontend/dashboard
npm test -- App.test.tsx StrategyWorkspacePage.test.tsx
```

Expected: FAIL because `App` still parses old views and non-strategy pages still load summary/detail resources.

- [ ] **Step 3: Make system overview self-contained**

In `build_dashboard_system_overview_data`, read the existing bounded summary:

```python
summary = agg.build_dashboard_summary_data(
    repo_root=root,
    markets=list(competition.MARKETS),
)
```

Add the market list to the returned payload:

```python
{
    "generated_at": _generated_at(),
    "markets": summary["markets"],
    "models": models,
    "strategy_model_usage": _latest_strategy_model_usage(root),
    "intelligence": {
        "pipeline": intelligence["pipeline"],
        "extraction": intelligence["extraction"],
        "factorSupply": intelligence["factorSupply"],
        "modelImpact": intelligence["modelImpact"],
        "decisions": intelligence["decisions"],
        "recentEvents": intelligence.get("rowsByDecision", {}).get("canonical", [])[:5],
    },
}
```

Update the backend test expected key set to include `"markets"` and assert the list contains `a_share` and `cn_qdii_etf`.

Update `SystemOverviewData` in `types.ts`:

```ts
export type SystemOverviewData = {
  generated_at: string;
  markets: MarketSummary[];
  models: SystemModelOverview[];
  strategy_model_usage: StrategyModelUsage[];
  intelligence: Pick<
    IntelligenceSummary,
    "pipeline" | "extraction" | "factorSupply" | "modelImpact" | "decisions"
  > & {
    recentEvents: IntelligenceDecisionRow[];
  };
};
```

Remove the `summary` prop from `SystemOverviewPanel`, replace `summary.markets` with `data.markets`, and change its callback to:

```tsx
type Props = {
  refreshToken?: number;
  onNavigate: (route: WorkspaceRoute) => void;
};
```

Use only public routes in every button:

```tsx
onNavigate({ view: "strategy", mode: "compare", market: "cn_qdii_etf" })
onNavigate({ view: "strategy", mode: "detail", market: item.market, strategy: item.agent === "claude" ? "defensive" : "trend" })
onNavigate({ view: "model-research", market: item.market })
onNavigate({ view: "data-intelligence", market: "a_share" })
```

The internal agent check remains inside this adapter expression and never reaches the URL or visible label.

- [ ] **Step 4: Extract the existing strategy composition without changing its order**

Move the current comparison/detail JSX and its local helpers from `App.tsx` into `StrategyWorkspacePage.tsx`. Use this public prop contract:

```tsx
import type { StrategyKey } from "./workspaceRoute";

type Props = {
  market: "a_share" | "cn_qdii_etf";
  mode: "compare" | "detail";
  strategy?: StrategyKey;
  search: string;
  refreshToken: number;
  onSelectStrategy: (strategy: StrategyKey) => void;
  onBusyChange?: (busy: boolean) => void;
};
```

Load the bounded strategy summary inside this page, and convert the public strategy at the single detail-API boundary:

```tsx
const summaryLoader = useCallback(
  (signal: AbortSignal) => fetchSummary(signal),
  [],
);
const summaryResource = useWorkspaceResource(
  `strategy:${market}`,
  true,
  summaryLoader,
);
const agent = agentForStrategy(strategy ?? "trend");
const detailEnabled = mode === "detail";
const { detail, error, loading, reload } = useDashboardData(
  market,
  agent,
  detailEnabled,
);
const marketSummary = summaryResource.data?.markets.find(
  (item) => item.market === market,
);

useEffect(() => {
  onBusyChange?.(summaryResource.loading || loading);
}, [loading, onBusyChange, summaryResource.loading]);

useEffect(() => {
  if (refreshToken > 0) {
    summaryResource.refresh();
    if (detailEnabled) void reload();
  }
}, [detailEnabled, refreshToken, reload, summaryResource.refresh]);

if (mode === "compare") {
  return (
    <CompetitionPanel
      comparison={marketSummary?.comparison}
      currency={marketSummary?.currency ?? "¥"}
      onSelectAgent={(selectedAgent) =>
        onSelectStrategy(selectedAgent === "claude" ? "defensive" : "trend")
      }
    />
  );
}

const selectedAgentSummary = marketSummary?.agents.find(
  (item) => item.agent === agent,
);
const positions = detail?.positions.rows ?? [];
const rawOrders = detail?.orders.rows ?? [];
const events = detail?.activity.rows ?? [];
const runs = detail?.runs.rows ?? [];
const orders = rawOrders.filter((row) => matchesSearch(row, search));
const filteredPositions = positions.filter((row) => matchesSearch(row, search));
const filteredEvents = events.filter((row) => matchesSearch(row, search));
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
```

Keep this exact page order in the returned detail branch:

```tsx
return (
  <div className={`strategy-workspace strategy-workspace-${agent}`}>
    <section className="metric-strip" role="region" aria-label="账户总览">
      <MetricTile
        label="账户净值"
        value={latest?.total_value_display ?? selectedAgentSummary?.nav.latest_display ?? "-"}
        helper={`估值日 ${latest?.date ?? selectedAgentSummary?.nav.date ?? "-"}`}
        icon={WalletCards}
      />
      <MetricTile
        label="累计收益"
        value={latest?.return_display ?? selectedAgentSummary?.nav.return_display ?? "-"}
        helper="已扣模拟交易成本"
        icon={BarChart3}
        tone={(latest?.return ?? selectedAgentSummary?.nav.return ?? 0) >= 0 ? "positive" : "negative"}
      />
      <MetricTile
        label="市场基准"
        value={formatPercent(latest?.benchmark_return)}
        helper={benchmarkLabel}
        icon={Gauge}
        tone={(latest?.benchmark_return ?? 0) >= 0 ? "positive" : "negative"}
      />
      <MetricTile
        label={positions.length ? "持仓证券" : "计划证券"}
        value={String(holdingCount)}
        helper={positions.length ? `${detail?.positions.summary.market_value_display || "-"} 已配置` : `${rawOrders.length} 笔等待成交`}
        icon={CircleDollarSign}
      />
    </section>
    <PortfolioSection
      positions={filteredPositions}
      planned={orders}
      currency={currency}
      onSelect={openDrawer}
    />
    <GovernancePanel data={detail?.governance} />
    <section className="performance-section terminal-section" role="region" aria-label="净值与基准">
      <header className="section-heading">
        <div>
          <span className="section-kicker"><BarChart3 size={14} aria-hidden="true" />PERFORMANCE</span>
          <h2>净值与市场基准</h2>
          <p>鼠标移动可查看每个交易日的组合收益、基准收益和超额收益</p>
        </div>
        <div className="section-stat">
          <span>数据更新时间</span>
          <strong>{String(detail?.generated_at ?? "-").replace("T", " ")}</strong>
        </div>
      </header>
      <PerformanceChart points={detail?.nav.series ?? []} benchmarkLabel={benchmarkLabel} />
    </section>
    <div className="prediction-workbench">
      <PredictionPanel summary={detail?.prediction_summary} modelDriven={false} />
      <div className="prediction-side-stack">
        <AlertCenter alerts={detail?.alerts} />
        <ModelHealthPanel health={detail?.model_health} regimes={detail?.regimes} sources={detail?.source_health} />
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
      <StrategyBrief strategy={strategyProfile} reportHref={detail?.weekly_report.href} />
    </div>
    <RuntimeHistory rows={runs} onSelect={openDrawer} />
    <TargetOrders rows={orders} currency={currency} onSelect={openDrawer} />
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
```

Move `MetricTile`, `TargetOrders`, drawer state, filtering, and formatting helpers from the current `App.tsx` into the new file without changing their code, labels, CSS classes, or component props. Do not edit `PortfolioViews.tsx`.

- [ ] **Step 5: Reduce App to route, shell, refresh, and page dispatch**

Use this route import:

```tsx
import {
  parseWorkspaceRoute,
  routeSearchMatches,
  serializeWorkspaceRoute,
  type DashboardMarket,
  type WorkspaceRoute,
} from "./workspaceRoute";
```

Add pure title/subtitle helpers:

```tsx
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
  if (route.view === "model-research") return `${marketLabel} · 训练、验收、模拟与采用`;
  return `${marketLabel} · 数据供给与实际使用`;
}
```

Use the pure route as initial state and canonicalize with `replaceState`:

```tsx
function marketFromRoute(route: WorkspaceRoute): DashboardMarket | null {
  if (route.view === "strategy"
    || route.view === "model-research"
    || route.view === "data-intelligence") {
    return route.market;
  }
  if (route.view === "operations"
    && (route.scope === "a_share" || route.scope === "cn_qdii_etf")) {
    return route.scope;
  }
  return null;
}

const [route, setRoute] = useState<WorkspaceRoute>(() =>
  parseWorkspaceRoute(window.location.search),
);
const [marketContext, setMarketContext] = useState<DashboardMarket>(() =>
  marketFromRoute(parseWorkspaceRoute(window.location.search))
    ?? "cn_qdii_etf"
);
const [search, setSearch] = useState("");
const [autoRefresh, setAutoRefresh] = useState(true);
const [refreshToken, setRefreshToken] = useState(0);
const [pageBusy, setPageBusy] = useState(false);

const navigate = useCallback((
  next: WorkspaceRoute,
  mode: "push" | "replace" = "push",
) => {
  setRoute(next);
  setSearch("");
  setPageBusy(false);
  const url = `${window.location.pathname}?${serializeWorkspaceRoute(next)}`;
  window.history[mode === "push" ? "pushState" : "replaceState"]({}, "", url);
}, []);

useEffect(() => {
  if (!routeSearchMatches(window.location.search, route)) {
    navigate(route, "replace");
  }
}, [navigate, route]);

useEffect(() => {
  const restore = () => setRoute(parseWorkspaceRoute(window.location.search));
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
```

Render exactly one workspace page:

```tsx
let page: ReactNode;
if (route.view === "system") {
  page = <SystemOverviewPanel refreshToken={refreshToken} onNavigate={navigate} />;
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
  page = <ModelResearchPage market={route.market} refreshToken={refreshToken} />;
} else if (route.view === "data-intelligence") {
  page = <DataIntelligencePage market={route.market} refreshToken={refreshToken} />;
} else {
  page = <OperationsPage scope={route.scope} refreshToken={refreshToken} />;
}
```

Wrap it with `WorkspaceShell` and show the search input only for strategy detail:

```tsx
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
  headerActions={route.view === "strategy" && route.mode === "detail" ? (
    <label className="search-box">
      <Search size={16} aria-hidden="true" />
      <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索证券、市场或账户" aria-label="搜索证券" />
    </label>
  ) : null}
>
  {page}
</WorkspaceShell>
```

Delete the old route constants, `routeFromLocation`, `routeSearch`, `normalizeRoute`, `ModelIterationLifecycle`, and the old standalone intelligence/model branches from `App.tsx`. Keep model simulation portfolio data available only through the model research endpoint and page.

- [ ] **Step 6: Run focused integration tests**

Run:

```bash
cd frontend/dashboard
npm test -- workspaceRoute.test.ts WorkspaceShell.test.tsx StrategyWorkspacePage.test.tsx SystemOverviewPanel.test.tsx App.test.tsx
```

Expected: PASS; each non-strategy workspace issues only its dedicated request, browser history works, and no canonical URL contains `agent`.

- [ ] **Step 7: Verify the portfolio source file was not touched**

Run:

```bash
git diff --exit-code -- frontend/dashboard/src/PortfolioViews.tsx
```

Expected: exit code 0 and no output.

- [ ] **Step 8: Commit the five-workspace integration**

```bash
git add frontend/dashboard/src/App.tsx frontend/dashboard/src/App.test.tsx frontend/dashboard/src/StrategyWorkspacePage.tsx frontend/dashboard/src/StrategyWorkspacePage.test.tsx frontend/dashboard/src/SystemOverviewPanel.tsx frontend/dashboard/src/SystemOverviewPanel.test.tsx frontend/dashboard/src/types.ts stock_analyze/dashboard_api.py tests/test_dashboard_resource_api.py
git commit -m "feat: integrate five dashboard workspaces"
```

### Task 13: Finish Dark-Terminal Styling and Responsive Behavior

**Files:**
- Modify: `frontend/dashboard/src/styles.css`
- Modify: `frontend/dashboard/src/WorkspaceShell.test.tsx`
- Modify: `frontend/dashboard/src/StageFlow.test.tsx`

- [ ] **Step 1: Add structural assertions for narrow screens and stable controls**

Add class-level tests:

```tsx
it("keeps the market control and workspace navigation in dedicated containers", () => {
  renderShell({ view: "model-research", market: "a_share" });
  expect(screen.getByRole("navigation", { name: "市场范围" })).toHaveClass("workspace-scope-slot");
  expect(screen.getByRole("navigation", { name: "工作区" })).toHaveClass("rail-analysis-nav");
});
```

Add to `StageFlow.test.tsx`:

```tsx
it("uses a stable flow container and node dimensions", () => {
  render(
    <StageFlow
      ariaLabel="运行链"
      selectedKey="a"
      onSelect={() => undefined}
      stages={[{ key: "a", label: "任务", status: "running", primary: "1", secondary: "处理中" }]}
    />,
  );
  expect(screen.getByRole("group", { name: "运行链" })).toHaveClass("stage-flow");
  expect(screen.getByRole("button", { name: /任务/ })).toHaveClass("stage-node");
});
```

- [ ] **Step 2: Add the production visual rules using existing tokens**

Append and consolidate these rules in `styles.css`:

```css
.workspace-page {
  display: grid;
  gap: 18px;
  min-width: 0;
  padding: 18px 22px 28px;
}

.workspace-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  width: fit-content;
  color: var(--muted);
  font-size: 12px;
  line-height: 1;
}

.workspace-status.status-ok { color: var(--accent); }
.workspace-status.status-active { color: var(--accent); }
.workspace-status.status-research { color: var(--warning); }
.workspace-status.status-warn { color: var(--positive); }

.stage-flow {
  display: grid;
  grid-template-columns: repeat(var(--stage-count, 5), minmax(144px, 1fr));
  gap: 20px;
  min-width: 0;
}

.stage-flow-item {
  position: relative;
  min-width: 0;
}

.stage-node {
  width: 100%;
  min-height: 148px;
  display: grid;
  grid-template-rows: auto auto auto 1fr auto;
  align-content: start;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--text);
  text-align: left;
}

.stage-node:hover,
.stage-node:focus-visible,
.stage-node.active {
  border-color: var(--accent);
  background: var(--panel-2);
  outline: none;
}

.stage-node b,
.stage-node strong,
.stage-node small {
  min-width: 0;
  overflow-wrap: anywhere;
}

.stage-node b { font-size: 18px; }
.stage-node small { color: var(--muted); }
.stage-index { color: var(--faint); font-variant-numeric: tabular-nums; }

.stage-link {
  position: absolute;
  top: 66px;
  right: -19px;
  color: var(--line-strong);
}

.workspace-detail-panel,
.usage-matrix-section,
.background-worker-section,
.schedule-section,
.intervention-panel {
  min-width: 0;
  border-top: 1px solid var(--line);
  padding-top: 16px;
}

.workspace-detail-panel > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 14px;
}

.workspace-detail-panel > header > div {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.workspace-detail-panel h2,
.supply-lanes h2,
.section-heading h2 {
  margin: 0;
  color: var(--text);
  font-size: 18px;
  letter-spacing: 0;
}

.workspace-detail-body,
.detail-stack {
  display: grid;
  gap: 14px;
  min-width: 0;
}

.workspace-metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  gap: 1px;
  margin: 0;
  background: var(--line);
  border: 1px solid var(--line);
}

.workspace-metric-grid > div {
  min-width: 0;
  padding: 12px;
  background: var(--panel);
}

.workspace-metric-grid dt {
  color: var(--muted);
  font-size: 12px;
}

.workspace-metric-grid dd {
  margin: 7px 0 0;
  color: var(--text);
  font-size: 17px;
  overflow-wrap: anywhere;
}

.bounded-table-wrap {
  min-width: 0;
  overflow-x: auto;
  border: 1px solid var(--line);
}

.bounded-table {
  width: 100%;
  min-width: 680px;
  border-collapse: collapse;
  font-size: 13px;
}

.bounded-table th,
.bounded-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  text-align: left;
  vertical-align: top;
}

.bounded-table th {
  color: var(--faint);
  background: var(--panel-2);
  font-weight: 600;
}

.bounded-table td strong { color: var(--text); }

.supply-lanes {
  display: grid;
  grid-template-columns: 1fr;
  gap: 18px;
}

.supply-lanes > section {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.data-intelligence-page .stage-flow {
  --stage-count: 4;
}

.background-worker-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.background-worker-grid article {
  min-width: 0;
  padding: 13px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}

.background-worker-grid article > header {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.background-worker-grid dl {
  display: grid;
  gap: 7px;
  margin: 12px 0 0;
}

.background-worker-grid dl div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.background-worker-grid dt { color: var(--faint); }
.background-worker-grid dd { margin: 0; color: var(--muted); text-align: right; overflow-wrap: anywhere; }

.workspace-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 10px;
}

.workspace-tabs button {
  min-height: 32px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--muted);
}

.workspace-tabs button.active {
  border-color: var(--accent);
  color: var(--text);
}

.operations-lower-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(260px, 1fr);
  gap: 18px;
  min-width: 0;
}

.stale-banner {
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--warning) 55%, var(--line));
  border-radius: var(--radius);
  color: var(--warning);
  background: var(--panel);
}

@media (max-width: 980px) {
  .stage-flow {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .stage-node {
    min-height: 112px;
  }

  .stage-link {
    display: none;
  }

  .workspace-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .background-worker-grid,
  .operations-lower-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .workspace-page {
    padding: 14px 12px 24px;
  }

  .workspace-scope-slot {
    min-height: 92px;
  }

  .workspace-metric-grid {
    grid-template-columns: 1fr;
  }

  .workspace-detail-panel > header {
    align-items: flex-start;
  }
}
```

Do not introduce new color literals except transparent values required by existing focus behavior. Use current `--bg`, `--panel*`, `--text`, `--muted`, `--line*`, `--accent`, `--positive`, `--negative`, `--warning`, and `--radius`.

- [ ] **Step 3: Run frontend tests and production build**

Run:

```bash
cd frontend/dashboard
npm test
npm run build
```

Expected: all Vitest suites PASS; TypeScript and Vite build complete without warnings about missing exports or oversized chunks caused by the new pages.

- [ ] **Step 4: Commit the visual integration**

```bash
git add frontend/dashboard/src/styles.css frontend/dashboard/src/WorkspaceShell.test.tsx frontend/dashboard/src/StageFlow.test.tsx
git commit -m "style: align five workspaces with dashboard terminal theme"
```

### Task 14: Add Partial-Failure Resilience, Performance Gates, and Harness Canaries

**Files:**
- Modify: `stock_analyze/dashboard_workspace_api.py`
- Modify: `tests/test_dashboard_workspace_api.py`
- Modify: `frontend/dashboard/src/workspaceTypes.ts`
- Modify: `frontend/dashboard/src/ModelResearchPage.tsx`
- Modify: `frontend/dashboard/src/DataIntelligencePage.tsx`
- Modify: `frontend/dashboard/src/OperationsPage.tsx`
- Modify: `scripts/deploy-app-to-ecs.sh`
- Modify: `scripts/system-audit.sh`
- Modify: `docs/system-harness.md`
- Modify: `docs/system-overview.md`

- [ ] **Step 1: Add failing tests for section-level degradation**

Add these methods inside `DashboardWorkspaceApiTests`:

```python
    def test_model_resource_keeps_simulation_when_model_health_is_unreadable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            side_effect=agg.DashboardDataError("model_health"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value={
                "status": "available",
                "candidate": {"model_version": "A20-V005"},
                "selected_count": 0,
            },
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_research_source_health",
            return_value=[],
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value=[],
        ):
            payload = build_dashboard_model_research_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        self.assertEqual(payload["training"]["models"], [])
        self.assertEqual(payload["simulation"]["candidate"]["model_version"], "A20-V005")
        self.assertEqual(
            payload["errors"],
            [{"resource": "model_health", "reason": "unavailable"}],
        )

    def test_data_resource_keeps_structured_lane_when_intelligence_is_unreadable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
            return_value={},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            return_value={"status": "available", "models": []},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value={},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            side_effect=agg.DashboardDataError("intelligence"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value=[],
        ):
            payload = build_dashboard_data_intelligence_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        self.assertTrue(payload["structured"]["sources"])
        self.assertEqual(payload["intelligence"]["pipeline"]["status"], "unavailable")
        self.assertEqual(
            payload["errors"],
            [{"resource": "intelligence", "reason": "unavailable"}],
        )
```

Add this import to the test module:

```python
from stock_analyze import dashboard_aggregator as agg
```

- [ ] **Step 2: Implement a bounded section reader and explicit fallbacks**

Add to `dashboard_workspace_api.py`:

```python
WORKSPACE_READ_ERRORS = (
    OSError,
    TypeError,
    ValueError,
    agg.DashboardDataError,
)


def _safe_workspace_read(
    errors: list[dict[str, str]],
    resource: str,
    fallback: Any,
    reader,
    *args,
    **kwargs,
):
    try:
        return reader(*args, **kwargs)
    except WORKSPACE_READ_ERRORS:
        errors.append({"resource": resource, "reason": "unavailable"})
        return fallback


def _empty_intelligence_workspace() -> dict[str, Any]:
    return {
        "pipeline": {
            "status": "unavailable",
            "documents": 0,
            "artifacts": {},
            "stages": {
                "catalogued": 0,
                "pdfReady": 0,
                "parsed": 0,
                "semanticCompleted": 0,
                "canonicalEvents": 0,
            },
            "backlog": {
                "download": 0,
                "parse": 0,
                "semantic": 0,
                "total": 0,
            },
            "sources": [],
            "snapshotGeneratedAt": None,
            "artifactWorkers": {
                "status": "unavailable",
                "activeLeases": 0,
                "leasedDocuments": 0,
                "completedDocuments": 0,
                "downloadedDocuments": 0,
                "parsedDocuments": 0,
                "latestFinishedAt": None,
                "stages": {
                    stage: {
                        "leased": 0,
                        "importing": 0,
                        "imported": 0,
                        "partial": 0,
                        "failed": 0,
                        "expired": 0,
                    }
                    for stage in ("download", "parse")
                },
            },
        },
        "extraction": {
            "status": "unavailable",
            "semanticRuns": {},
            "decisions": {
                "canonical": 0,
                "no_event": 0,
                "quarantined": 0,
                "failed": 0,
            },
            "latestBatch": None,
            "contract": {},
        },
        "factorSupply": {
            "status": "unavailable",
            "snapshotDate": None,
            "rows": 0,
            "reportName": None,
            "factorSet": None,
            "factorSets": [],
            "suppliedFactors": 0,
            "modelEligible": False,
            "modelEligibleFactors": [],
            "factors": [],
            "lifecycleCounts": {},
        },
        "modelImpact": {
            "status": "unavailable",
            "asOf": None,
            "snapshotDate": None,
            "reportName": None,
            "factorSet": None,
            "qualifiedHorizons": 0,
            "activation": "unavailable",
            "adopted": False,
            "activeFactors": [],
            "iterationFactors": [],
            "reason": "intelligence_status_unavailable",
            "horizons": [],
        },
        "decisions": {
            "canonical": 0,
            "no_event": 0,
            "quarantined": 0,
            "failed": 0,
        },
    }
```

Refactor `_model_rows` to accept a health payload instead of reading it:

```python
def _model_rows(
    root: Path,
    market: str,
    health: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in health.get("models") or []:
        metrics = raw.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        feature_columns = sorted(
            {str(value) for value in raw.get("feature_columns") or [] if value}
        )
        horizon = int(raw.get("horizon") or 0)
        model_version = str(raw.get("model_version") or "")
        registry_record = _model_registry_record(
            root,
            market,
            horizon,
            model_version,
        )
        artifact_ref = _model_artifact_ref(
            root,
            market,
            horizon,
            model_version,
            registry_record,
        )
        rows.append(
            {
                "modelVersion": model_version,
                "horizon": horizon,
                "algorithmFamily": _algorithm_family(raw),
                "trainedAt": (
                    raw.get("trained_at")
                    or raw.get("created_at")
                    or registry_record.get("registered_at")
                ),
                "sampleSupport": int(raw.get("sample_support") or 0),
                "featureColumns": feature_columns,
                "artifactRef": artifact_ref,
                "artifactStatus": "available" if artifact_ref else "missing",
                "gatePassed": raw.get("gate_passed") is True,
                "gateReasons": list(raw.get("gate_reasons") or []),
                "shadowCycles": int(raw.get("shadow_cycles") or 0),
                "shadowCyclesRemaining": int(raw.get("shadow_cycles_remaining") or 0),
                "isChampion": raw.get("is_champion") is True,
                "pointInTimeAudit": metrics.get("point_in_time_audit"),
                "candidateFeatureCount": int(metrics.get("candidate_feature_count") or len(feature_columns)),
                "metrics": {
                    key: metrics.get(key)
                    for key in MODEL_METRIC_KEYS
                    if key in metrics
                },
            }
        )
    return sorted(rows, key=lambda row: (row["horizon"], row["modelVersion"]))
```

At the start of each builder, create `errors: list[dict[str, str]] = []`. Read model health, source health, iteration state, formal usage, and intelligence through `_safe_workspace_read`; use `{}`, `[]`, `{"status": "unavailable", "models": []}`, and `_empty_intelligence_workspace()` as the exact fallbacks.

For model research:

```python
health = _safe_workspace_read(
    errors,
    "model_health",
    {"status": "unavailable", "models": []},
    agg._read_model_health,
    root,
    market,
)
models = _model_rows(root, market, health)
source_health = _safe_workspace_read(
    errors,
    "source_health",
    [],
    agg._read_research_source_health,
    root,
    market,
)
iteration = _safe_workspace_read(
    errors,
    "model_iteration",
    {"status": "unavailable", "candidate": None, "champion": None},
    agg._read_model_iteration_status,
    root,
    market,
)
all_usage = _safe_workspace_read(
    errors,
    "strategy_model_usage",
    [],
    _latest_strategy_model_usage,
    root,
)
usage = [
    row for row in all_usage if row.get("market") == market
]
```

For data/intelligence, replace `_model_feature_evidence` with a payload-only helper:

```python
def _model_feature_evidence_from_health(
    health: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    models = list(health.get("models") or [])
    by_version = {
        str(row.get("model_version")): sorted(
            {str(value) for value in row.get("feature_columns") or [] if value}
        )
        for row in models
        if row.get("model_version")
    }
    audited = sum(
        1
        for row in models
        if isinstance(row.get("metrics"), dict)
        and row["metrics"].get("point_in_time_audit") is True
    )
    return by_version, {
        "status": "available" if models else "unavailable",
        "modelCount": len(models),
        "pointInTimeAuditedModels": audited,
        "pointInTimeFailedModels": max(0, len(models) - audited),
        "missingRateStatus": "not_recorded",
        "outlierStatus": "not_recorded",
    }


model_health = _safe_workspace_read(
    errors,
    "model_health",
    {"status": "unavailable", "models": []},
    agg._read_model_health,
    root,
    market,
)
model_features, quality = _model_feature_evidence_from_health(model_health)
iteration = _safe_workspace_read(
    errors,
    "model_iteration",
    {"status": "unavailable", "candidate": None},
    agg._read_model_iteration_status,
    root,
    market,
)
formal_usage = _safe_workspace_read(
    errors,
    "strategy_model_usage",
    [],
    _latest_strategy_model_usage,
    root,
)
formal_usage = [
    row for row in formal_usage if row.get("market") == market
]
```

For data/intelligence and operations, read intelligence with:

```python
intelligence = _safe_workspace_read(
    errors,
    "intelligence",
    _empty_intelligence_workspace(),
    build_dashboard_intelligence_data,
    repo_root=root,
    market=market,
    agent="codex",
    limit=1,
)
```

The operations builder uses `market="a_share"` in that call and passes its own `errors` list. Do not include exception messages or local paths in the API response.

In each existing response literal, insert the exact entry `"errors": errors,`
immediately after `"generated_at": _generated_at(),`. This is additive: do
not replace or suppress any successfully built section when another reader
fails.

- [ ] **Step 3: Surface partial errors without hiding valid sections**

Append to all three resource types:

```ts
errors?: { resource: string; reason: string }[];
```

Immediately inside each page root, render:

```tsx
{data.errors?.length ? (
  <div className="error-banner" role="status">
    部分状态不可用：{data.errors.map((item) => item.resource).join("、")}
  </div>
) : null}
```

Keep the available stage flows, matrices, schedules, and histories rendered below the banner.

- [ ] **Step 4: Add a shared payload-size test for all three resources**

Add this method inside `DashboardWorkspaceApiTests`:

```python
    def test_workspace_payloads_remain_bounded(self) -> None:
        builders = (
            (
                build_dashboard_model_research_data,
                {"market": "a_share"},
            ),
            (
                build_dashboard_data_intelligence_data,
                {"market": "a_share"},
            ),
            (
                build_dashboard_operations_center_data,
                {"scope": "all"},
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for builder, kwargs in builders:
                with self.subTest(builder=builder.__name__):
                    payload = builder(repo_root=root, **kwargs)
                    encoded = json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    self.assertLess(len(encoded), 250_000)
                    for key in ("rows", "recentRuns"):
                        if isinstance(payload.get(key), list):
                            self.assertLessEqual(len(payload[key]), 20)
```

Use focused mocks for `read_dashboard_runtime`, model health, iteration status, and intelligence data in this test so it does not depend on local ECS state.

- [ ] **Step 5: Add the new tests to full deployment and local audit gates**

In `scripts/deploy-app-to-ecs.sh`, add after `tests.test_dashboard_resource_api`:

```bash
  tests.test_dashboard_workspace_api \
  tests.test_dashboard_runtime \
```

In `scripts/system-audit.sh`, add the same modules to `run_local`.

- [ ] **Step 6: Add the three live endpoint canaries**

In the remote heredoc of `scripts/system-audit.sh`, add:

```bash
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/model-research.json?market=a_share' >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/data-intelligence.json?market=a_share' >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/operations-center.json?scope=all' >/dev/null
```

- [ ] **Step 7: Document the operator-facing contract**

Add this exact section to `docs/system-overview.md`:

```markdown
## Dashboard 五工作区

| 工作区 | 回答的问题 | 首屏接口 |
| --- | --- | --- |
| 决策总览 | 数据、情报、模型和正式策略是否真实连接 | `system-overview.json` |
| 策略工作台 | 两种正式策略表现如何，当前持仓和计划是什么 | 现有拆分策略资源 |
| 模型研究 | 接了什么数据、训练了什么、指标如何、是否模拟和采用 | `model-research.json` |
| 数据与情报 | 传统因子和情报因子来自哪里，被谁实际使用 | `data-intelligence.json` |
| 运行中心 | 今天跑到哪里，下次何时，是否需要人工 | `operations-center.json` |

`等待计划时间`、`等待上游`、零入选和正常历史回填不是故障。只有接口返回的 `interventions` 项才表示系统无法自动恢复或达到人工决策门槛。
```

Add this exact section to `docs/system-harness.md`:

```markdown
## Dashboard Workspace Runtime Contract

- `dashboard_runtime.py` only reads the fixed service/timer allowlist with `systemctl show`.
- The dashboard response cache provides a 15-second hot cache; the runtime reader retains its last successful allowlisted snapshot when the systemd bus is temporarily unavailable.
- A prior-day `Result=success` never counts as today's successful task.
- Artifact backfill exit status 75 with `Result=success` means the reconcile lock owned the worker slot and is displayed as skipped.
- The operations endpoint reads at most 20 run-ledger rows and never reads a full journal.
- All three workspace payloads must remain below 250 KB.
```

- [ ] **Step 8: Run the complete local verification gate**

Run:

```bash
python3 -m unittest \
  tests.test_dashboard_runtime \
  tests.test_dashboard_workspace_api \
  tests.test_dashboard_resource_api \
  tests.test_cli_dashboard_routes \
  tests.test_dashboard_http \
  tests.test_deploy_app_script \
  tests.test_system_structure -v
```

Expected: PASS.

Run:

```bash
cd frontend/dashboard
npm test
npm run build
```

Expected: all frontend tests PASS and production assets build.

Run:

```bash
./scripts/system-audit.sh local
```

Expected: `OK: local structure, harness, dashboard, and shell checks passed.`

- [ ] **Step 9: Commit resilience, performance gates, and documentation**

```bash
git add stock_analyze/dashboard_workspace_api.py tests/test_dashboard_workspace_api.py frontend/dashboard/src/workspaceTypes.ts frontend/dashboard/src/ModelResearchPage.tsx frontend/dashboard/src/DataIntelligencePage.tsx frontend/dashboard/src/OperationsPage.tsx scripts/deploy-app-to-ecs.sh scripts/system-audit.sh docs/system-harness.md docs/system-overview.md
git commit -m "test: harden dashboard workspace release"
```

### Task 15: Browser Acceptance and Scoped ECS Release

**Files:**
- Modify only files from Tasks 1-14 if acceptance reveals a defect.
- Do not stage or deploy unrelated dirty-worktree files.

- [ ] **Step 1: Build the static app and start the local dashboard server**

Run:

```bash
./scripts/build-dashboard-app.sh
python3 -m stock_analyze.cli --reports-dir reports serve-dashboard --host 127.0.0.1 --port 18765
```

Expected: the server remains active at `http://127.0.0.1:18765/app.html`.

- [ ] **Step 2: Verify all canonical desktop routes with the in-app browser**

Open these URLs at 1440 × 900:

```text
http://127.0.0.1:18765/app.html?view=system
http://127.0.0.1:18765/app.html?view=strategy&mode=compare&market=a_share
http://127.0.0.1:18765/app.html?view=strategy&mode=detail&market=cn_qdii_etf&strategy=defensive
http://127.0.0.1:18765/app.html?view=model-research&market=a_share
http://127.0.0.1:18765/app.html?view=data-intelligence&market=a_share
http://127.0.0.1:18765/app.html?view=operations&scope=all
```

For every route verify:

- the market/scope control remains in the same rail slot;
- the active workspace is unambiguous;
- flow nodes, tables, and status badges do not overlap;
- only current counts/statuses appear on nodes;
- clicking a node changes the detail panel without changing the workspace;
- browser back, forward, and refresh preserve the canonical route;
- the strategy detail portfolio remains visually identical to the current online workbench.

- [ ] **Step 3: Verify mobile behavior**

Repeat model, data/intelligence, operations, and strategy detail at 390 × 844. Expected:

- stage flows read vertically;
- no horizontal page overflow;
- wide tables scroll inside their own wrappers;
- left rail and scope control remain reachable;
- no text occludes another control;
- portfolio rows and drawer behavior remain unchanged.

- [ ] **Step 4: Measure local response bounds**

Run twice for each endpoint so the second request is warm:

```bash
curl --silent --output /tmp/model-research.json --write-out '%{size_download} %{time_starttransfer}\n' 'http://127.0.0.1:18765/api/dashboard/model-research.json?market=a_share'
curl --silent --output /tmp/data-intelligence.json --write-out '%{size_download} %{time_starttransfer}\n' 'http://127.0.0.1:18765/api/dashboard/data-intelligence.json?market=a_share'
curl --silent --output /tmp/operations-center.json --write-out '%{size_download} %{time_starttransfer}\n' 'http://127.0.0.1:18765/api/dashboard/operations-center.json?scope=all'
```

Expected for every second request: size below 250000 bytes. Local macOS may return `runtime.status=unavailable`; that is the expected degradation contract.

- [ ] **Step 5: Capture the exact release file list**

Run:

```bash
git diff --name-only b5a8915..HEAD
```

Expected: only the files enumerated in this plan plus the design/plan documents. Review the output before synchronization.

- [ ] **Step 6: Back up the current ECS dashboard code and static assets**

Run:

```bash
: "${SA_ECS_REMOTE:?set SA_ECS_REMOTE=user@host:/opt/stock-analyze/app}"
remote_no_slash="${SA_ECS_REMOTE%/}"
REMOTE_HOST="${SA_ECS_SSH_HOST:-${remote_no_slash%%:*}}"
REMOTE_PATH="${SA_ECS_REMOTE_PATH:-${remote_no_slash#*:}}"
test "$REMOTE_PATH" = "/opt/stock-analyze/app"
release_stamp="$(date +%Y%m%d-%H%M%S)"
printf '%s\n' "$release_stamp" >/tmp/stock-analyze-dashboard-release-stamp
ssh ${SA_ECS_SSH_OPTS:-} "$REMOTE_HOST" \
  bash -s -- "$REMOTE_PATH" "$release_stamp" <<'REMOTE'
set -eu
root="$1"
stamp="$2"
backup="/opt/stock-analyze/releases/$stamp"
mkdir -p "$backup"
cd "$root"
: >"$backup/preexisting-files.txt"
for item in \
  stock_analyze/cli.py \
  stock_analyze/dashboard_api.py \
  stock_analyze/dashboard_workspace_api.py \
  stock_analyze/dashboard_runtime.py \
  reports/app; do
  if [ -e "$item" ]; then
    printf '%s\n' "$item" >>"$backup/preexisting-files.txt"
    cp -a --parents "$item" "$backup/"
  fi
done
REMOTE
```

Expected: a timestamped rollback directory containing every pre-existing API
file, `reports/app`, and a manifest that distinguishes newly introduced modules
from files that already existed.

- [ ] **Step 7: Deploy only the workspace implementation**

Build first:

```bash
./scripts/build-dashboard-app.sh
```

Synchronize the explicit backend and test files:

```bash
rsync -az --relative \
  stock_analyze/cli.py \
  stock_analyze/dashboard_api.py \
  stock_analyze/dashboard_workspace_api.py \
  stock_analyze/dashboard_runtime.py \
  tests/test_dashboard_workspace_api.py \
  tests/test_dashboard_runtime.py \
  tests/test_cli_dashboard_routes.py \
  tests/test_dashboard_resource_api.py \
  scripts/system-audit.sh \
  docs/system-harness.md \
  docs/system-overview.md \
  "$SA_ECS_REMOTE/"
```

Synchronize the built app:

```bash
rsync -az --delete reports/app/ "$SA_ECS_REMOTE/reports/app/"
```

Expected: no strategy config, data directory, model artifact, timer, or unrelated dirty-worktree file is transferred.

- [ ] **Step 8: Run remote tests and restart only the dashboard service**

Run:

```bash
remote_no_slash="${SA_ECS_REMOTE%/}"
REMOTE_HOST="${SA_ECS_SSH_HOST:-${remote_no_slash%%:*}}"
REMOTE_PATH="${SA_ECS_REMOTE_PATH:-${remote_no_slash#*:}}"
ssh ${SA_ECS_SSH_OPTS:-} "$REMOTE_HOST" \
  "cd '$REMOTE_PATH' && \
   /opt/stock-analyze/venv/bin/python -m unittest \
     tests.test_dashboard_runtime \
     tests.test_dashboard_workspace_api \
     tests.test_dashboard_resource_api \
     tests.test_cli_dashboard_routes -v && \
   systemctl restart stock-analyze-dashboard.service && \
   systemctl is-active --quiet stock-analyze-dashboard.service"
```

Expected: tests PASS and `stock-analyze-dashboard.service` is active. No simulation, training, ingestion, semantic, or backfill service is restarted.

- [ ] **Step 9: Run live ECS canaries and performance checks**

Run:

```bash
./scripts/system-audit.sh --remote
```

Expected: timer checks, service health, run-ledger consistency, all dashboard endpoints, and intelligence status pass.

Run twice on ECS:

```bash
remote_no_slash="${SA_ECS_REMOTE%/}"
REMOTE_HOST="${SA_ECS_SSH_HOST:-${remote_no_slash%%:*}}"
ssh ${SA_ECS_SSH_OPTS:-} "$REMOTE_HOST" \
  "for url in \
    'model-research.json?market=a_share' \
    'data-intelligence.json?market=a_share' \
    'operations-center.json?scope=all'; do \
      curl --silent --output /dev/null --write-out \"\$url %{size_download} %{time_starttransfer}\\n\" \
        \"http://127.0.0.1:8765/api/dashboard/\$url\"; \
      curl --silent --output /dev/null --write-out \"\$url warm %{size_download} %{time_starttransfer}\\n\" \
        \"http://127.0.0.1:8765/api/dashboard/\$url\"; \
    done"
```

Expected: every response is below 250000 bytes and every warm `time_starttransfer` is below 0.500 seconds.

- [ ] **Step 10: Verify the deployed UI through the SSH tunnel**

Use the existing tunnel:

```bash
remote_no_slash="${SA_ECS_REMOTE%/}"
REMOTE_HOST="${SA_ECS_SSH_HOST:-${remote_no_slash%%:*}}"
ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=6 \
  ${SA_ECS_SSH_OPTS:-} -L 18765:127.0.0.1:8765 "$REMOTE_HOST"
```

Open:

```text
http://127.0.0.1:18765/app.html?view=system
```

Repeat the desktop and mobile checks from Steps 2-3 against live ECS data. Confirm:

- model research shows actual model versions and gate reasons;
- data/intelligence counts match the live endpoint;
- operations statuses match `systemctl show` for the sampled units;
- the portfolio workbench has no visual or behavioral regression.

- [ ] **Step 11: Roll back only if a release gate fails**

Restore the timestamped backup and remove a new backend module only when the
manifest proves that it did not exist before this release:

```bash
remote_no_slash="${SA_ECS_REMOTE%/}"
REMOTE_HOST="${SA_ECS_SSH_HOST:-${remote_no_slash%%:*}}"
REMOTE_PATH="${SA_ECS_REMOTE_PATH:-${remote_no_slash#*:}}"
release_stamp="$(cat /tmp/stock-analyze-dashboard-release-stamp)"
ssh ${SA_ECS_SSH_OPTS:-} "$REMOTE_HOST" \
  bash -s -- "$REMOTE_PATH" "$release_stamp" <<'REMOTE'
set -eu
root="$1"
stamp="$2"
backup="/opt/stock-analyze/releases/$stamp"
manifest="$backup/preexisting-files.txt"
test -f "$manifest"
cp -a "$backup/stock_analyze/." "$root/stock_analyze/"
rm -rf "$root/reports/app"
cp -a "$backup/reports/app" "$root/reports/app"
for module in \
  stock_analyze/dashboard_workspace_api.py \
  stock_analyze/dashboard_runtime.py; do
  if ! grep -Fqx "$module" "$manifest"; then
    rm -f "$root/$module"
  fi
done
systemctl restart stock-analyze-dashboard.service
REMOTE
```

Expected: the previous Dashboard API and static app return without changing data, strategies, models, or scheduled jobs.

- [ ] **Step 12: Record final evidence**

Save in the implementation completion note:

- local Python and frontend test counts;
- production build result;
- desktop/mobile screenshot paths;
- three local and three ECS payload sizes;
- three warm ECS response times;
- live systemd/dashboard canary result;
- deployed file list;
- rollback snapshot path;
- explicit confirmation that no strategy, model, paper-trading, or timer logic changed.
