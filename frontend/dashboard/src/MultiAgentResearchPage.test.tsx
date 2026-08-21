import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MultiAgentResearchPage } from "./MultiAgentResearchPage";

function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MultiAgentResearchPage", () => {
  it("shows completed artifacts and research-only universe scope without a run control", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      schemaVersion: "multi-agent-research-dashboard-v1",
      status: "available",
      latestRun: {
        runId: "run-1",
        createdAt: "2026-08-22T01:02:03+00:00",
        status: "completed_with_degradation",
        market: "a_share",
        instrument: { code: "000001.SZ", name: "平安银行" },
        model: "test-model",
        degradedRoles: ["news"],
        digest: "# 简报\n\n仅研究",
        executionEffect: "none_research_only",
        reportPath: "reports/research/multi_agent/a_share/000001.SZ/run-1/full_report.md",
      },
      universe: {
        status: "available",
        asOf: "20260822",
        aShare: { scopeCounts: { csi1000: 1000 }, uniqueInstruments: 1000 },
        funds: {
          sourceCounts: { exchange: 2188, otc: 15000 },
          overseasScopeCounts: { nasdaq_100: 6 },
          classificationCounts: { name_benchmark_inferred: 6 },
        },
      },
      executionEffect: "none_research_only",
    })));

    render(<MultiAgentResearchPage refreshToken={0} />);

    await waitFor(() => expect(screen.getByText("平安银行")).toBeInTheDocument());
    expect(screen.getByText("CSI1000")).toBeInTheDocument();
    expect(screen.getByText("场外基金目录")).toBeInTheDocument();
    expect(screen.getAllByText(/仅研究/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /运行|生成/ })).not.toBeInTheDocument();
  });
});
