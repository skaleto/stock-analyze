import { describe, expect, it } from "vitest";
import { accountLabel, fieldMeta, formatFieldValue, sideLabel } from "./finance";


describe("finance field dictionary", () => {
  it("translates common stock indicators for beginners", () => {
    expect(fieldMeta("roe").label).toBe("净资产收益率 ROE");
    expect(fieldMeta("roe").explanation).toContain("股东投入");
    expect(fieldMeta("gross_margin").label).toBe("毛利率");
    expect(fieldMeta("debt_ratio").label).toBe("资产负债率");
  });

  it("translates account and order vocabulary", () => {
    expect(sideLabel("buy")).toBe("买入");
    expect(sideLabel("sell")).toBe("卖出");
    expect(accountLabel("us_exposure")).toBe("美国市场ETF账户");
  });

  it("formats ratio and money fields by meaning", () => {
    expect(formatFieldValue("roe", 0.1532)).toBe("15.32%");
    expect(formatFieldValue("avg_amount_20", 12345678)).toContain("1,234.57万");
    expect(formatFieldValue("shares", 1200)).toBe("1,200");
  });
});
