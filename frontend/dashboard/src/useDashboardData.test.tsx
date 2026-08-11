import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { useDashboardData } from "./useDashboardData";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    fetchOverview: vi.fn(),
    fetchPerformance: vi.fn(),
    fetchPortfolio: vi.fn(),
    fetchPredictions: vi.fn(),
    fetchResearch: vi.fn(),
    fetchOperations: vi.fn(),
    fetchGovernance: vi.fn(),
  };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const resource = { generated_at: "2026-08-08T10:00:00+08:00" };

const primaryMocks = [
  api.fetchOverview,
  api.fetchPerformance,
  api.fetchPortfolio,
  api.fetchPredictions,
] as const;

const deferredMocks = [
  api.fetchResearch,
  api.fetchOperations,
  api.fetchGovernance,
] as const;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useDashboardData", () => {
  it("waits for primary resources before starting heavy secondary resources", async () => {
    const pending = primaryMocks.map(() => deferred<never>());
    primaryMocks.forEach((mock, index) => {
      vi.mocked(mock).mockReturnValue(pending[index].promise);
    });
    deferredMocks.forEach((mock) => {
      vi.mocked(mock).mockResolvedValue(resource as never);
    });

    renderHook(() => useDashboardData("a_share", "codex"));

    await waitFor(() => {
      primaryMocks.forEach((mock) => expect(mock).toHaveBeenCalledTimes(1));
    });
    deferredMocks.forEach((mock) => expect(mock).not.toHaveBeenCalled());

    await act(async () => {
      pending.forEach((request) => request.resolve(resource as never));
      await Promise.resolve();
    });

    await waitFor(() => {
      deferredMocks.forEach((mock) => expect(mock).toHaveBeenCalledTimes(1));
    });
  });

  it("reuses fresh strategy resources after leaving and returning", async () => {
    [...primaryMocks, ...deferredMocks].forEach((mock) => {
      vi.mocked(mock).mockResolvedValue(resource as never);
    });

    const first = renderHook(() => useDashboardData("a_share", "codex"));
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    await waitFor(() => {
      deferredMocks.forEach((mock) => expect(mock).toHaveBeenCalledTimes(1));
    });
    first.unmount();

    const second = renderHook(() => useDashboardData("a_share", "codex"));
    expect(second.result.current.loading).toBe(false);
    await act(async () => Promise.resolve());

    [...primaryMocks, ...deferredMocks].forEach((mock) => {
      expect(mock).toHaveBeenCalledTimes(1);
    });
  });

  it("keeps the visible strategy snapshot while an explicit refresh runs", async () => {
    const refresh = deferred<never>();
    vi.mocked(api.fetchOverview)
      .mockResolvedValueOnce(resource as never)
      .mockReturnValueOnce(refresh.promise);
    [...primaryMocks.slice(1), ...deferredMocks].forEach((mock) => {
      vi.mocked(mock).mockResolvedValue(resource as never);
    });

    const { result } = renderHook(() => useDashboardData("a_share", "codex"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.detail).not.toBeNull();

    act(() => result.current.reload());
    await waitFor(() => expect(api.fetchOverview).toHaveBeenCalledTimes(2));
    expect(result.current.loading).toBe(true);
    expect(result.current.detail).not.toBeNull();

    await act(async () => {
      refresh.resolve(resource as never);
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
  });
});
