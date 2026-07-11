import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import CompetitionPanel from "./CompetitionPanel";
import type { StrategyComparison } from "./types";

vi.mock("./FinancialCharts", () => ({
  StrategyComparisonChart: () => <div>双策略净值图</div>,
}));

const comparison: StrategyComparison = {
  market: "cn_qdii_etf",
  season: {
    id: "dual_strategy_2026_s1",
    name: "双策略对抗 · 赛季1",
    effective_date: "2026-07-11",
    anchor_date: "2026-07-10",
  },
  strategies: {
    claude: {
      agent: "claude",
      label: "稳健防守",
      description: "价值质量、低波与低换手",
      color: "#d6a84b",
      strategy_id: "defensive_global_etf_v1",
      strategy_name: "稳健防守 · 低波均衡",
      holdings_source: "positions",
      allocations: [
        { label: "美国市场", value: 600000, weight: 0.6 },
        { label: "香港市场", value: 400000, weight: 0.4 },
      ],
      metrics: {
        season_return: 0.032,
        benchmark_return: 0.018,
        excess_return: 0.014,
        annualized_volatility: 0.11,
        sharpe: 1.28,
        max_drawdown: -0.025,
        cash_ratio: 0.15,
        turnover: 0.22,
        trading_cost: 86.4,
        cost_bps: 7.2,
        position_count: 8,
        pending_order_count: 2,
        trade_count: 12,
      },
    },
    codex: {
      agent: "codex",
      label: "趋势进攻",
      description: "动量成长与主动换仓",
      color: "#22d3ee",
      strategy_id: "trend_global_etf_v1",
      strategy_name: "趋势进攻 · 全球动量",
      holdings_source: "planned_orders",
      allocations: [
        { label: "美国市场", value: 0.7, weight: 0.7 },
        { label: "香港市场", value: 0.3, weight: 0.3 },
      ],
      metrics: {
        season_return: 0.051,
        benchmark_return: 0.018,
        excess_return: 0.033,
        annualized_volatility: 0.19,
        sharpe: 1.51,
        max_drawdown: -0.041,
        cash_ratio: 0.08,
        turnover: 0.63,
        trading_cost: 221.8,
        cost_bps: 9.4,
        position_count: 10,
        pending_order_count: 10,
        trade_count: 18,
      },
    },
  },
  pair: {
    position_overlap: 0.43,
    return_correlation: 0.36,
    factor_distance: 0.65,
    factor_distance_floor: 0.45,
  },
  nav_series: [
    { date: "2026-07-10", claude: 0, codex: 0, benchmark: 0 },
    { date: "2026-07-14", claude: 0.032, codex: 0.051, benchmark: 0.018 },
  ],
  factor_rows: [
    {
      key: "low_volatility_60",
      label: "近60日波动率",
      explanation: "越低越稳定。",
      claude: { weight: 0.45, direction: "low" },
      codex: { weight: 0.05, direction: "low" },
    },
    {
      key: "momentum_20",
      label: "近20日动量",
      explanation: "近期趋势强弱。",
      claude: { weight: 0, direction: null },
      codex: { weight: 0.4, direction: "high" },
    },
  ],
};

describe("CompetitionPanel", () => {
  it("renders season, metric matrix, divergence, factors and allocations", () => {
    render(
      <CompetitionPanel
        comparison={comparison}
        activeAgent="codex"
        currency="¥"
        onSelectAgent={() => undefined}
      />
    );

    const panel = screen.getByRole("region", { name: "双策略竞技场" });
    expect(within(panel).getByText("双策略对抗 · 赛季1")).toBeInTheDocument();
    expect(within(panel).getAllByText("稳健防守").length).toBeGreaterThan(0);
    expect(within(panel).getAllByText("趋势进攻").length).toBeGreaterThan(0);
    expect(within(panel).getByText("持仓重合度")).toBeInTheDocument();
    expect(within(panel).getByText("收益相关性")).toBeInTheDocument();
    expect(within(panel).getByText("因子差异度")).toBeInTheDocument();
    expect(within(panel).getByText("年化波动")).toBeInTheDocument();
    expect(within(panel).getByText("赛季换手")).toBeInTheDocument();
    expect(within(panel).getByText("近60日波动率")).toBeInTheDocument();
    expect(within(panel).getAllByText("美国市场").length).toBe(2);
    expect(within(panel).getByText("来自目标订单")).toBeInTheDocument();
    expect(within(panel).getByText("双策略净值图")).toBeInTheDocument();
  });

  it("switches the selected strategy from the strategy header", async () => {
    const onSelectAgent = vi.fn();
    const user = userEvent.setup();
    render(
      <CompetitionPanel
        comparison={comparison}
        activeAgent="codex"
        currency="¥"
        onSelectAgent={onSelectAgent}
      />
    );

    await user.click(screen.getByRole("button", { name: "查看稳健防守明细" }));

    expect(onSelectAgent).toHaveBeenCalledWith("claude");
  });

  it("uses explicit accumulating-data text for unavailable statistics", () => {
    const sparse: StrategyComparison = {
      ...comparison,
      pair: { ...comparison.pair, return_correlation: null },
      strategies: {
        ...comparison.strategies,
        claude: {
          ...comparison.strategies.claude,
          metrics: {
            ...comparison.strategies.claude.metrics,
            annualized_volatility: null,
            sharpe: null,
          },
        },
      },
    };

    render(
      <CompetitionPanel
        comparison={sparse}
        activeAgent="codex"
        currency="¥"
        onSelectAgent={() => undefined}
      />
    );

    expect(screen.getAllByText("数据积累中").length).toBeGreaterThanOrEqual(3);
  });

  it("shows only the top five allocations and combines the remainder", () => {
    const crowded: StrategyComparison = {
      ...comparison,
      strategies: {
        ...comparison.strategies,
        claude: {
          ...comparison.strategies.claude,
          allocations: [
            { label: "板块一", value: 30, weight: 0.30 },
            { label: "板块二", value: 22, weight: 0.22 },
            { label: "板块三", value: 16, weight: 0.16 },
            { label: "板块四", value: 12, weight: 0.12 },
            { label: "板块五", value: 9, weight: 0.09 },
            { label: "板块六", value: 7, weight: 0.07 },
            { label: "板块七", value: 4, weight: 0.04 },
          ],
        },
      },
    };

    render(
      <CompetitionPanel
        comparison={crowded}
        activeAgent="claude"
        currency="¥"
        onSelectAgent={() => undefined}
      />
    );

    expect(screen.getByText("板块五")).toBeInTheDocument();
    expect(screen.queryByText("板块六")).not.toBeInTheDocument();
    expect(screen.queryByText("板块七")).not.toBeInTheDocument();
    const remainder = screen.getByText("其他 2 项").closest("span");
    expect(remainder).not.toBeNull();
    expect(within(remainder as HTMLElement).getByText("11.00%")).toBeInTheDocument();
  });
});
