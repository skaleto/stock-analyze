import { fireEvent, render, screen, within } from "@testing-library/react";
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

  it("uses one focused chart with metric tabs", () => {
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

    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.getByLabelText("永久组合净值对比图")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "净值走势" }))
      .toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "回撤路径" }));
    expect(screen.getByLabelText("永久组合回撤图")).toBeInTheDocument();
    expect(screen.queryByLabelText("永久组合净值对比图"))
      .not.toBeInTheDocument();
    expect(screen.getAllByText("开发期")).toHaveLength(1);
    expect(screen.getAllByText("盲测期")).toHaveLength(1);
  });

  it("starts with the full evidence window and supports range presets", () => {
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

    expect(screen.getByText("2018-01-02 至 2026-08-30"))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部" }))
      .toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "近3年" }));
    expect(screen.getByText("2023-08-30 至 2026-08-30"))
      .toBeInTheDocument();

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
    const originalStart = chart.getAttribute("data-start-date");
    const originalEnd = chart.getAttribute("data-end-date");
    fireEvent.wheel(chart, { clientX: 500, deltaY: -100 });
    expect(chart).toHaveAttribute("data-start-date", originalStart);
    expect(chart).toHaveAttribute("data-end-date", originalEnd);

    fireEvent.wheel(chart, {
      clientX: 500,
      deltaY: -100,
      ctrlKey: true,
    });

    const zoomedStart = chart.getAttribute("data-start-date");
    const zoomedEnd = chart.getAttribute("data-end-date");
    expect(zoomedStart).not.toBe("2023-08-30");

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
    expect(chart).toHaveAttribute("data-start-date", "2018-01-02");
    expect(chart).toHaveAttribute("data-end-date", "2026-08-30");
  });

  it("keeps the chosen window when the displayed metric changes", () => {
    render(<PermanentPortfolioCharts series={{ fixed, dynamic }} />);

    const chart = screen.getByLabelText("永久组合净值对比图");
    fireEvent.wheel(chart, { clientX: 500, deltaY: -100, ctrlKey: true });
    const start = chart.getAttribute("data-start-date");
    const end = chart.getAttribute("data-end-date");

    fireEvent.click(screen.getByRole("button", { name: "63日波动" }));
    const volatility = screen.getByLabelText("永久组合滚动波动图");
    expect(volatility).toHaveAttribute("data-start-date", start);
    expect(volatility).toHaveAttribute("data-end-date", end);
  });

  it("resets the visible range when a different stage series arrives", () => {
    const { rerender } = render(
      <PermanentPortfolioCharts series={{ fixed, dynamic }} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "近1年" }));

    const forward = [
      { date: "2026-09-01", normalized_nav: 1, drawdown: 0, volatility_63d: 0.08 },
      { date: "2027-02-01", normalized_nav: 1.03, drawdown: -0.01, volatility_63d: 0.09 },
    ];
    rerender(
      <PermanentPortfolioCharts series={{ fixed: forward, dynamic: forward }} />,
    );

    expect(screen.getByText("2026-09-01 至 2027-02-01"))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部" }))
      .toHaveAttribute("aria-pressed", "true");
  });

  it("shows only the selected benchmark with colored rebalance points and hover details", () => {
    render(
      <PermanentPortfolioCharts
        series={{ fixed, dynamic }}
        benchmarks={[
          {
            id: "equity_buy_hold",
            name: "沪深300买入持有",
            series: fixed,
          },
          {
            id: "equal_weight_buy_hold",
            name: "四资产等权买入持有",
            series: dynamic,
          },
          {
            id: "cash_buy_hold",
            name: "现金ETF买入持有",
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

    const legend = screen.getByLabelText("归一化净值图例");
    expect(within(legend).queryByText("沪深300买入持有")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".chart-line-benchmark")).toHaveLength(0);
    fireEvent.change(screen.getByRole("combobox", { name: "选择对比基准" }), {
      target: { value: "equity_buy_hold" },
    });
    expect(within(legend).getByText("沪深300买入持有")).toBeInTheDocument();
    expect(within(legend).queryByText("四资产等权买入持有")).not.toBeInTheDocument();
    expect(screen.getByText("买入")).toHaveClass("buy");
    expect(screen.getByText("卖出")).toHaveClass("sell");
    expect(document.querySelectorAll(".chart-line-benchmark")).toHaveLength(1);

    fireEvent.change(screen.getByRole("combobox", { name: "选择对比基准" }), {
      target: { value: "equal_weight_buy_hold" },
    });
    expect(within(legend).queryByText("沪深300买入持有")).not.toBeInTheDocument();
    expect(within(legend).getByText("四资产等权买入持有")).toBeInTheDocument();
    expect(document.querySelectorAll(".chart-line-benchmark")).toHaveLength(1);
    const buy = screen.getByRole("button", {
      name: "买入调仓 固定永久组合 2025-01-02",
    });
    const sell = screen.getByRole("button", {
      name: "卖出调仓 动态永久组合 2025-01-02",
    });
    expect(buy).toHaveClass("buy");
    expect(sell).toHaveClass("sell");
    expect(document.querySelector(".chart-trade-marker"))
      .toHaveAttribute("r", "2.4");
    expect(document.querySelector(".chart-trade-hit-area"))
      .toHaveAttribute("r", "8");

    fireEvent.mouseEnter(buy);
    fireEvent.mouseMove(buy);
    expect(screen.getByRole("tooltip")).toHaveTextContent("2025-01-02");
    expect(screen.getByRole("tooltip")).toHaveTextContent("510300.SH");
    expect(screen.getByRole("tooltip")).toHaveTextContent("100份");
    expect(screen.getByRole("tooltip")).toHaveTextContent("4.20");
  });

  it("keeps chart typography proportional and explains risk metrics", () => {
    render(<PermanentPortfolioCharts series={{ fixed, dynamic }} />);

    const chart = screen.getByLabelText("永久组合净值对比图");
    expect(chart).toHaveAttribute("preserveAspectRatio", "xMidYMid meet");
    expect(screen.getByRole("note")).toHaveTextContent(
      "以起点净值 1.0000 为基准",
    );

    fireEvent.click(screen.getByRole("button", { name: "回撤路径" }));
    expect(screen.getByRole("note")).toHaveTextContent(
      "相对此前历史最高净值的跌幅",
    );
    expect(screen.getByRole("note")).toHaveTextContent(
      "-8% 表示距离此前高点仍低 8%",
    );

    fireEvent.click(screen.getByRole("button", { name: "63日波动" }));
    expect(screen.getByRole("note")).toHaveTextContent(
      "最近约三个月（63 个交易日）",
    );
    expect(screen.getByRole("note")).toHaveTextContent(
      "不代表涨跌方向",
    );
  });
});
