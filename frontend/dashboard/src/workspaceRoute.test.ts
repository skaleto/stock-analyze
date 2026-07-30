import { describe, expect, it } from "vitest";
import {
  agentForStrategy,
  parseWorkspaceRoute,
  serializeWorkspaceRoute,
} from "./workspaceRoute";

describe("workspace route contract", () => {
  it.each([
    ["?view=compare&market=a_share&agent=codex", {
      view: "strategy", mode: "compare", market: "a_share",
    }],
    ["?view=detail&market=a_share&agent=claude", {
      view: "strategy", mode: "detail", market: "a_share", strategy: "defensive",
    }],
    ["?view=model-shadow&market=cn_qdii_etf&agent=model_shadow", {
      view: "model-research", market: "cn_qdii_etf",
    }],
    ["?view=model-iteration&market=a_share", {
      view: "model-research", market: "a_share",
    }],
    ["?view=intelligence&market=a_share&agent=model_shadow", {
      view: "data-intelligence", market: "a_share",
    }],
  ])("migrates %s", (search, expected) => {
    const route = parseWorkspaceRoute(search);
    expect(route).toEqual(expected);
    expect(serializeWorkspaceRoute(route)).not.toContain("agent=");
  });

  it("normalizes invalid parameters to the decision overview", () => {
    expect(parseWorkspaceRoute("?view=unknown&market=hk&scope=broken")).toEqual({
      view: "system",
    });
  });

  it("keeps operations scope independent from market selection", () => {
    expect(parseWorkspaceRoute("?view=operations&scope=exceptions")).toEqual({
      view: "operations",
      scope: "exceptions",
    });
  });

  it("maps public strategy keys only at the API boundary", () => {
    expect(agentForStrategy("defensive")).toBe("claude");
    expect(agentForStrategy("trend")).toBe("codex");
  });
});
