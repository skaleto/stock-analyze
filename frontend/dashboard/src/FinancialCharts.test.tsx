import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const chartMocks = vi.hoisted(() => {
  const setData = vi.fn();
  const applyPriceScaleOptions = vi.fn();
  const addSeries = vi.fn(() => ({ setData, priceScale: () => ({ applyOptions: applyPriceScaleOptions }) }));
  const remove = vi.fn();
  const subscribeCrosshairMove = vi.fn();
  const fitContent = vi.fn();
  const createChart = vi.fn(() => ({
    addSeries,
    remove,
    subscribeCrosshairMove,
    timeScale: () => ({ fitContent }),
    applyOptions: vi.fn(),
  }));
  return { setData, addSeries, remove, subscribeCrosshairMove, fitContent, createChart };
});

vi.mock("lightweight-charts", () => ({
  createChart: chartMocks.createChart,
  AreaSeries: { type: "area" },
  CandlestickSeries: { type: "candlestick" },
  HistogramSeries: { type: "histogram" },
  LineSeries: { type: "line" },
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: 0 },
}));

import { CandlestickChart, PerformanceChart } from "./FinancialCharts";


describe("financial charts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
  });

  it("renders portfolio and benchmark as interactive percentage series", () => {
    render(
      <PerformanceChart
        points={[
          { date: "2026-07-09", return: 0, benchmark_return: 0 },
          { date: "2026-07-10", return: 0.01, benchmark_return: 0.005 },
        ]}
        benchmarkLabel="组合基准"
      />
    );

    expect(chartMocks.createChart).toHaveBeenCalledTimes(1);
    expect(chartMocks.addSeries).toHaveBeenCalledTimes(2);
    expect(screen.getByText("组合净值")).toBeInTheDocument();
    expect(screen.getByText("组合基准")).toBeInTheDocument();
    expect(chartMocks.subscribeCrosshairMove).toHaveBeenCalledTimes(1);
  });

  it("initializes the readout when nav points arrive after the first render", () => {
    const { rerender } = render(
      <PerformanceChart points={[]} benchmarkLabel="组合基准" />
    );

    rerender(
      <PerformanceChart
        points={[{ date: "2026-07-10", return: 0, benchmark_return: null }]}
        benchmarkLabel="组合基准"
      />
    );

    expect(screen.getByText("2026-07-10")).toBeInTheDocument();
    expect(screen.getByText("0.00%")).toBeInTheDocument();
  });

  it("renders candlesticks and volume and releases the chart", () => {
    const { unmount } = render(
      <CandlestickChart
        candles={[
          { date: "2026-07-10", open: 2.1, high: 2.2, low: 2.0, close: 2.15, volume: 1000, amount: 2200 },
        ]}
      />
    );

    expect(chartMocks.addSeries).toHaveBeenCalledTimes(2);
    expect(chartMocks.setData).toHaveBeenCalled();
    unmount();
    expect(chartMocks.remove).toHaveBeenCalled();
  });
});
