import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import type { ReactNode } from "react";

export const WORKSPACE_STALE_TIME_MS = 60_000;
export const WORKSPACE_GC_TIME_MS = 10 * 60_000;

export function createWorkspaceQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: WORKSPACE_STALE_TIME_MS,
        gcTime: WORKSPACE_GC_TIME_MS,
        retry: import.meta.env.MODE === "test" ? false : 1,
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,
      },
    },
  });
}

export const workspaceQueryClient = createWorkspaceQueryClient();

export function WorkspaceQueryProvider({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={workspaceQueryClient}>
      {children}
    </QueryClientProvider>
  );
}
