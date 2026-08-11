import { describe, expect, it } from "vitest";
import { termMeta } from "./terminology";

describe("dashboard terminology", () => {
  it("explains the current production sources and feature families in Chinese", () => {
    expect(termMeta("adjusted_ohlcv", "source")).toMatchObject({
      label: "复权行情数据",
      known: true,
    });
    expect(termMeta("tushare_anns_d", "source").explanation).toContain(
      "上市公司公告",
    );
    expect(termMeta("technical", "family")).toMatchObject({
      label: "技术面特征",
      known: true,
    });
  });

  it("explains financial, technical, event and model terms consistently", () => {
    expect(termMeta("momentum_20", "feature").label).toBe("近20日涨跌");
    expect(termMeta("macd_hist", "feature").explanation).toContain("MACD");
    expect(termMeta("event_net_strength_5d", "factor").label).toBe(
      "5日事件净强度",
    );
    expect(termMeta("boosting_ensemble", "algorithm").label).toBe(
      "提升树集成模型",
    );
    expect(termMeta("rank_ic", "metric").label).toBe("排序相关性");
  });

  it("never presents an unknown code as if it were a Chinese explanation", () => {
    expect(termMeta("future_feature_not_registered", "feature")).toEqual({
      label: "未收录特征",
      explanation: "该特征尚未配置中文说明，请结合原始编码追溯。",
      known: false,
    });
  });
});
