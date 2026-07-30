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
