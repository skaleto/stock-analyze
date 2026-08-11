import { describe, expect, it } from "vitest";
import {
  agentForStrategy,
  parseWorkspaceRoute,
  routeSearchMatches,
  serializeWorkspaceRoute,
  type WorkspaceRoute,
} from "./workspaceRoute";

const legacyRouteCases = [
  ["?view=compare&market=a_share&agent=codex", {
    view: "strategy", mode: "compare", market: "a_share",
  }],
  ["?view=detail&market=a_share&agent=claude", {
    view: "strategy", mode: "detail", market: "a_share", strategy: "defensive",
  }],
  ["?view=model-shadow&market=cn_qdii_etf&agent=model_shadow", {
    view: "model-research", focus: "cn_qdii_etf",
  }],
  ["?view=model-iteration&market=a_share", {
    view: "model-research", focus: "a_share",
  }],
  ["?view=intelligence&market=a_share&agent=model_shadow", {
    view: "data-intelligence", focus: "a_share",
  }],
] as const satisfies ReadonlyArray<readonly [string, WorkspaceRoute]>;

const canonicalRouteCases = [
  ["system", {
    view: "system",
  }, "view=system"],
  ["strategy compare", {
    view: "strategy", mode: "compare", market: "a_share",
  }, "view=strategy&mode=compare&market=a_share"],
  ["defensive strategy detail", {
    view: "strategy", mode: "detail", market: "a_share", strategy: "defensive",
  }, "view=strategy&mode=detail&market=a_share&strategy=defensive"],
  ["trend strategy detail", {
    view: "strategy", mode: "detail", market: "cn_qdii_etf", strategy: "trend",
  }, "view=strategy&mode=detail&market=cn_qdii_etf&strategy=trend"],
  ["model research", {
    view: "model-research",
  }, "view=model-research"],
  ["model research detail", {
    view: "model-research", focus: "a_share",
  }, "view=model-research&focus=a_share"],
  ["data intelligence", {
    view: "data-intelligence",
  }, "view=data-intelligence"],
  ["data intelligence detail", {
    view: "data-intelligence", focus: "cn_qdii_etf",
  }, "view=data-intelligence&focus=cn_qdii_etf"],
  ["operations", {
    view: "operations", scope: "exceptions",
  }, "view=operations&scope=exceptions"],
] as const satisfies ReadonlyArray<readonly [string, WorkspaceRoute, string]>;

const defaultMarketCases = [
  ["?view=strategy&mode=detail", {
    view: "strategy",
    mode: "detail",
    market: "cn_qdii_etf",
    strategy: "trend",
  }],
  ["?view=detail&market=hk", {
    view: "strategy",
    mode: "detail",
    market: "cn_qdii_etf",
    strategy: "trend",
  }],
  ["?view=model-research&market=hk", {
    view: "model-research",
  }],
  ["?view=model-shadow", {
    view: "model-research",
  }],
  ["?view=data-intelligence", {
    view: "data-intelligence",
  }],
  ["?view=intelligence&market=us", {
    view: "data-intelligence",
  }],
] as const satisfies ReadonlyArray<readonly [string, WorkspaceRoute]>;

// @ts-expect-error -- Detail routes require a public strategy key.
const detailWithoutStrategy = { view: "strategy", mode: "detail", market: "a_share" } satisfies WorkspaceRoute;

// @ts-expect-error -- Compare routes cannot carry a detail strategy.
const compareWithStrategy = { view: "strategy", mode: "compare", market: "a_share", strategy: "trend" } satisfies WorkspaceRoute;

void detailWithoutStrategy;
void compareWithStrategy;

describe("workspace route contract", () => {
  it.each(legacyRouteCases)("migrates %s", (search, expected) => {
    const route = parseWorkspaceRoute(search);
    expect(route).toEqual(expected);
    expect(serializeWorkspaceRoute(route)).not.toContain("agent=");
  });

  it.each(canonicalRouteCases)(
    "serializes and round trips canonical %s routes",
    (_label, route, expectedSearch) => {
      const serialized = serializeWorkspaceRoute(route);

      expect(serialized).toBe(expectedSearch);
      expect(parseWorkspaceRoute(serialized)).toEqual(route);
      expect(serializeWorkspaceRoute(parseWorkspaceRoute(serialized))).toBe(expectedSearch);
      expect(routeSearchMatches(`?${expectedSearch}`, route)).toBe(true);

      for (const internalName of ["agent=", "claude", "codex", "model_shadow"]) {
        expect(serialized).not.toContain(internalName);
      }
    },
  );

  it("requires legacy and extra agent parameters to be normalized", () => {
    const route = {
      view: "strategy",
      mode: "detail",
      market: "a_share",
      strategy: "defensive",
    } satisfies WorkspaceRoute;

    expect(routeSearchMatches(
      "?view=strategy&mode=detail&market=a_share&strategy=defensive",
      route,
    )).toBe(true);
    expect(routeSearchMatches("?view=detail&market=a_share&agent=claude", route)).toBe(false);
    expect(routeSearchMatches(
      "?view=strategy&mode=detail&market=a_share&strategy=defensive&agent=claude",
      route,
    )).toBe(false);
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

  it.each(defaultMarketCases)(
    "normalizes invalid or absent detail market for %s",
    (search, expected) => {
      expect(parseWorkspaceRoute(search)).toEqual(expected);
    },
  );

  it.each([
    "?view=operations&scope=broken",
    "?view=operations",
    "?view=operations&scope=a_share",
    "?view=operations&scope=cn_qdii_etf",
  ])("defaults invalid operations scope in %s to all", (search) => {
    expect(parseWorkspaceRoute(search)).toEqual({
      view: "operations",
      scope: "all",
    });
  });

  it("maps public strategy keys only at the API boundary", () => {
    expect(agentForStrategy("defensive")).toBe("claude");
    expect(agentForStrategy("trend")).toBe("codex");
  });
});
