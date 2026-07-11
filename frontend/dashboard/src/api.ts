import type { DashboardDetail, DashboardSummary, InstrumentDetail } from "./types";

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { cache: "no-store", signal });
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

export function fetchDetail(
  market: string,
  agent: string,
  signal?: AbortSignal
): Promise<DashboardDetail> {
  const params = new URLSearchParams({ market, agent });
  return fetchJson<DashboardDetail>(`/api/dashboard/detail.json?${params.toString()}`, signal);
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
