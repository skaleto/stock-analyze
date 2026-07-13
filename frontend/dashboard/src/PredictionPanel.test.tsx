import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PredictionPanel from "./PredictionPanel";

const summary = {
  status: "available" as const,
  as_of: "2026-07-10",
  horizons: [3, 5, 10, 20],
  rows: [
    { code: "513100", name: "纳指ETF", horizon: 3, p_up: 0.65, p_flat: 0.2, p_down: 0.15, confidence: 0.72, expected_excess_return: 0.02, reasons: ["趋势加速"], invalidation: ["跌破均线"], active_status: "inactive" },
    { code: "513100", name: "纳指ETF", horizon: 5, p_up: 0.72, p_flat: 0.18, p_down: 0.1, confidence: 0.81, expected_excess_return: 0.04, reasons: ["资金流确认"], invalidation: ["状态转弱"], active_status: "inactive" },
  ],
};

describe("PredictionPanel", () => {
  it("switches horizons and keeps probability separate from confidence", () => {
    render(<PredictionPanel summary={summary} />);
    fireEvent.click(screen.getByRole("button", { name: "5日" }));
    expect(screen.getByText("上涨概率")).toBeInTheDocument();
    expect(screen.getByText("72.0%")).toBeInTheDocument();
    expect(screen.getByText("可信度")).toBeInTheDocument();
    expect(screen.getByText("81.0%")).toBeInTheDocument();
    expect(screen.getByText("资金流确认")).toBeInTheDocument();
    expect(screen.getByText("状态转弱")).toBeInTheDocument();
  });

  it("shows an explicit unavailable state", () => {
    render(<PredictionPanel summary={{ status: "unavailable", horizons: [], rows: [] }} />);
    expect(screen.getByText("预测研究尚无可用数据")).toBeInTheDocument();
  });
});
