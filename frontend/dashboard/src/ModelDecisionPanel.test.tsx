import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ModelDecisionPanel from "./ModelDecisionPanel";

describe("ModelDecisionPanel", () => {
  it("explains a cash-only decision with its funnel and near misses", () => {
    render(<ModelDecisionPanel status={{
      display_version: "A20-V005",
      cash_only: true,
      cash_reason: "模型未发现满足条件的上行机会",
      decision_diagnostics: {
        outcome: "cash",
        summary: "722只证券通过置信度门槛，但下跌概率均不低于上涨概率，候选模拟组合保持现金",
        regime: "risk_off",
        funnel: [
          { key: "predictions", label: "预测证券", count: 845 },
          { key: "valid", label: "数据有效", count: 722 },
          { key: "confidence", label: "置信度达标", count: 722 },
          { key: "direction", label: "上涨概率占优", count: 0 },
          { key: "positive_excess", label: "预期超额为正", count: 0 },
        ],
        near_misses: [{
          code: "300450",
          name: "先导智能",
          confidence: 0.6878,
          p_up: 0.329,
          p_down: 0.5351,
          expected_excess_return: -0.00633,
          failed_rules: ["下跌概率不低于上涨概率", "预期超额收益不高于0"],
        }],
      },
    }} />);

    const panel = screen.getByRole("region", { name: "本期模型决策" });
    expect(within(panel).getByText("本期决策")).toBeInTheDocument();
    expect(within(panel).getByText("持币观察")).toBeInTheDocument();
    expect(within(panel).getByText(/722只证券通过置信度门槛/)).toBeInTheDocument();
    expect(within(panel).getByText("风险规避")).toBeInTheDocument();
    expect(within(panel).getByText("845")).toBeInTheDocument();
    expect(within(panel).getAllByText("0")).toHaveLength(2);
    expect(within(panel).getByText("先导智能")).toBeInTheDocument();
    expect(within(panel).getByText("上涨 32.9%")).toBeInTheDocument();
    expect(within(panel).getByText("预期超额")).toBeInTheDocument();
    expect(within(panel).getByText("-0.63%")).toBeInTheDocument();
    expect(within(panel).getByText("下跌概率不低于上涨概率")).toBeInTheDocument();
  });
});
