import { fireEvent, render, screen, within } from "@testing-library/react";
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

  it("switches between shadow, event and theme research views", () => {
    render(
      <EtfResearchPanel
        selection={{ schema_version: 1, scopes: {} }}
        research={{
          capacity: { run_id: "capacity-run", recommendations: [{ strategy: "codex", scope: "us_exposure", recommended_top_n: 5 }], metrics: [] },
          shadow: {
            run_id: "shadow-run",
            mode: "research_only",
            metrics: [
              { strategy_variant: "defensive_shadow", asset_class: "global_equity", scope: "japan_exposure", factor_model: "global_equity_defensive_v1", cumulative_return: 0.12, sharpe_ratio: 0.8, max_drawdown: -0.1, promotion_status: "shadow_ready" },
              { strategy_variant: "trend_shadow", asset_class: "global_equity", scope: "japan_exposure", factor_model: "global_equity_trend_v1", cumulative_return: 0.18, sharpe_ratio: 1.0, max_drawdown: -0.12, promotion_status: "shadow_ready" },
            ],
            catalog: [{ code: "159866.SZ", name: "日经ETF", asset_class: "global_equity", research_scope: "japan_exposure", promotion_status: "shadow_ready" }],
          },
          events: { total: 1, active_hard_blocks: 1, latest_observed_at: "2026-07-10T08:00:00", source: "eastmoney_fund_announcements", rows: [{ event_id: "AN1", code: "513100.SH", title: "暂停申购公告", event_type: "suspension", severity: "hard", published_at: "2026-07-10T00:00:00", source_url: "https://example.test/a" }] },
          theme_sentiment: [{ agent: "codex", week_end: "2026-07-10", index_key: "nikkei_225", score: 0.4, confidence: 0.8, drivers: "日元走弱", sources: "https://example.test/n", observed_at: "2026-07-10T08:00:00" }],
        }}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "全球影子" }));
    expect(screen.getAllByText("日本市场")).toHaveLength(2);
    expect(screen.getByText("12.00%")).toBeInTheDocument();
    expect(screen.getByText("稳健防守")).toBeInTheDocument();
    expect(screen.getByText("趋势进攻")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "风险事件" }));
    expect(screen.getByText("暂停申购公告")).toBeInTheDocument();
    expect(screen.getByText("1 项硬阻断")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "主题观点" }));
    expect(screen.getByText("nikkei_225")).toBeInTheDocument();
    expect(screen.getByText("日元走弱")).toBeInTheDocument();
  });
});
