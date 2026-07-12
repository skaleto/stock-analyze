import { AlertTriangle, Building2, Database, Filter, Layers3, ShieldCheck } from "lucide-react";
import { accountLabel, formatPercent } from "./finance";
import type { PortfolioLookthrough, SelectionSnapshot } from "./types";


const reasonLabels: Record<string, string> = {
  paused_or_stale: "停牌或行情过期",
  liquidity_below_floor: "流动性不足",
  listing_too_recent: "上市时间过短",
  abnormal_premium: "异常溢价",
  fund_size_below_floor: "基金规模不足",
  peer_tracking_error_high: "同指数跟踪偏差高",
  management_fee_high: "管理费偏高",
  insufficient_factor_coverage: "因子覆盖不足",
  candidate_cap: "候选上限",
};

const gapLabels: Record<string, string> = {
  discount_premium: "折溢价未测量",
  fund_size_yuan: "基金规模未测量",
  peer_tracking_error_60: "同指数偏离未测量",
  history_incomplete: "历史行情不完整",
};

function coverageStatus(status?: string): string {
  if (status === "complete") return "完整覆盖";
  if (status === "partial") return "部分覆盖";
  return "暂无穿透";
}

export default function EtfResearchPanel({
  selection,
  lookthrough,
}: {
  selection?: SelectionSnapshot;
  lookthrough?: PortfolioLookthrough | Record<string, never>;
}) {
  const scopes = Object.entries(selection?.scopes ?? {});
  const exposure = lookthrough && "indexes" in lookthrough ? lookthrough as PortfolioLookthrough : null;
  const latestSourceDate = exposure?.sources
    .map((source) => source.as_of || "")
    .filter(Boolean)
    .sort()
    .slice(-1)[0];

  return (
    <section className="etf-research terminal-section" role="region" aria-label="ETF候选与底层暴露">
      <header className="section-heading etf-research-heading">
        <div>
          <span className="section-kicker"><Filter size={14} aria-hidden="true" />ETF RESEARCH</span>
          <h2>候选漏斗与底层暴露</h2>
          <p>{selection?.as_of || "等待周度选基"} · {coverageStatus(exposure?.status)}{latestSourceDate ? ` · 资料 ${latestSourceDate}` : ""}</p>
        </div>
        <div className="universe-version">
          <span><Database size={13} aria-hidden="true" />共享候选版本</span>
          <strong>{selection?.universe_hash || "等待生成"}</strong>
        </div>
      </header>

      <div className="etf-research-grid">
        <div className="selection-funnels">
          <div className="research-subhead"><Layers3 size={15} aria-hidden="true" /><h3>选择过程</h3></div>
          {scopes.length === 0 ? <p className="terminal-empty">等待周度调仓生成候选漏斗</p> : scopes.map(([scope, block]) => {
            const baseline = Math.max(block.stages[0]?.count ?? 0, 1);
            const dataGaps = Object.entries(block.data_gaps ?? {}).filter(([, count]) => count > 0);
            return (
              <section className="funnel-scope" key={scope} aria-label={`${accountLabel(scope)}候选漏斗`}>
                <header><b>{accountLabel(scope)}</b><span>{block.stages.slice(-1)[0]?.count ?? 0} / {block.stages[0]?.count ?? 0}</span></header>
                <div className="funnel-stages">
                  {block.stages.map((stage) => (
                    <div key={stage.key}>
                      <span><b>{stage.label}</b><strong>{stage.count}</strong></span>
                      <i><em style={{ width: `${Math.max((stage.count / baseline) * 100, stage.count > 0 ? 3 : 0)}%` }} /></i>
                    </div>
                  ))}
                </div>
                {block.rejections?.length ? (
                  <div className="rejection-line">
                    <span><ShieldCheck size={13} aria-hidden="true" />风险淘汰</span>
                    {block.rejections.slice(0, 4).map((item) => (
                      <b key={item.reason}>{reasonLabels[item.reason] || item.reason} {item.count}</b>
                    ))}
                  </div>
                ) : null}
                {dataGaps.length ? (
                  <div className="data-gap-line">
                    <span><AlertTriangle size={13} aria-hidden="true" />风控降级</span>
                    {dataGaps.slice(0, 4).map(([key, count]) => (
                      <b key={key}>{gapLabels[key] || `${key} 未测量`} {count}</b>
                    ))}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>

        <div className="lookthrough-panel">
          <div className="research-subhead">
            <Building2 size={15} aria-hidden="true" />
            <h3>真实底层暴露</h3>
            <span className={`coverage-badge coverage-${exposure?.status || "unavailable"}`}>{coverageStatus(exposure?.status)}</span>
          </div>
          <div className="coverage-readout">
            <div><span>指数资料覆盖</span><strong>{formatPercent(exposure?.profile_coverage)}</strong></div>
            <div><span>公司权重覆盖</span><strong>{formatPercent(exposure?.company_weight_coverage)}</strong></div>
          </div>
          <div className="exposure-columns">
            <div>
              <h4>底层指数</h4>
              {(exposure?.indexes ?? []).slice(0, 5).map((item) => (
                <div className="exposure-row" key={item.index_key}>
                  <span><b>{item.label}</b><small>{item.index_key}</small></span>
                  <strong>{formatPercent(item.weight)}</strong>
                </div>
              ))}
              {!exposure?.indexes.length ? <p className="comparison-pending">暂无指数暴露</p> : null}
            </div>
            <div>
              <h4>底层公司</h4>
              {(exposure?.companies ?? []).slice(0, 5).map((item) => (
                <div className="exposure-row" key={item.symbol}>
                  <span><b>{item.name}</b><small>{item.symbol} · {item.sector}</small></span>
                  <strong>{formatPercent(item.weight)}</strong>
                </div>
              ))}
              {!exposure?.companies.length ? <p className="comparison-pending">暂无可计权公司数据</p> : null}
            </div>
          </div>
          {exposure?.unsupported_indexes.length ? (
            <p className="coverage-note">尚未覆盖 {exposure.unsupported_indexes.length} 个指数：{exposure.unsupported_indexes.slice(0, 3).join("、")}</p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
