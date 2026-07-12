import { useState } from "react";
import { AlertTriangle, Building2, Database, ExternalLink, Filter, FlaskConical, Globe2, Layers3, MessageSquareText, ShieldAlert, ShieldCheck } from "lucide-react";
import { accountLabel, formatPercent } from "./finance";
import type { PortfolioLookthrough, QDIIResearch, SelectionSnapshot } from "./types";

const reasonLabels: Record<string, string> = {
  active_fund_event_block: "基金公告硬阻断",
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

const scopeLabels: Record<string, string> = {
  us_exposure: "美国市场",
  hk_exposure: "香港市场",
  japan_exposure: "日本市场",
  europe_exposure: "欧洲市场",
  saudi_exposure: "沙特市场",
  other_global_exposure: "其他全球市场",
  commodity_oil: "海外原油",
  commodity_precious_metals: "贵金属",
  commodity_broad: "全球商品",
  bond_overseas: "海外债券",
};

function coverageStatus(status?: string): string {
  if (status === "complete") return "完整覆盖";
  if (status === "partial") return "部分覆盖";
  return "暂无穿透";
}

function statusLabel(status?: string): string {
  if (status === "shadow_ready") return "影子运行";
  if (status === "insufficient_breadth") return "产品不足";
  return "研究中";
}

function CandidateView({ selection, exposure }: { selection?: SelectionSnapshot; exposure: PortfolioLookthrough | null }) {
  const scopes = Object.entries(selection?.scopes ?? {});
  return <div className="etf-research-grid">
    <div className="selection-funnels">
      <div className="research-subhead"><Layers3 size={15} aria-hidden="true" /><h3>选择过程</h3></div>
      {scopes.length === 0 ? <p className="terminal-empty">等待周度调仓生成候选漏斗</p> : scopes.map(([scope, block]) => {
        const baseline = Math.max(block.stages[0]?.count ?? 0, 1);
        const dataGaps = Object.entries(block.data_gaps ?? {}).filter(([, count]) => count > 0);
        return <section className="funnel-scope" key={scope} aria-label={`${accountLabel(scope)}候选漏斗`}>
          <header><b>{accountLabel(scope)}</b><span>{block.stages.slice(-1)[0]?.count ?? 0} / {block.stages[0]?.count ?? 0}</span></header>
          <div className="funnel-stages">{block.stages.map((stage) => <div key={stage.key}>
            <span><b>{stage.label}</b><strong>{stage.count}</strong></span>
            <i><em style={{ width: `${Math.max((stage.count / baseline) * 100, stage.count > 0 ? 3 : 0)}%` }} /></i>
          </div>)}</div>
          {block.rejections?.length ? <div className="rejection-line"><span><ShieldCheck size={13} aria-hidden="true" />风险淘汰</span>{block.rejections.slice(0, 4).map((item) => <b key={item.reason}>{reasonLabels[item.reason] || item.reason} {item.count}</b>)}</div> : null}
          {dataGaps.length ? <div className="data-gap-line"><span><AlertTriangle size={13} aria-hidden="true" />风控降级</span>{dataGaps.slice(0, 4).map(([key, count]) => <b key={key}>{gapLabels[key] || `${key} 未测量`} {count}</b>)}</div> : null}
        </section>;
      })}
    </div>
    <div className="lookthrough-panel">
      <div className="research-subhead"><Building2 size={15} aria-hidden="true" /><h3>真实底层暴露</h3><span className={`coverage-badge coverage-${exposure?.status || "unavailable"}`}>{coverageStatus(exposure?.status)}</span></div>
      <div className="coverage-readout"><div><span>指数资料覆盖</span><strong>{formatPercent(exposure?.profile_coverage)}</strong></div><div><span>公司权重覆盖</span><strong>{formatPercent(exposure?.company_weight_coverage)}</strong></div></div>
      <div className="exposure-columns">
        <div><h4>底层指数</h4>{(exposure?.indexes ?? []).slice(0, 5).map((item) => <div className="exposure-row" key={item.index_key}><span><b>{item.label}</b><small>{item.index_key}</small></span><strong>{formatPercent(item.weight)}</strong></div>)}{!exposure?.indexes.length ? <p className="comparison-pending">暂无指数暴露</p> : null}</div>
        <div><h4>底层公司</h4>{(exposure?.companies ?? []).slice(0, 5).map((item) => <div className="exposure-row" key={item.symbol}><span><b>{item.name}</b><small>{item.symbol} · {item.sector}</small></span><strong>{formatPercent(item.weight)}</strong></div>)}{!exposure?.companies.length ? <p className="comparison-pending">暂无可计权公司数据</p> : null}</div>
      </div>
      {exposure?.unsupported_indexes.length ? <p className="coverage-note">尚未覆盖 {exposure.unsupported_indexes.length} 个指数：{exposure.unsupported_indexes.slice(0, 3).join("、")}</p> : null}
    </div>
  </div>;
}

function ShadowView({ research }: { research?: QDIIResearch }) {
  const rows = research?.shadow?.metrics ?? [];
  const recommendations = research?.capacity?.recommendations ?? [];
  const strategyLabel = (variant?: string) => variant === "defensive_shadow" ? "稳健防守" : variant === "trend_shadow" ? "趋势进攻" : "研究策略";
  return <div className="research-view research-shadow-view">
    <div className="research-summary-strip"><div><span>影子范围</span><strong>{rows.length}</strong></div><div><span>目录产品</span><strong>{research?.shadow?.catalog?.length ?? 0}</strong></div><div><span>容量建议</span><strong>{recommendations.filter((item) => item.recommended_top_n != null).length}</strong></div><div><span>运行标识</span><strong>{research?.shadow?.run_id ?? "等待运行"}</strong></div></div>
    <div className="research-table-wrap"><table className="research-table"><thead><tr><th>范围</th><th>策略</th><th>模型</th><th>累计</th><th>Sharpe</th><th>回撤</th><th>状态</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.scope}-${row.strategy_variant}-${index}`}><td><b>{scopeLabels[row.scope ?? ""] ?? row.scope}</b><small>{row.asset_class}</small></td><td><span className={`research-state state-${row.strategy_variant}`}>{strategyLabel(row.strategy_variant)}</span></td><td>{row.factor_model}</td><td className={(row.cumulative_return ?? 0) >= 0 ? "positive" : "negative"}>{formatPercent(row.cumulative_return)}</td><td>{row.sharpe_ratio?.toFixed(2) ?? "-"}</td><td className="negative">{formatPercent(row.max_drawdown)}</td><td><span className={`research-state state-${row.promotion_status}`}>{statusLabel(row.promotion_status)}</span></td></tr>)}</tbody></table>{rows.length === 0 ? <p className="terminal-empty">等待每周影子研究生成净值</p> : null}</div>
    {recommendations.length ? <div className="capacity-line"><b>当前容量证据</b>{recommendations.map((item) => <span key={`${item.strategy}-${item.scope}`}>{item.strategy} · {scopeLabels[item.scope ?? ""] ?? item.scope}: {item.recommended_top_n == null ? "暂不晋级" : `建议 ${item.recommended_top_n} 只`}</span>)}</div> : null}
  </div>;
}

function EventView({ research }: { research?: QDIIResearch }) {
  const events = research?.events;
  return <div className="research-view"><div className="event-status-line"><span className={(events?.active_hard_blocks ?? 0) > 0 ? "event-alert" : "event-clear"}><ShieldAlert size={15} />{events?.active_hard_blocks ?? 0} 项硬阻断</span><span>共 {events?.total ?? 0} 条公告</span><span>观测至 {events?.latest_observed_at?.slice(0, 16).replace("T", " ") ?? "尚未同步"}</span></div><div className="event-timeline">{(events?.rows ?? []).map((event) => <article key={event.event_id ?? `${event.code}-${event.published_at}`}><i className={`event-dot event-${event.severity ?? "info"}`} /><div><header><b>{event.title}</b><span>{event.published_at?.slice(0, 10)}</span></header><p>{event.code} · {event.event_type}</p></div>{event.source_url ? <a href={event.source_url} target="_blank" rel="noreferrer" aria-label={`查看${event.title}来源`}><ExternalLink size={14} /></a> : null}</article>)}{!events?.rows?.length ? <p className="terminal-empty">等待同步基金公告</p> : null}</div></div>;
}

function ThemeView({ research }: { research?: QDIIResearch }) {
  const rows = research?.theme_sentiment ?? [];
  return <div className="research-view theme-grid">{rows.map((row) => <article key={`${row.agent}-${row.week_end}-${row.index_key}`}><header><b>{row.index_key}</b><span className={(row.score ?? 0) >= 0 ? "positive" : "negative"}>{(row.score ?? 0) >= 0 ? "+" : ""}{row.score?.toFixed(2)}</span></header><p>{row.drivers || "暂无驱动摘要"}</p><footer><span>置信度 {formatPercent(row.confidence)}</span><span>{row.week_end}</span>{row.sources ? <a href={row.sources.split("|")[0]} target="_blank" rel="noreferrer"><ExternalLink size={12} />来源</a> : null}</footer></article>)}{rows.length === 0 ? <p className="terminal-empty">尚无可审计的指数级主题观点</p> : null}</div>;
}

export default function EtfResearchPanel({ selection, lookthrough, research }: { selection?: SelectionSnapshot; lookthrough?: PortfolioLookthrough | Record<string, never>; research?: QDIIResearch }) {
  const [view, setView] = useState<"candidate" | "shadow" | "events" | "theme">("candidate");
  const exposure = lookthrough && "indexes" in lookthrough ? lookthrough as PortfolioLookthrough : null;
  const latestSourceDate = exposure?.sources.map((source) => source.as_of || "").filter(Boolean).sort().slice(-1)[0];
  const tabs = [
    { key: "candidate" as const, label: "候选与暴露", icon: Filter },
    { key: "shadow" as const, label: "全球影子", icon: Globe2 },
    { key: "events" as const, label: "风险事件", icon: ShieldAlert },
    { key: "theme" as const, label: "主题观点", icon: MessageSquareText },
  ];
  return <section className="etf-research terminal-section" role="region" aria-label="ETF候选与底层暴露">
    <header className="section-heading etf-research-heading"><div><span className="section-kicker"><FlaskConical size={14} />ETF RESEARCH</span><h2>跨境 ETF 研究工作台</h2><p>{selection?.as_of || "等待周度选基"} · {coverageStatus(exposure?.status)}{latestSourceDate ? ` · 资料 ${latestSourceDate}` : ""}</p></div><div className="universe-version"><span><Database size={13} />共享候选版本</span><strong>{selection?.universe_hash || "等待生成"}</strong></div></header>
    <nav className="research-tabs" aria-label="ETF研究视图">{tabs.map((tab) => <button type="button" key={tab.key} className={view === tab.key ? "active" : ""} onClick={() => setView(tab.key)}><tab.icon size={14} />{tab.label}</button>)}</nav>
    {view === "candidate" ? <CandidateView selection={selection} exposure={exposure} /> : null}
    {view === "shadow" ? <ShadowView research={research} /> : null}
    {view === "events" ? <EventView research={research} /> : null}
    {view === "theme" ? <ThemeView research={research} /> : null}
  </section>;
}
