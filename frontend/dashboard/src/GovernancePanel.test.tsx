import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import GovernancePanel from "./GovernancePanel";
import type { DashboardGovernance } from "./types";

const governance: DashboardGovernance = {
  generated_at: "2026-07-24T08:00:00",
  market: "cn_qdii_etf",
  agent: "codex",
  action_state: {
    status: "warning",
    items: [{ severity: "warning", title: "归因证据待补齐", detail: "行业归因暂不可用" }],
  },
  lineage: {
    status: "available",
    database_integrity: "ok",
    counts: {
      decision_runs: 1,
      candidate_evaluations: 2,
      target_allocations: 1,
      orders: 1,
      fills: 0,
      pnl_attributions: 1,
      experiment_trials: 1,
    },
    decision_runs: [],
    decision_funnel: {
      evaluated: 2,
      eligible: 1,
      selected: 1,
      rejection_counts: { abnormal_premium: 1 },
    },
    candidates: [
      { security_code: "513100.SH", name: "纳指ETF", rank_score: 0.91, eligible: true, selected: true },
      { security_code: "513500.SH", name: "标普500ETF", rank_score: 0.44, eligible: false, selected: false, rejection_reason: "abnormal_premium" },
    ],
    allocations: [],
    orders: [],
    fills: [],
    attributions: [],
    experiments: [],
  },
  risk: {
    status: "available",
    portfolios: [{
      account_id: "us_exposure",
      cash_weight: 0.12,
      expected_alpha: 0.03,
      volatility: 0.18,
      tracking_error: 0.07,
      turnover: 0.14,
      expected_cost: 0.001,
      stress_losses: { "海外科技回撤": -0.08 },
      risk_contributions: { "513100.SH": 0.73 },
      binding_constraints: ["max_single_weight"],
    }],
  },
  attribution: {
    status: "partial",
    rows: [{
      as_of: "2026-07-24",
      security_code: "__PORTFOLIO__",
      net_pnl: 120,
      market_pnl: 75,
      industry_pnl: 0,
      alpha_pnl: 50,
      cash_pnl: 0,
      cost_pnl: -5,
      constraint_pnl: 0,
      residual_pnl: 0,
      reconciliation_delta: 0,
      status: "partial",
      unavailable_inputs: ["industry_returns"],
    }],
  },
  drift: { "5": { status: "healthy", breaches: [] } },
  experiments: [{ trial_id: "trial-1", model_version: "Q5-V003", horizon: 5, rank_ic: 0.08, sharpe: 0.62 }],
  intelligence_evidence: {
    factor_validation: {
      factors: {
        policy_event: {
          recommendation: "observing",
          coverage: 0.61,
          mean_rank_ic: 0.03,
          ic_sign_stability: 0.67,
        },
      },
    },
    quality: {},
  },
  distinctness: {
    status: "breached",
    distinctness_score: 0.41,
    breaches: [{ metric: "daily_decision_agreement", reason: "每日决策过于一致" }],
  },
};

describe("GovernancePanel", () => {
  it("supports evidence drill-down across decision, risk, attribution and research", async () => {
    const user = userEvent.setup();
    render(<GovernancePanel data={governance} />);

    const panel = screen.getByRole("region", { name: "决策与风控" });
    expect(within(panel).getByText("纳指ETF")).toBeInTheDocument();
    expect(within(panel).getAllByText("溢价异常")).toHaveLength(2);

    await user.click(within(panel).getByRole("tab", { name: "风险压力" }));
    expect(within(panel).getByText("海外科技回撤")).toBeInTheDocument();
    expect(within(panel).getByText(/max_single_weight/)).toBeInTheDocument();

    await user.click(within(panel).getByRole("tab", { name: "收益归因" }));
    expect(within(panel).getByText("对账差额")).toBeInTheDocument();
    expect(within(panel).getByText(/industry_returns/)).toBeInTheDocument();

    await user.click(within(panel).getByRole("tab", { name: "模型与情报" }));
    expect(within(panel).getByText((_, element) => element?.textContent === "未达标：每日决策过于一致")).toBeInTheDocument();
    expect(within(panel).getByText("政策事件信号")).toBeInTheDocument();
    expect(within(panel).getByText("policy_event")).toBeInTheDocument();
  });
});
