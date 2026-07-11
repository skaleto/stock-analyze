import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PortfolioSection, TradeTimeline } from "./PortfolioViews";


describe("portfolio views", () => {
  it("groups holdings by underlying market instead of flattening them", () => {
    render(
      <PortfolioSection
        positions={[
          {
            code: "513100.SH",
            name: "纳指ETF",
            exposure_group: "美国市场",
            theme: "纳斯达克100",
            market_value: 300000,
            unrealized_pnl: 1200,
            shares: 1000,
          },
          {
            code: "159920.SZ",
            name: "恒生ETF",
            exposure_group: "香港市场",
            theme: "恒生综合",
            market_value: 200000,
            unrealized_pnl: -800,
            shares: 2000,
          },
        ]}
        planned={[]}
        currency="¥"
        onSelect={vi.fn()}
      />
    );

    const portfolio = screen.getByRole("region", { name: "持仓组合" });
    expect(within(portfolio).getByRole("heading", { name: "美国市场" })).toBeInTheDocument();
    expect(within(portfolio).getByRole("heading", { name: "香港市场" })).toBeInTheDocument();
    expect(within(portfolio).getByText("纳斯达克100")).toBeInTheDocument();
    expect(within(portfolio).getByText("60.00%")).toBeInTheDocument();
  });

  it("labels pending orders as planned holdings when no live positions exist", () => {
    render(
      <PortfolioSection
        positions={[]}
        planned={[
          {
            code: "513100.SH",
            name: "纳指ETF",
            exposure_group: "美国市场",
            theme: "纳斯达克100",
            target_value: 100000,
            shares: 1000,
          },
        ]}
        currency="¥"
        onSelect={vi.fn()}
      />
    );

    expect(screen.getByText("计划持仓")).toBeInTheDocument();
    expect(screen.getByText("等待下一交易日模拟成交")).toBeInTheDocument();
  });

  it("groups completed and planned actions by date", () => {
    render(
      <TradeTimeline
        events={[
          { date: "2026-07-13", code: "513100.SH", name: "纳指ETF", status: "planned", status_label: "计划买入", shares: 1000 },
          { date: "2026-07-10", code: "159920.SZ", name: "恒生ETF", status: "completed", status_label: "已买入", shares: 2000 },
        ]}
        onSelect={vi.fn()}
      />
    );

    const timeline = screen.getByRole("region", { name: "交易时间线" });
    expect(within(timeline).getByText("07月13日")).toBeInTheDocument();
    expect(within(timeline).getByText("计划买入")).toBeInTheDocument();
    expect(within(timeline).getByText("已买入")).toBeInTheDocument();
  });
});
