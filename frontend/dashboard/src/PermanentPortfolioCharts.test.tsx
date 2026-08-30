import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PermanentPortfolioCharts } from "./PermanentPortfolioCharts";

const fixed = [
  {
    date: "2018-01-02",
    normalized_nav: 1,
    drawdown: 0,
    volatility_63d: 0.08,
  },
  {
    date: "2023-08-30",
    normalized_nav: 1.4,
    drawdown: -0.03,
    volatility_63d: 0.09,
  },
  {
    date: "2025-01-02",
    normalized_nav: 1.6,
    drawdown: -0.01,
    volatility_63d: 0.1,
  },
  {
    date: "2026-08-30",
    normalized_nav: 1.8,
    drawdown: -0.02,
    volatility_63d: 0.11,
  },
];

const dynamic = fixed.map((point) => ({
  ...point,
  normalized_nav: (point.normalized_nav ?? 0) + 0.02,
}));

describe("PermanentPortfolioCharts", () => {
  it("renders an empty chart state without invalid date calculations", () => {
    render(
      <PermanentPortfolioCharts
        series={{ fixed: [], dynamic: [] }}
      />,
    );

    expect(screen.getByText("暂无可展示的历史序列")).toBeInTheDocument();
  });

  it("renders stable chart surfaces for complete series", () => {
    render(
      <PermanentPortfolioCharts
        series={{
          fixed: [
            {
              date: "2024-12-31",
              normalized_nav: 1,
              drawdown: 0,
              volatility_63d: 0.08,
            },
            {
              date: "2025-01-02",
              normalized_nav: 1.01,
              drawdown: -0.01,
              volatility_63d: 0.09,
            },
          ],
          dynamic: [
            {
              date: "2024-12-31",
              normalized_nav: 1,
              drawdown: 0,
              volatility_63d: 0.1,
            },
            {
              date: "2025-01-02",
              normalized_nav: 1.02,
              drawdown: -0.02,
              volatility_63d: 0.11,
            },
          ],
        }}
        stageBoundary={{
          date: "2025-01-01",
          before_label: "开发期",
          after_label: "盲测期",
        }}
      />,
    );

    expect(
      screen.getByLabelText("永久组合净值对比图"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("永久组合回撤图")).toBeInTheDocument();
    expect(
      screen.getByLabelText("永久组合滚动波动图"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("开发期")).toHaveLength(3);
    expect(screen.getAllByText("盲测期")).toHaveLength(3);
  });

  it("zooms, pans, and resets the shared chart window directly", () => {
    render(
      <PermanentPortfolioCharts
        series={{ fixed, dynamic }}
        stageBoundary={{
          date: "2025-01-01",
          before_label: "开发期",
          after_label: "盲测期",
        }}
      />,
    );

    expect(screen.getByText("2023-08-30 至 2026-08-30"))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "最近3年" }))
      .not.toBeInTheDocument();
    expect(screen.queryByLabelText("图表开始日期")).not.toBeInTheDocument();

    const chart = screen.getByLabelText("永久组合净值对比图");
    Object.defineProperty(chart, "getBoundingClientRect", {
      value: () => ({
        left: 0,
        width: 1000,
        top: 0,
        height: 220,
        right: 1000,
        bottom: 220,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    });
    fireEvent.wheel(chart, { clientX: 500, deltaY: -100 });

    const zoomedCharts = screen.getAllByRole("img");
    const zoomedStart = zoomedCharts[0].getAttribute("data-start-date");
    const zoomedEnd = zoomedCharts[0].getAttribute("data-end-date");
    expect(zoomedStart).not.toBe("2023-08-30");
    expect(zoomedCharts.every((item) => (
      item.getAttribute("data-start-date") === zoomedStart
      && item.getAttribute("data-end-date") === zoomedEnd
    ))).toBe(true);

    fireEvent(chart, Object.assign(new Event("pointerdown", { bubbles: true }), {
      button: 0,
      clientX: 500,
      pointerId: 1,
    }));
    expect(chart).toHaveClass("is-panning");
    fireEvent(chart, Object.assign(new Event("pointermove", { bubbles: true }), {
      clientX: 900,
      pointerId: 1,
    }));
    fireEvent(chart, Object.assign(new Event("pointerup", { bubbles: true }), {
      clientX: 900,
      pointerId: 1,
    }));

    expect([
      chart.getAttribute("data-start-date"),
      chart.getAttribute("data-end-date"),
    ]).not.toEqual([zoomedStart, zoomedEnd]);
    expect(chart).not.toHaveClass("is-panning");

    fireEvent.doubleClick(chart);
    for (const item of screen.getAllByRole("img")) {
      expect(item).toHaveAttribute("data-start-date", "2023-08-30");
      expect(item).toHaveAttribute("data-end-date", "2026-08-30");
    }
  });

  it("keeps the same zoomed window on every chart", () => {
    render(<PermanentPortfolioCharts series={{ fixed, dynamic }} />);

    const chart = screen.getByLabelText("永久组合回撤图");
    fireEvent.wheel(chart, { clientX: 500, deltaY: -100 });

    const charts = screen.getAllByRole("img");
    const start = charts[0].getAttribute("data-start-date");
    const end = charts[0].getAttribute("data-end-date");
    expect(charts.every((item) => (
      item.getAttribute("data-start-date") === start
      && item.getAttribute("data-end-date") === end
    ))).toBe(true);
  });

  it("shows benchmark lines, colored rebalance points, and hover details", () => {
    render(
      <PermanentPortfolioCharts
        series={{ fixed, dynamic }}
        benchmarks={[
          {
            id: "equity_buy_hold",
            name: "沪深300买入持有",
            series: fixed,
          },
        ]}
        trades={{
          fixed: [{
            trade_date: "2025-01-02",
            side: "buy",
            role: "equity",
            code: "510300.SH",
            shares: 100,
            price: 4.2,
          }],
          dynamic: [{
            trade_date: "2025-01-02",
            side: "sell",
            role: "gold",
            code: "518880.SH",
            shares: 200,
            price: 6.3,
          }],
        }}
      />,
    );

    expect(screen.getByText("沪深300买入持有")).toBeInTheDocument();
    expect(screen.getByText("买入")).toHaveClass("buy");
    expect(screen.getByText("卖出")).toHaveClass("sell");
    expect(document.querySelectorAll(".chart-line-benchmark")).toHaveLength(1);
    const buy = screen.getByRole("button", {
      name: "买入调仓 固定永久组合 2025-01-02",
    });
    const sell = screen.getByRole("button", {
      name: "卖出调仓 动态永久组合 2025-01-02",
    });
    expect(buy).toHaveClass("buy");
    expect(sell).toHaveClass("sell");

    fireEvent.mouseEnter(buy);
    fireEvent.mouseMove(buy);
    expect(screen.getByRole("tooltip")).toHaveTextContent("2025-01-02");
    expect(screen.getByRole("tooltip")).toHaveTextContent("510300.SH");
    expect(screen.getByRole("tooltip")).toHaveTextContent("100份");
    expect(screen.getByRole("tooltip")).toHaveTextContent("4.20");
  });
});
