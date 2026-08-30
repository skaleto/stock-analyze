import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceShell } from "./WorkspaceShell";

describe("WorkspaceShell", () => {
  it("renders five stable workspaces and the defensive strategy hierarchy", () => {
    render(
      <WorkspaceShell
        route={{
          view: "strategy",
          mode: "detail",
          market: "a_share",
          strategy: "defensive",
        }}
        marketContext="a_share"
        title="稳健防守"
        subtitle="A股"
        busy={false}
        autoRefresh
        onNavigate={vi.fn()}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    const nav = screen.getByRole("navigation", { name: "工作区" });
    const topLevelLabels = [
      "决策总览",
      "模型研究",
      "永久投资组合",
      "数据与情报",
      "运行中心",
      "策略工作台",
    ];
    const renderedTopLevelLabels = within(nav)
      .getAllByRole("button")
      .map((button) => button.textContent?.trim())
      .filter((label): label is string => (
        Boolean(label) && topLevelLabels.includes(label as string)
      ));
    expect(renderedTopLevelLabels).toEqual(topLevelLabels);

    const strategyWorkspace = within(nav).getByRole("button", {
      name: "策略工作台",
    });
    expect(strategyWorkspace).toHaveAttribute("aria-expanded", "true");
    const strategyBranch = strategyWorkspace.parentElement;
    expect(strategyBranch).not.toBeNull();
    expect(
      within(strategyBranch as HTMLElement).getByRole("button", {
        name: "策略对比",
      }),
    ).toBeInTheDocument();
    expect(
      within(strategyBranch as HTMLElement).getByText("单策略分析"),
    ).toBeInTheDocument();
    expect(
      within(strategyBranch as HTMLElement).getByRole("button", {
        name: "稳健防守",
      }),
    ).toHaveClass("active");
    expect(
      within(strategyBranch as HTMLElement).getByRole("button", {
        name: "趋势进攻",
      }),
    ).toBeInTheDocument();

    for (const label of topLevelLabels.filter(
      (item) => item !== "策略工作台",
    )) {
      expect(
        within(nav).getByRole("button", { name: label }),
      ).not.toHaveAttribute("aria-expanded");
    }
  });

  it("marks only the exact strategy destination as the current page", () => {
    const sharedProps = {
      marketContext: "a_share" as const,
      title: "策略工作台",
      subtitle: "A股",
      busy: false,
      autoRefresh: true,
      onNavigate: vi.fn(),
      onRefresh: vi.fn(),
      onToggleAutoRefresh: vi.fn(),
    };
    const { rerender } = render(
      <WorkspaceShell
        {...sharedProps}
        route={{
          view: "strategy",
          mode: "detail",
          market: "a_share",
          strategy: "defensive",
        }}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    const expectExactCurrentPage = (name: string) => {
      const nav = screen.getByRole("navigation", { name: "工作区" });
      expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
      expect(within(nav).getByRole("button", { name })).toHaveAttribute(
        "aria-current",
        "page",
      );
      expect(
        within(nav).getByRole("button", { name: "策略工作台" }),
      ).not.toHaveAttribute("aria-current");
    };

    expectExactCurrentPage("稳健防守");

    rerender(
      <WorkspaceShell
        {...sharedProps}
        route={{ view: "strategy", mode: "compare", market: "a_share" }}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    expectExactCurrentPage("策略对比");
  });

  it("does not expose a global market selector outside the strategy branch", () => {
    render(
      <WorkspaceShell
        route={{ view: "operations", scope: "all" }}
        marketContext="cn_qdii_etf"
        title="运行中心"
        subtitle="全部市场"
        busy={false}
        autoRefresh
        onNavigate={vi.fn()}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    expect(
      screen.queryByRole("navigation", { name: "市场范围" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("投资市场")).not.toBeInTheDocument();
    expect(screen.queryByText("运行范围")).not.toBeInTheDocument();
  });

  it("navigates from a strategy market to global model research", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShell
        route={{ view: "strategy", mode: "compare", market: "a_share" }}
        marketContext="a_share"
        title="策略对比"
        subtitle="A股"
        busy={false}
        autoRefresh
        onNavigate={onNavigate}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    await user.click(screen.getByRole("button", { name: "模型研究" }));
    expect(onNavigate).toHaveBeenCalledWith({
      view: "model-research",
    });
  });

  it("keeps a defensive detail route valid when switching markets", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShell
        route={{
          view: "strategy",
          mode: "detail",
          market: "a_share",
          strategy: "defensive",
        }}
        marketContext="a_share"
        title="稳健防守"
        subtitle="A股"
        busy={false}
        autoRefresh
        onNavigate={onNavigate}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    const scope = screen.getByRole("navigation", { name: "策略市场" });
    expect(
      screen.queryByRole("navigation", { name: "市场范围" }),
    ).not.toBeInTheDocument();
    await user.click(
      within(scope).getByRole("button", { name: "跨境ETF" }),
    );
    expect(onNavigate).toHaveBeenCalledWith({
      view: "strategy",
      mode: "detail",
      market: "cn_qdii_etf",
      strategy: "defensive",
    });
  });

  it("keeps compare routes strategy-free when switching markets", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShell
        route={{ view: "strategy", mode: "compare", market: "a_share" }}
        marketContext="a_share"
        title="策略对比"
        subtitle="A股"
        busy={false}
        autoRefresh
        onNavigate={onNavigate}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    const scope = screen.getByRole("navigation", { name: "策略市场" });
    await user.click(
      within(scope).getByRole("button", { name: "跨境ETF" }),
    );
    expect(onNavigate).toHaveBeenCalledWith({
      view: "strategy",
      mode: "compare",
      market: "cn_qdii_etf",
    });
  });

  it("keeps the system overview market-neutral", () => {
    render(
      <WorkspaceShell
        route={{ view: "system" }}
        marketContext="cn_qdii_etf"
        title="决策总览"
        subtitle="双市场"
        busy={false}
        autoRefresh
        onNavigate={vi.fn()}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    expect(
      screen.queryByRole("navigation", { name: "策略市场" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("全局工作区")).toBeInTheDocument();
    expect(screen.getByText("模拟策略")).toBeInTheDocument();
  });

  it("renders header and rail slots with working refresh controls", async () => {
    const onRefresh = vi.fn();
    const onToggleAutoRefresh = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShell
        route={{ view: "model-research" }}
        marketContext="a_share"
        title="模型研究"
        subtitle="A股"
        busy
        autoRefresh={false}
        headerActions={<button type="button">导出</button>}
        railStatus={<div>模型状态</div>}
        onNavigate={vi.fn()}
        onRefresh={onRefresh}
        onToggleAutoRefresh={onToggleAutoRefresh}
      >
        <div>workspace content</div>
      </WorkspaceShell>,
    );

    const heading = screen.getByRole("heading", { name: "模型研究" });
    expect(heading).toBeInTheDocument();
    expect(within(heading.closest("header") as HTMLElement).getByText("A股"))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出" })).toBeInTheDocument();
    expect(screen.getByText("模型状态")).toBeInTheDocument();
    expect(screen.getByText("workspace content")).toBeInTheDocument();

    const refresh = screen.getByRole("button", { name: "刷新 dashboard" });
    expect(refresh).toHaveAttribute("aria-busy", "true");
    expect(refresh.querySelector("svg")).toHaveClass("spin");
    await user.click(refresh);
    expect(onRefresh).toHaveBeenCalledOnce();

    const autoRefresh = screen.getByRole("button", {
      name: "自动刷新已关闭",
    });
    expect(autoRefresh).toHaveAttribute("aria-pressed", "false");
    await user.click(autoRefresh);
    expect(onToggleAutoRefresh).toHaveBeenCalledOnce();
  });

  it("keeps workspace navigation in a stable rail container", () => {
    render(
      <WorkspaceShell
        route={{ view: "model-research" }}
        marketContext="a_share"
        title="模型研究"
        subtitle="A股"
        busy={false}
        autoRefresh
        onNavigate={vi.fn()}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    expect(
      screen.queryByRole("navigation", { name: "市场范围" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "工作区" })).toHaveClass(
      "rail-analysis-nav",
    );
    expect(screen.getByRole("main")).toHaveClass("app-shell");
  });

  it("keeps rail controls separate from the bounded workspace surface", () => {
    render(
      <WorkspaceShell
        route={{ view: "operations", scope: "all" }}
        marketContext="a_share"
        title="运行中心"
        subtitle="全部范围"
        busy={false}
        autoRefresh
        onNavigate={vi.fn()}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div data-testid="bounded-content">content</div>
      </WorkspaceShell>,
    );

    const shell = screen.getByRole("main");
    const rail = shell.querySelector(".left-rail");
    const workspace = shell.querySelector(".workspace");
    expect(rail).not.toBeNull();
    expect(workspace).not.toBeNull();
    expect(rail).toContainElement(
      screen.getByRole("navigation", { name: "工作区" }),
    );
    expect(workspace).toContainElement(screen.getByTestId("bounded-content"));
  });
});
