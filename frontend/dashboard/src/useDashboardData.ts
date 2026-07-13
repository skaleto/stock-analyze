import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchOperations,
  fetchOverview,
  fetchPerformance,
  fetchPortfolio,
  fetchPredictions,
  fetchResearch,
} from "./api";
import type {
  DashboardDetail,
  DashboardOperations,
  DashboardOverview,
  DashboardPerformance,
  DashboardPortfolio,
  DashboardPredictions,
  DashboardResearch,
} from "./types";

type ResourceName = "overview" | "performance" | "portfolio" | "predictions" | "research" | "operations";

type Resources = {
  overview: DashboardOverview;
  performance: DashboardPerformance;
  portfolio: DashboardPortfolio;
  predictions: DashboardPredictions;
  research: DashboardResearch;
  operations: DashboardOperations;
};

type Snapshot = {
  key: string;
  resources: Partial<Resources>;
  errors: Partial<Record<ResourceName, string>>;
};

const labels: Record<ResourceName, string> = {
  overview: "账户概览",
  performance: "净值轨迹",
  portfolio: "持仓交易",
  predictions: "概率预测",
  research: "ETF研究",
  operations: "运行历史",
};

function reasonMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function mergeDetail(
  market: string,
  agent: string,
  resources: Partial<Resources>,
): DashboardDetail | null {
  if (Object.keys(resources).length === 0) return null;
  const overview = resources.overview;
  const performance = resources.performance;
  const portfolio = resources.portfolio;
  const predictions = resources.predictions;
  const research = resources.research;
  const operations = resources.operations;
  const generatedValues = Object.values(resources)
    .map((resource) => resource.generated_at)
    .filter(Boolean)
    .sort();
  const generated = generatedValues[generatedValues.length - 1] ?? new Date().toISOString();
  return {
    generated_at: generated,
    market,
    market_label: overview?.market_label ?? market,
    currency: overview?.currency ?? "¥",
    agent,
    strategy: overview?.strategy ?? {
      agent,
      agent_label: agent,
      name: agent,
      factors: [],
    },
    nav: performance?.nav ?? {
      latest: overview?.latest_nav ?? null,
      series: [],
      accounts: [],
    },
    activity: portfolio?.activity ?? { summary: { total: 0 }, rows: [] },
    orders: portfolio?.orders ?? { summary: { total: 0, buy: 0, sell: 0 }, rows: [] },
    positions: portfolio?.positions ?? { summary: { total: 0 }, rows: [] },
    trades: portfolio?.trades ?? { summary: { total: 0 }, rows: [] },
    runs: operations?.runs ?? { summary: { total: 0 }, rows: [] },
    weekly_report: operations?.weekly_report ?? { exists: false, href: null, markdown: "" },
    selection: research?.selection,
    lookthrough: research?.lookthrough,
    research: research?.research,
    prediction_summary: predictions?.prediction_summary,
    alerts: predictions?.alerts,
    regimes: predictions?.regimes,
    model_health: predictions?.model_health,
    source_health: predictions?.source_health,
  };
}

export function useDashboardData(market: string, agent: string) {
  const key = `${market}:${agent}`;
  const [snapshot, setSnapshot] = useState<Snapshot>({ key: "", resources: {}, errors: {} });
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  const requestIdRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async (preserve: boolean) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const requestId = ++requestIdRef.current;
    setLoadingKey(key);
    setSnapshot((current) => ({
      key,
      resources: preserve && current.key === key ? current.resources : {},
      errors: {},
    }));

    const settle = (name: ResourceName, result: PromiseSettledResult<unknown>, primary: boolean) => {
      if (requestId !== requestIdRef.current || controller.signal.aborted) return;
      setSnapshot((current) => {
        if (current.key !== key) return current;
        const resources = { ...current.resources };
        const errors = { ...current.errors };
        if (result.status === "fulfilled") {
          resources[name] = result.value as never;
          delete errors[name];
        } else {
          delete resources[name];
          errors[name] = reasonMessage(result.reason);
        }
        return { key, resources, errors };
      });
      if (primary) {
        setLoadingKey((current) => current === key ? null : current);
      }
    };
    const request = async (
      name: ResourceName,
      resource: Promise<unknown>,
      primary: boolean,
    ) => {
      try {
        settle(name, { status: "fulfilled", value: await resource }, primary);
      } catch (reason) {
        settle(name, { status: "rejected", reason }, primary);
      }
    };

    const primary: [ResourceName, Promise<unknown>][] = [
      ["overview", fetchOverview(market, agent, controller.signal)],
      ["performance", fetchPerformance(market, agent, controller.signal)],
      ["portfolio", fetchPortfolio(market, agent, controller.signal)],
      ["predictions", fetchPredictions(market, agent, controller.signal)],
    ];
    const primaryTasks = primary.map(([name, resource]) => request(name, resource, true));

    await Promise.resolve();
    const deferred: [ResourceName, Promise<unknown>][] = [
      ["research", fetchResearch(market, agent, controller.signal)],
      ["operations", fetchOperations(market, agent, controller.signal)],
    ];
    const deferredTasks = deferred.map(([name, resource]) => request(name, resource, false));
    await Promise.all([...primaryTasks, ...deferredTasks]);
  }, [agent, key, market]);

  useEffect(() => {
    void load(false);
    return () => {
      abortRef.current?.abort();
      requestIdRef.current += 1;
    };
  }, [load]);

  const active = snapshot.key === key ? snapshot : { key, resources: {}, errors: {} };
  const detail = useMemo(
    () => mergeDetail(market, agent, active.resources),
    [active.resources, agent, market],
  );
  const error = Object.entries(active.errors)
    .map(([name, message]) => `${labels[name as ResourceName]}：${message}`)
    .join("；") || null;

  return {
    detail,
    error,
    loading: loadingKey === key,
    reload: useCallback(() => load(true), [load]),
  };
}
