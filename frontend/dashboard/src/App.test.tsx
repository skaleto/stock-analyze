import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const requests: string[] = [];

vi.mock("./SystemOverviewPanel", () => ({
  default: () => {
    requests.push("/api/dashboard/system-overview.json");
    return <div data-testid="workspace-page">决策总览页面</div>;
  },
}));
vi.mock("./StrategyWorkspacePage", () => ({
  StrategyWorkspacePage: ({
    market,
    mode,
    strategy,
    refreshToken,
  }: {
    market: string;
    mode: string;
    strategy?: string;
    refreshToken: number;
  }) => {
    requests.push(`/api/dashboard/strategy/${market}/${mode}/${strategy ?? ""}`);
    return (
      <div data-testid="workspace-page">
        策略页面 {market} {mode} {strategy ?? "-"} 刷新{refreshToken}
      </div>
    );
  },
}));
vi.mock("./ModelResearchPage", () => ({
  ModelResearchPage: ({
    focus,
    refreshToken,
  }: {
    focus?: string;
    refreshToken: number;
  }) => {
    requests.push(`/api/dashboard/model-research/${focus ?? "global"}`);
    return (
      <div data-testid="workspace-page">
        模型研究页面 {focus ?? "global"} 刷新{refreshToken}
      </div>
    );
  },
}));
vi.mock("./DataIntelligencePage", () => ({
  DataIntelligencePage: ({
    focus,
    refreshToken,
  }: {
    focus?: string;
    refreshToken: number;
  }) => {
    requests.push(`/api/dashboard/data-intelligence/${focus ?? "global"}`);
    return (
      <div data-testid="workspace-page">
        数据与情报页面 {focus ?? "global"} 刷新{refreshToken}
      </div>
    );
  },
}));
vi.mock("./OperationsPage", () => ({
  OperationsPage: ({
    scope,
    refreshToken,
  }: {
    scope: string;
    refreshToken: number;
  }) => {
    requests.push(`/api/dashboard/operations-center.json?scope=${scope}`);
    return (
      <div data-testid="workspace-page">
        运行中心页面 {scope} 刷新{refreshToken}
      </div>
    );
  },
}));

afterEach(() => {
  requests.length = 0;
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

describe("Dashboard app workspace integration", () => {
  it("shows a stable route skeleton while the lazy workspace loads", async () => {
    window.history.replaceState({}, "", "/app.html?view=system");

    render(<App />);

    expect(
      screen.getByLabelText("工作区加载中"),
    ).toBeInTheDocument();
    expect(await screen.findByText("决策总览页面")).toBeInTheDocument();
  });

  it.each([
    ["?view=system", "决策总览页面", "/api/dashboard/system-overview.json"],
    [
      "?view=strategy&mode=compare&market=a_share",
      "策略页面 a_share compare -",
      "/api/dashboard/strategy/a_share/compare/",
    ],
    [
      "?view=strategy&mode=detail&market=cn_qdii_etf&strategy=trend",
      "策略页面 cn_qdii_etf detail trend",
      "/api/dashboard/strategy/cn_qdii_etf/detail/trend",
    ],
    [
      "?view=model-research",
      "模型研究页面 global",
      "/api/dashboard/model-research/global",
    ],
    [
      "?view=data-intelligence",
      "数据与情报页面 global",
      "/api/dashboard/data-intelligence/global",
    ],
    [
      "?view=operations&scope=all",
      "运行中心页面 all",
      "/api/dashboard/operations-center.json?scope=all",
    ],
  ])(
    "dispatches only one canonical workspace for %s",
    async (search, expectedText, expectedRequest) => {
      window.history.replaceState({}, "", `/app.html${search}`);

      render(<App />);

      expect(await screen.findByText(new RegExp(expectedText))).toBeInTheDocument();
      expect(screen.getAllByTestId("workspace-page")).toHaveLength(1);
      expect(new Set(requests)).toEqual(new Set([expectedRequest]));
    },
  );

  it("canonicalizes a legacy model URL exactly once", async () => {
    const replace = vi.spyOn(window.history, "replaceState");
    window.history.replaceState(
      {},
      "",
      "/app.html?view=model-shadow&market=a_share&agent=model_shadow",
    );
    replace.mockClear();

    render(<App />);

    await waitFor(() => {
      expect(window.location.search).toBe("?view=model-research&focus=a_share");
    });
    expect(replace).toHaveBeenCalledTimes(1);
    expect(window.location.search).not.toMatch(/agent|claude|codex|model_shadow/);
  });

  it("canonicalizes legacy agent detail links to public strategy keys", async () => {
    window.history.replaceState(
      {},
      "",
      "/app.html?view=detail&market=a_share&agent=claude",
    );

    render(<App />);

    await waitFor(() => {
      expect(window.location.search).toBe(
        "?view=strategy&mode=detail&market=a_share&strategy=defensive",
      );
    });
    expect(window.location.search).not.toMatch(/agent|claude|codex/);
  });

  it("restores the workspace from browser history without pushing again", async () => {
    const push = vi.spyOn(window.history, "pushState");
    window.history.replaceState({}, "", "/app.html?view=system");
    render(<App />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "模型研究" }));
    expect(window.location.search).toBe(
      "?view=model-research",
    );
    expect(push).toHaveBeenCalledTimes(1);

    window.history.replaceState(
      {},
      "",
      "/app.html?view=operations&scope=exceptions",
    );
    act(() => window.dispatchEvent(new PopStateEvent("popstate")));

    expect(await screen.findByText(/运行中心页面 exceptions/)).toBeInTheDocument();
    expect(push).toHaveBeenCalledTimes(1);
  });

  it("returns to the page top when the active workspace changes", async () => {
    window.history.replaceState({}, "", "/app.html?view=system");
    render(<App />);
    const user = userEvent.setup();
    await screen.findByText("决策总览页面");
    document.documentElement.scrollTop = 420;
    document.body.scrollTop = 420;

    await user.click(screen.getByRole("button", { name: "模型研究" }));

    await waitFor(() => {
      expect(document.documentElement.scrollTop).toBe(0);
      expect(document.body.scrollTop).toBe(0);
    });
  });

  it("keeps strategy search exclusive to detail mode", async () => {
    window.history.replaceState(
      {},
      "",
      "/app.html?view=strategy&mode=compare&market=a_share",
    );
    const { unmount } = render(<App />);
    expect(screen.queryByRole("textbox", { name: "搜索证券" })).not.toBeInTheDocument();
    unmount();

    window.history.replaceState(
      {},
      "",
      "/app.html?view=strategy&mode=detail&market=a_share&strategy=trend",
    );
    render(<App />);
    expect(screen.getByRole("textbox", { name: "搜索证券" })).toBeInTheDocument();
  });

  it("keeps operations filtering inside the operations page", async () => {
    window.history.replaceState(
      {},
      "",
      "/app.html?view=operations&scope=all",
    );
    render(<App />);

    expect(screen.queryByRole("button", { name: "A股" })).not.toBeInTheDocument();
    expect(screen.getByText(/运行中心页面 all/)).toBeInTheDocument();
    expect(window.location.search).toBe("?view=operations&scope=all");
  });

  it("increments the active page refresh token without mounting a second page", async () => {
    window.history.replaceState(
      {},
      "",
      "/app.html?view=model-research",
    );
    render(<App />);
    const user = userEvent.setup();

    expect(screen.getByText(/模型研究页面 global 刷新0/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "刷新 dashboard" }));

    expect(screen.getByText(/模型研究页面 global 刷新1/)).toBeInTheDocument();
    expect(screen.getAllByTestId("workspace-page")).toHaveLength(1);
  });
});
