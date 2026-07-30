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
  it("reports a synchronous loader throw on initial load", async () => {
    const loader = vi.fn(
      (_signal: AbortSignal): Promise<{ value: number }> => {
        throw new Error("sync unavailable");
      },
    );
    const { result } = renderHook(() =>
      useWorkspaceResource("a_share", true, loader),
    );

    await waitFor(() => expect(result.current.error).toBe("sync unavailable"));
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.stale).toBe(false);
  });

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
    expect(result.current.data).toEqual({ value: 7 });
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();
    expect(result.current.stale).toBe(false);
    second.reject(new Error("runtime unavailable"));
    await waitFor(() => expect(result.current.error).toBe("runtime unavailable"));
    expect(result.current.data).toEqual({ value: 7 });
    expect(result.current.stale).toBe(true);
  });

  it("keeps the last successful snapshot when refresh throws synchronously", async () => {
    const loader = vi.fn()
      .mockResolvedValueOnce({ value: 7 })
      .mockImplementationOnce(() => {
        throw new Error("sync refresh unavailable");
      });
    const { result } = renderHook(() =>
      useWorkspaceResource("a_share", true, loader),
    );

    await waitFor(() => expect(result.current.data).toEqual({ value: 7 }));
    act(() => result.current.refresh());
    await waitFor(() =>
      expect(result.current.error).toBe("sync refresh unavailable"),
    );
    expect(result.current.data).toEqual({ value: 7 });
    expect(result.current.loading).toBe(false);
    expect(result.current.stale).toBe(true);
  });

  it("projects loading immediately for an enabled initial or new key", async () => {
    const second = deferred<{ value: number }>();
    let callCount = 0;
    const loader = vi.fn(() => {
      callCount += 1;
      return callCount === 1
        ? Promise.resolve({ value: 7 })
        : second.promise;
    });
    const renders: Array<{
      key: string;
      data: { value: number } | null;
      loading: boolean;
      error: string | null;
      stale: boolean;
    }> = [];
    const { result, rerender } = renderHook(
      ({ key }) => {
        const resource = useWorkspaceResource(key, true, loader);
        renders.push({
          key: resource.key,
          data: resource.data,
          loading: resource.loading,
          error: resource.error,
          stale: resource.stale,
        });
        return resource;
      },
      { initialProps: { key: "a_share" } },
    );

    expect(renders[0]).toEqual({
      key: "a_share",
      data: null,
      loading: true,
      error: null,
      stale: false,
    });
    await waitFor(() => expect(result.current.data).toEqual({ value: 7 }));

    renders.length = 0;
    rerender({ key: "cn_qdii_etf" });
    expect(renders[0]).toEqual({
      key: "cn_qdii_etf",
      data: null,
      loading: true,
      error: null,
      stale: false,
    });
  });

  it("clears prior-key data while the replacement request loads", async () => {
    const second = deferred<{ value: number }>();
    const loader = vi.fn()
      .mockResolvedValueOnce({ value: 7 })
      .mockImplementationOnce(() => second.promise);
    const { result, rerender } = renderHook(
      ({ key }) => useWorkspaceResource(key, true, loader),
      { initialProps: { key: "a_share" } },
    );

    await waitFor(() => expect(result.current.data).toEqual({ value: 7 }));
    rerender({ key: "cn_qdii_etf" });
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    expect(result.current).toMatchObject({
      key: "cn_qdii_etf",
      data: null,
      loading: true,
      error: null,
      stale: false,
    });
  });

  it("ignores a stale resolution after the key changes", async () => {
    const first = deferred<{ value: number }>();
    const second = deferred<{ value: number }>();
    const loader = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const { result, rerender } = renderHook(
      ({ key }) => useWorkspaceResource(key, true, loader),
      { initialProps: { key: "a_share" } },
    );

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));
    rerender({ key: "cn_qdii_etf" });
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    act(() => second.resolve({ value: 2 }));
    await waitFor(() => expect(result.current.data).toEqual({ value: 2 }));
    await act(async () => {
      first.resolve({ value: 1 });
      await Promise.resolve();
    });
    expect(result.current.data).toEqual({ value: 2 });
  });

  it("ignores a stale rejection after the loader changes", async () => {
    const first = deferred<{ value: number }>();
    const signals: AbortSignal[] = [];
    const firstLoader = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return first.promise;
    });
    const secondLoader = vi.fn(() => Promise.resolve({ value: 2 }));
    const { result, rerender } = renderHook(
      ({ loader }) => useWorkspaceResource("a_share", true, loader),
      { initialProps: { loader: firstLoader } },
    );

    await waitFor(() => expect(firstLoader).toHaveBeenCalledTimes(1));
    rerender({ loader: secondLoader });
    await waitFor(() => expect(result.current.data).toEqual({ value: 2 }));
    expect(signals[0].aborted).toBe(true);
    await act(async () => {
      first.reject(new Error("late failure"));
      await first.promise.catch(() => undefined);
      await Promise.resolve();
    });
    expect(result.current.error).toBeNull();
    expect(result.current.data).toEqual({ value: 2 });
  });

  it("keeps disabled state clear after a non-cooperative request resolves", async () => {
    const request = deferred<{ value: number }>();
    const signals: AbortSignal[] = [];
    const loader = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return request.promise;
    });
    const { result, rerender } = renderHook(
      ({ enabled }) => useWorkspaceResource("a_share", enabled, loader),
      { initialProps: { enabled: true } },
    );

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));
    rerender({ enabled: false });
    await waitFor(() => expect(signals[0].aborted).toBe(true));
    expect(result.current).toMatchObject({
      key: "a_share",
      data: null,
      loading: false,
      error: null,
      stale: false,
    });

    await act(async () => {
      request.resolve({ value: 7 });
      await request.promise;
      await Promise.resolve();
    });
    expect(result.current).toMatchObject({
      key: "a_share",
      data: null,
      loading: false,
      error: null,
      stale: false,
    });
  });

  it("aborts the active request when unmounted", async () => {
    const request = deferred<{ value: number }>();
    const signals: AbortSignal[] = [];
    const loader = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return request.promise;
    });
    const { unmount } = renderHook(() =>
      useWorkspaceResource("a_share", true, loader),
    );

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));
    unmount();
    expect(signals[0].aborted).toBe(true);
  });

  it("stringifies non-Error rejections", async () => {
    const loader = vi.fn(() => Promise.reject(503));
    const { result } = renderHook(() =>
      useWorkspaceResource("a_share", true, loader),
    );

    await waitFor(() => expect(result.current.error).toBe("503"));
    expect(result.current.data).toBeNull();
    expect(result.current.stale).toBe(false);
  });
});
