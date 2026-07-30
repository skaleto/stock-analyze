import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AlertCenter from "./AlertCenter";

const alerts = [
  { id: "a", type: "opportunity" as const, severity: "high" as const, title: "纳指ETF上行预警", detail: "5日上涨概率72%", code: "513100", horizon: 5 },
  { id: "b", type: "downside" as const, severity: "medium" as const, title: "黄金ETF下行风险", detail: "下行概率60%", code: "518880", horizon: 5 },
];

describe("AlertCenter", () => {
  it("filters alerts with keyboard-accessible buttons", () => {
    render(<AlertCenter alerts={alerts} />);
    fireEvent.click(screen.getByRole("button", { name: "机会" }));
    expect(screen.getByText("纳指ETF上行预警")).toBeInTheDocument();
    expect(screen.queryByText("黄金ETF下行风险")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "机会" })).toHaveAttribute("aria-pressed", "true");
  });
});
