import type {
  DashboardDetail,
  DashboardGovernance,
  DashboardOperations,
  DashboardOverview,
  DashboardPerformance,
  DashboardPortfolio,
  DashboardPredictions,
  DashboardResearch,
  DashboardSummary,
  IntelligenceDocumentDetail,
  IntelligenceEventDetail,
  IntelligenceSummary,
  InstrumentDetail,
  SystemOverviewData,
} from "./types";
import type { ModelResearchData } from "./workspaceTypes";

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { cache: "no-cache", signal });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`.trim();
    try {
      const payload = await response.json() as { message?: unknown };
      if (typeof payload.message === "string" && payload.message.trim()) {
        message = payload.message.trim();
      }
    } catch {
      // Keep the HTTP fallback when the error body is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function fetchSummary(signal?: AbortSignal): Promise<DashboardSummary> {
  return fetchJson<DashboardSummary>("/api/dashboard/summary.json", signal);
}

export function fetchSystemOverview(signal?: AbortSignal): Promise<SystemOverviewData> {
  return fetchJson<SystemOverviewData>("/api/dashboard/system-overview.json", signal);
}

export function fetchDetail(
  market: string,
  agent: string,
  signal?: AbortSignal
): Promise<DashboardDetail> {
  const params = new URLSearchParams({ market, agent });
  return fetchJson<DashboardDetail>(`/api/dashboard/detail.json?${params.toString()}`, signal);
}

function resourceUrl(resource: string, market: string, agent: string, extra?: Record<string, string>): string {
  const params = new URLSearchParams({ market, agent, ...extra });
  return `/api/dashboard/${resource}.json?${params.toString()}`;
}

export function fetchOverview(market: string, agent: string, signal?: AbortSignal): Promise<DashboardOverview> {
  return fetchJson(resourceUrl("overview", market, agent), signal);
}

export function fetchPerformance(market: string, agent: string, signal?: AbortSignal): Promise<DashboardPerformance> {
  return fetchJson(resourceUrl("performance", market, agent), signal);
}

export function fetchPortfolio(market: string, agent: string, signal?: AbortSignal): Promise<DashboardPortfolio> {
  return fetchJson(resourceUrl("portfolio", market, agent), signal);
}

export function fetchPredictions(market: string, agent: string, signal?: AbortSignal): Promise<DashboardPredictions> {
  return fetchJson(resourceUrl("predictions", market, agent, { limit_per_horizon: "12" }), signal);
}

export function fetchResearch(market: string, agent: string, signal?: AbortSignal): Promise<DashboardResearch> {
  return fetchJson(resourceUrl("research", market, agent), signal);
}

export function fetchOperations(market: string, agent: string, signal?: AbortSignal): Promise<DashboardOperations> {
  return fetchJson(resourceUrl("operations", market, agent), signal);
}

export function fetchGovernance(market: string, agent: string, signal?: AbortSignal): Promise<DashboardGovernance> {
  return fetchJson(resourceUrl("governance", market, agent), signal);
}

export function fetchIntelligence(
  market: string,
  agent: string,
  signal?: AbortSignal,
): Promise<IntelligenceSummary> {
  return fetchJson(resourceUrl("intelligence", market, agent), signal);
}

export function fetchIntelligenceEvent(
  market: string,
  agent: string,
  eventId: string,
  signal?: AbortSignal,
): Promise<IntelligenceEventDetail> {
  return fetchJson(
    resourceUrl("intelligence-event", market, agent, { event_id: eventId }),
    signal,
  );
}

export function fetchIntelligenceDocument(
  market: string,
  agent: string,
  documentId: number,
  signal?: AbortSignal,
): Promise<IntelligenceDocumentDetail> {
  return fetchJson(
    resourceUrl("intelligence-document", market, agent, {
      document_id: String(documentId),
    }),
    signal,
  );
}

export function fetchInstrument(
  market: string,
  agent: string,
  code: string,
  signal?: AbortSignal
): Promise<InstrumentDetail> {
  const params = new URLSearchParams({ market, agent, code });
  return fetchJson<InstrumentDetail>(`/api/dashboard/instrument.json?${params.toString()}`, signal);
}

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
