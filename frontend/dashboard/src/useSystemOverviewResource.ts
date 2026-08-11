import { useCallback, useEffect, useRef } from "react";
import { fetchSystemOverview } from "./api";
import type { SystemOverviewData } from "./types";
import { useWorkspaceResource } from "./useWorkspaceResource";

export function useSystemOverviewResource(refreshToken = 0) {
  const loader = useCallback(
    (signal: AbortSignal) => fetchSystemOverview(signal),
    [],
  );
  const resource = useWorkspaceResource<SystemOverviewData>(
    "system-overview",
    true,
    loader,
  );
  const previousToken = useRef(refreshToken);
  const mounted = useRef(false);

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      previousToken.current = refreshToken;
      return;
    }
    if (previousToken.current === refreshToken) return;
    previousToken.current = refreshToken;
    resource.refresh();
  }, [refreshToken, resource.refresh]);

  return resource;
}
