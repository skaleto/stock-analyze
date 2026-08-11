import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { workspaceQueryClient } from "./src/queryClient";

afterEach(() => {
  workspaceQueryClient.clear();
});
