import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PermanentPortfolioPage } from "./PermanentPortfolioPage";
import { fetchPermanentPortfolio } from "./api";

vi.mock("./api", () => ({
  fetchPermanentPortfolio: vi.fn(),
}));

const payload = {
  schemaVersion: 1 as const,
  generatedAt: "2026-08-30T12:00:00+00:00",
  status: "available",
  study: {
    studyId: "permanent_portfolio_v1",
    status: "development_complete",
    initialCash: 200000,
    contractSha256: "contract",
    developmentSha256: "development",
  },
  assets: [
    { role: "equity", code: "510300.SH", name: "沪深300ETF" },
  ],
  strategies: [
    { id: "fixed", name: "固定永久组合" },
    { id: "dynamic", name: "动态永久组合" },
  ],
  benchmarks: [],
  windows: {
    historical: {
      status: "complete",
      start_date: "20180102",
      end_date: "20260828",
      stage_boundaries: [{
        date: "20250101",
        before_label: "开发期",
        after_label: "盲测期",
      }],
      portfolios: {
        fixed: {
          metrics: {
            cumulative_return: 0.1,
            annualized_return: 0.074,
            annualized_volatility: 0.08,
            max_drawdown: -0.06,
            sharpe_vs_cash: 0.8,
          },
          series: [],
          nav: [{
            date: "20241231",
            cash: 10000,
            market_value: 190000,
            total_value: 200000,
          }],
          positions: [{
            role: "equity",
            code: "510300.SH",
            shares: 12000,
            last_price: 4,
            market_value: 48000,
          }],
          targets: [{
            role: "equity",
            signal_date: "20240321",
            target_weight: 0.25,
          }],
          pending: [],
        },
        dynamic: {
          metrics: {
            cumulative_return: 0.12,
            annualized_return: 0.082,
            annualized_volatility: 0.1,
            max_drawdown: -0.08,
            sharpe_vs_cash: 0.7,
          },
          series: [],
          nav: [{
            date: "20241231",
            cash: 8000,
            market_value: 212000,
            total_value: 220000,
          }],
          positions: [{
            role: "equity",
            code: "510300.SH",
            shares: 18000,
            last_price: 4,
            market_value: 72000,
          }],
          targets: [{
            role: "equity",
            signal_date: "20241231",
            target_weight: 0.4,
          }],
          pending: [],
        },
      },
    },
    forward: { status: "unavailable" },
  },
  errors: [],
};

describe("PermanentPortfolioPage", () => {
  beforeEach(() => {
    vi.mocked(fetchPermanentPortfolio).mockResolvedValue(payload);
  });

  it("shows only historical and forward stages", async () => {
    render(<PermanentPortfolioPage refreshToken={0} />);

    expect(await screen.findByRole("button", { name: "历史数据回测" }))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "前向纸面期" }))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "开发期" }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "盲测期" }))
      .not.toBeInTheDocument();
  });

  it("leads with the selected strategy outcome and risk summary", async () => {
    render(<PermanentPortfolioPage refreshToken={0} />);

    expect(await screen.findByRole("heading", { name: "固定永久组合" }))
      .toBeInTheDocument();
    const summary = screen.getByLabelText("固定永久组合核心指标");
    expect(within(summary).getByText("年化收益")).toBeInTheDocument();
    expect(within(summary).getByText("7.40%")).toBeInTheDocument();
    expect(within(summary).getByText("最大回撤")).toBeInTheDocument();
    expect(within(summary).getByText("-6.00%")).toBeInTheDocument();
    expect(within(summary).getByText("现金超额 Sharpe"))
      .toBeInTheDocument();
  });

  it("switches the decision context instead of mixing both strategies", async () => {
    render(<PermanentPortfolioPage refreshToken={0} />);

    await screen.findByRole("heading", { name: "固定永久组合" });
    expect(screen.getByText("12,000")).toBeInTheDocument();
    expect(screen.getByText("每日收盘检查阈值")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看动态永久组合" }));

    expect(screen.getByRole("heading", { name: "动态永久组合" }))
      .toBeInTheDocument();
    expect(screen.getByText("18,000")).toBeInTheDocument();
    expect(screen.getByText("每月末检查动量排名")).toBeInTheDocument();
    expect(screen.queryByText("12,000")).not.toBeInTheDocument();
  });

  it("keeps comparison and evidence available without dominating the page", async () => {
    render(<PermanentPortfolioPage refreshToken={0} />);

    await screen.findByRole("heading", { name: "固定永久组合" });
    const comparison = screen.getByRole("button", { name: "展开完整策略对比" });
    expect(comparison).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("table", { name: "完整策略指标对比" }))
      .not.toBeInTheDocument();

    fireEvent.click(comparison);
    expect(screen.getByRole("table", { name: "完整策略指标对比" }))
      .toBeInTheDocument();
    expect(screen.getByText("研究证据与校验哈希")).toBeInTheDocument();
  });

  it("labels v2 as a corrected sealed retest instead of a pristine blind test", async () => {
    vi.mocked(fetchPermanentPortfolio).mockResolvedValue({
      ...payload,
      study: {
        ...payload.study,
        studyId: "permanent_portfolio_v2",
        status: "holdout_complete",
        validity: "corrected_retest",
        accountingVersion: "cash_distributions_v2",
        evidenceClass: "bug_corrected_sealed_retest",
      },
      correction: {
        v1Status: "invalidated",
        reason: "v1会计口径失效；v2使用原始价格估值并显式计入现金分红。",
        holdoutLabel: "纠错封存复测",
      },
    });

    render(<PermanentPortfolioPage refreshToken={0} />);

    expect(await screen.findByText("v2 纠错封存复测已完成"))
      .toBeInTheDocument();
    expect(screen.getByText(/v1会计口径失效/)).toBeInTheDocument();
    expect(screen.queryByText("开发与盲测已封存")).not.toBeInTheDocument();
  });
});
