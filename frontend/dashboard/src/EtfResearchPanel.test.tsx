import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import EtfResearchPanel from "./EtfResearchPanel";


describe("ETF research panel", () => {
  it("shows the selection funnel, shared universe version and measured exposures", () => {
    render(
      <EtfResearchPanel
        selection={{
          schema_version: 1,
          as_of: "2026-07-10",
          universe_hash: "a1b2c3d4e5f60708",
          scopes: {
            us_exposure: {
              stages: [
                { key: "catalog", label: "动态目录", count: 26 },
                { key: "risk_ready", label: "风险闸门后", count: 11 },
                { key: "portfolio_target", label: "目标持仓", count: 5 },
              ],
              rejections: [
                { reason: "abnormal_premium", count: 2 },
                { reason: "liquidity_below_floor", count: 6 },
              ],
              data_gaps: {
                discount_premium: 3,
                fund_size_yuan: 1,
                peer_tracking_error_60: 0,
              },
              selected: [],
            },
          },
        }}
        lookthrough={{
          status: "partial",
          source: "planned_orders",
          profile_coverage: 0.8,
          company_weight_coverage: 0.54,
          indexes: [
            { index_key: "nasdaq_100", label: "纳斯达克100", weight: 0.6, profile_available: true },
            { index_key: "sp_500", label: "标普500", weight: 0.4, profile_available: true },
          ],
          countries: [{ label: "美国", weight: 1 }],
          sectors: [{ label: "信息技术", weight: 0.58 }],
          companies: [
            { symbol: "NVDA", name: "英伟达", sector: "信息技术", weight: 0.076 },
            { symbol: "AAPL", name: "苹果", sector: "信息技术", weight: 0.0667 },
          ],
          company_symbols: ["NVDA", "AAPL"],
          sources: [{ index_key: "nasdaq_100", name: "纳斯达克100", as_of: "2026-06-30", source_url: "https://example.com", source_label: "official" }],
          unsupported_indexes: ["msci_us_50"],
        }}
      />
    );

    const panel = screen.getByRole("region", { name: "ETF候选与底层暴露" });
    expect(within(panel).getByText("a1b2c3d4e5f60708")).toBeInTheDocument();
    expect(within(panel).getByText("动态目录")).toBeInTheDocument();
    expect(within(panel).getByText("26")).toBeInTheDocument();
    expect(within(panel).getByText("目标持仓")).toBeInTheDocument();
    expect(within(panel).getByText("纳斯达克100")).toBeInTheDocument();
    expect(within(panel).getByText("英伟达")).toBeInTheDocument();
    expect(within(panel).getByText("80.00%")).toBeInTheDocument();
    expect(within(panel).getByText("54.00%")).toBeInTheDocument();
    expect(within(panel).getByText(/异常溢价/)).toBeInTheDocument();
    expect(within(panel).getByText(/风控降级/)).toBeInTheDocument();
    expect(within(panel).getByText("折溢价未测量 3")).toBeInTheDocument();
    expect(within(panel).getByText("基金规模未测量 1")).toBeInTheDocument();
    expect(within(panel).getByText("部分覆盖")).toBeInTheDocument();
  });
});
