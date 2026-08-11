import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TermDisplay } from "./TermDisplay";

describe("TermDisplay", () => {
  it("shows a Chinese label, explanation and original code", () => {
    render(<TermDisplay code="adjusted_ohlcv" kind="source" />);

    expect(screen.getByText("复权行情数据")).toBeInTheDocument();
    expect(screen.getByText(/开盘、最高、最低、收盘/)).toBeInTheDocument();
    expect(screen.getByText("adjusted_ohlcv")).toBeInTheDocument();
  });

  it("makes an unknown term explicit while preserving traceability", () => {
    render(<TermDisplay code="future_feature_not_registered" kind="feature" />);

    expect(screen.getByText("未收录特征")).toBeInTheDocument();
    expect(screen.getByText(/尚未配置中文说明/)).toBeInTheDocument();
    expect(screen.getByText("future_feature_not_registered")).toBeInTheDocument();
  });
});
