import { useCallback, useEffect, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import {
  fetchIntelligence,
  fetchIntelligenceDocument,
  fetchIntelligenceEvent,
  fetchOperations,
  fetchGovernance,
  fetchOverview,
  fetchPerformance,
  fetchPortfolio,
  fetchPredictions,
  fetchResearch,
} from "./api";
import type {
  DashboardDetail,
  DashboardGovernance,
  DashboardOperations,
  DashboardOverview,
  DashboardPerformance,
  DashboardPortfolio,
  DashboardPredictions,
  DashboardResearch,
  IntelligenceDocumentDetail,
  IntelligenceEventDetail,
  IntelligenceSummary,
} from "./types";
import { workspaceQueryClient } from "./queryClient";

type ResourceName = "overview" | "performance" | "portfolio" | "predictions" | "research" | "operations" | "governance";

type Resources = {
  overview: DashboardOverview;
  performance: DashboardPerformance;
  portfolio: DashboardPortfolio;
  predictions: DashboardPredictions;
  research: DashboardResearch;
  operations: DashboardOperations;
  governance: DashboardGovernance;
};

const labels: Record<ResourceName, string> = {
  overview: "账户概览",
  performance: "净值轨迹",
  portfolio: "持仓交易",
  predictions: "概率预测",
  research: "ETF研究",
  operations: "运行历史",
  governance: "决策与风控",
};

const primaryResourceNames: ResourceName[] = [
  "overview",
  "performance",
  "portfolio",
  "predictions",
];

const secondaryResourceNames: ResourceName[] = [
  "research",
  "operations",
  "governance",
];

const resourceLoaders: Record<
  ResourceName,
  (market: string, agent: string, signal: AbortSignal) => Promise<unknown>
> = {
  overview: fetchOverview,
  performance: fetchPerformance,
  portfolio: fetchPortfolio,
  predictions: fetchPredictions,
  research: fetchResearch,
  operations: fetchOperations,
  governance: fetchGovernance,
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
  const governance = resources.governance;
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
    model_iteration: overview?.model_iteration,
    model_shadow: overview?.model_shadow,
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
    intelligence: { market, agent },
    prediction_summary: predictions?.prediction_summary,
    alerts: predictions?.alerts,
    regimes: predictions?.regimes,
    model_health: predictions?.model_health,
    source_health: predictions?.source_health,
    governance,
  };
}

export function useIntelligenceData(
  market: string,
  agent: string,
  visible: boolean,
  decisionId: string | null,
  refreshToken = 0,
) {
  const key = `${market}:${agent}`;
  const [summaryState, setSummaryState] = useState<{
    key: string;
    value: IntelligenceSummary | null;
    error: string | null;
  }>({ key: "", value: null, error: null });
  const [detailState, setDetailState] = useState<{
    key: string;
    value: IntelligenceEventDetail | null;
    error: string | null;
  }>({ key: "", value: null, error: null });
  const [documentDetail, setDocumentDetail] = useState<IntelligenceDocumentDetail | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    setSummaryLoading(true);
    setSummaryState((current) => current.key === key
      ? { ...current, error: null }
      : { key, value: null, error: null });
    fetchIntelligence(market, agent, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setSummaryState({ key, value, error: null });
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setSummaryState({ key, value: null, error: reasonMessage(reason) });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setSummaryLoading(false);
      });
    return () => controller.abort();
  }, [agent, key, market, refreshToken, visible]);

  useEffect(() => {
    setDocumentDetail(null);
    if (!decisionId) {
      setDetailState({ key: "", value: null, error: null });
      setDetailLoading(false);
      return;
    }
    const controller = new AbortController();
    const detailKey = `${key}:${decisionId}`;
    setDetailLoading(true);
    setDetailState({ key: detailKey, value: null, error: null });
    fetchIntelligenceEvent(market, agent, decisionId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setDetailState({ key: detailKey, value, error: null });
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setDetailState({ key: detailKey, value: null, error: reasonMessage(reason) });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [agent, decisionId, key, market]);

  const loadDocument = useCallback(async (documentId: number) => {
    const controller = new AbortController();
    try {
      const value = await fetchIntelligenceDocument(
        market,
        agent,
        documentId,
        controller.signal,
      );
      setDocumentDetail(value);
    } catch (reason) {
      setDetailState((current) => ({
        ...current,
        error: reasonMessage(reason),
      }));
    }
    return () => controller.abort();
  }, [agent, market]);

  return {
    summary: summaryState.key === key ? summaryState.value : null,
    summaryError: summaryState.key === key ? summaryState.error : null,
    summaryLoading,
    detail: detailState.key === `${key}:${decisionId ?? ""}` ? detailState.value : null,
    detailError: detailState.key === `${key}:${decisionId ?? ""}` ? detailState.error : null,
    detailLoading,
    documentDetail,
    loadDocument,
  };
}

export function useDashboardData(market: string, agent: string, enabled = true) {
  const key = `${market}:${agent}`;
  const [secondaryKey, setSecondaryKey] = useState("");

  const primaryQueries = useQueries({
    queries: primaryResourceNames.map((name) => ({
      queryKey: ["strategy-resource", market, agent, name] as const,
      enabled,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        resourceLoaders[name](market, agent, signal),
    })),
  }, workspaceQueryClient);

  const primarySettled = primaryQueries.every((query) => !query.isPending);

  useEffect(() => {
    setSecondaryKey("");
    if (!enabled) {
      void workspaceQueryClient.cancelQueries({
        queryKey: ["strategy-resource", market, agent],
      });
      return;
    }
    const idleFallback = window.setTimeout(() => setSecondaryKey(key), 800);
    return () => window.clearTimeout(idleFallback);
  }, [agent, enabled, key, market]);

  useEffect(() => {
    if (enabled && primarySettled) setSecondaryKey(key);
  }, [enabled, key, primarySettled]);

  const secondaryQueries = useQueries({
    queries: secondaryResourceNames.map((name) => ({
      queryKey: ["strategy-resource", market, agent, name] as const,
      enabled: enabled && secondaryKey === key,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        resourceLoaders[name](market, agent, signal),
    })),
  }, workspaceQueryClient);

  const resources: Partial<Resources> = {};
  const errors: Partial<Record<ResourceName, string>> = {};
  [...primaryQueries, ...secondaryQueries].forEach((query, index) => {
    const name = [...primaryResourceNames, ...secondaryResourceNames][index];
    if (query.data !== undefined) {
      Object.assign(resources, { [name]: query.data });
    }
    if (query.error) errors[name] = reasonMessage(query.error);
  });

  const detail = mergeDetail(market, agent, resources);
  const error = Object.entries(errors)
    .map(([name, message]) => `${labels[name as ResourceName]}：${message}`)
    .join("；") || null;

  const reload = useCallback(() => {
    setSecondaryKey(key);
    void workspaceQueryClient.invalidateQueries({
      queryKey: ["strategy-resource", market, agent],
      refetchType: "active",
    });
  }, [agent, key, market]);

  return {
    detail,
    error,
    loading: enabled && primaryQueries.some((query) => query.isFetching),
    reload,
  };
}
