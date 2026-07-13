import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ModelHealthPanel from "./ModelHealthPanel";

describe("ModelHealthPanel", () => {
  it("shows one honest quality row for every prediction horizon", () => {
    render(
      <ModelHealthPanel
        health={{
          status: "available",
          models: [3, 5, 10, 20].map((horizon) => ({
            horizon,
            model_version: `m${horizon}`,
            status: "research",
            metrics: { log_loss: 1.05 + horizon / 1000, brier_score: 0.64, calibration_quality: 0.02 },
            gate_reasons: ["auc", "brier_improvement"],
            shadow_cycles: 0,
            shadow_cycles_remaining: 4,
          })),
        }}
        regimes={{ status: "available", current: { composite_regime: "unknown" } }}
        sources={[
          { source: "news", status: "source_unavailable" },
          { source: "announcement", status: "source_unavailable" },
          { source: "policy", status: "source_unavailable" },
        ]}
      />,
    );

    expect(screen.getAllByText("研究中")).toHaveLength(5);
    expect(screen.queryByText("已校准")).not.toBeInTheDocument();
    for (const horizon of [3, 5, 10, 20]) expect(screen.getByText(`${horizon}日`)).toBeInTheDocument();
    expect(screen.getAllByText("0/4")).toHaveLength(4);
    expect(screen.getByText("未接入文本源 3")).toBeInTheDocument();
    expect(screen.getByText("待补证据：区分能力、概率校准")).toBeInTheDocument();
  });
});
