import { useCallback, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { workspaceQueryClient } from "./queryClient";

/**
 * Callers must memoize the loader identity, typically with `useCallback`, and
 * should honor the supplied AbortSignal whenever possible.
 */
export type Loader<T> = (signal: AbortSignal) => Promise<T>;

type WorkspaceResourceOptions = {
  keepPreviousData?: boolean;
};

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

export function useWorkspaceResource<T>(
  key: string,
  enabled: boolean,
  loader: Loader<T>,
  options: WorkspaceResourceOptions = {},
) {
  const queryKey = ["workspace-resource", key] as const;
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const previousRequest = useRef({ key, loader });
  const query = useQuery<T, unknown>({
    queryKey,
    enabled,
    queryFn: ({ signal }) => Promise.resolve().then(
      () => loaderRef.current(signal),
    ),
    placeholderData: options.keepPreviousData
      ? (previousData) => previousData
      : undefined,
  }, workspaceQueryClient);

  useEffect(() => {
    if (enabled) return;
    void workspaceQueryClient.cancelQueries({ queryKey, exact: true });
  }, [enabled, key]);

  useEffect(() => {
    const previous = previousRequest.current;
    previousRequest.current = { key, loader };
    if (previous.key !== key || previous.loader === loader) return;
    if (!enabled) return;
    void workspaceQueryClient.cancelQueries({ queryKey, exact: true }).then(
      () => query.refetch({ cancelRefetch: false }),
    );
  }, [enabled, key, loader, query.refetch]);

  const data = enabled ? query.data ?? null : null;
  return {
    key,
    data,
    loading: enabled && query.isFetching,
    error: enabled && query.error ? message(query.error) : null,
    stale: Boolean(data && query.isRefetchError),
    refresh: useCallback(() => {
      void query.refetch({ cancelRefetch: true });
    }, [query.refetch]),
  };
}
