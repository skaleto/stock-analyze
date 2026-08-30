import { render, screen } from "@testing-library/react";
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
            annualized_volatility: 0.08,
            max_drawdown: -0.06,
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
            annualized_volatility: 0.1,
            max_drawdown: -0.08,
          },
          series: [],
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

  it("shows the continuous historical return and risk comparison", async () => {
    render(<PermanentPortfolioPage refreshToken={0} />);

    expect(
      await screen.findByRole("heading", { name: "收益与波动" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("固定永久组合").length).toBeGreaterThan(0);
    expect(screen.getAllByText("动态永久组合").length).toBeGreaterThan(0);
  });

  it("shows executable holdings and timing", async () => {
    render(<PermanentPortfolioPage refreshToken={0} />);

    expect(
      await screen.findByRole("heading", { name: /历史期末持仓与目标/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("510300.SH")).toBeInTheDocument();
    expect(screen.getByText("12,000")).toBeInTheDocument();
    expect(screen.getByText("每日收盘检查阈值")).toBeInTheDocument();
  });
});
