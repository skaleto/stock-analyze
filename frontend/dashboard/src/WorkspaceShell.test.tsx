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
      "策略工作台",
      "模型研究",
      "数据与情报",
      "运行中心",
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

  it("navigates to the exact operations exception scope", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShell
        route={{ view: "operations", scope: "all" }}
        marketContext="cn_qdii_etf"
        title="运行中心"
        subtitle="全部市场"
        busy={false}
        autoRefresh
        onNavigate={onNavigate}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    const scope = screen.getByRole("navigation", { name: "市场范围" });
    expect(scope.querySelector(".segmented")).toHaveClass(
      "segmented-operations",
    );
    expect(
      within(scope).getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["全部", "A股", "跨境ETF", "仅异常"]);
    await user.click(
      within(scope).getByRole("button", { name: "仅异常" }),
    );
    expect(onNavigate).toHaveBeenCalledWith({
      view: "operations",
      scope: "exceptions",
    });
  });

  it("preserves the selected A-share market across aware workspaces", async () => {
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
      market: "a_share",
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

    const scope = screen.getByRole("navigation", { name: "市场范围" });
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

    const scope = screen.getByRole("navigation", { name: "市场范围" });
    await user.click(
      within(scope).getByRole("button", { name: "跨境ETF" }),
    );
    expect(onNavigate).toHaveBeenCalledWith({
      view: "strategy",
      mode: "compare",
      market: "cn_qdii_etf",
    });
  });

  it("drills from the system market scope into strategy comparison", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShell
        route={{ view: "system" }}
        marketContext="cn_qdii_etf"
        title="决策总览"
        subtitle="双市场"
        busy={false}
        autoRefresh
        onNavigate={onNavigate}
        onRefresh={vi.fn()}
        onToggleAutoRefresh={vi.fn()}
      >
        <div>content</div>
      </WorkspaceShell>,
    );

    const scope = screen.getByRole("navigation", { name: "市场范围" });
    expect(
      within(scope).getAllByRole("button").map((button) => button.textContent),
    ).toEqual(["A股", "跨境ETF"]);
    await user.click(within(scope).getByRole("button", { name: "A股" }));
    expect(onNavigate).toHaveBeenCalledWith({
      view: "strategy",
      mode: "compare",
      market: "a_share",
    });
  });

  it("renders header and rail slots with working refresh controls", async () => {
    const onRefresh = vi.fn();
    const onToggleAutoRefresh = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShell
        route={{ view: "model-research", market: "a_share" }}
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
});
