import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BarChart3,
  BrainCircuit,
  Braces,
  CheckCircle2,
  ChevronRight,
  Database,
  FlaskConical,
  GitCompareArrows,
  Layers3,
  RadioTower,
  ShieldAlert,
  Sigma,
} from "lucide-react";
import { fetchSystemOverview } from "./api";
import type {
  StrategyModelUsage,
  SystemOverviewData,
} from "./types";
import type {
  DashboardMarket,
  WorkspaceRoute,
} from "./workspaceRoute";

type Props = {
  refreshToken?: number;
  onNavigate: (route: WorkspaceRoute) => void;
};

const preferredMarket = "cn_qdii_etf";

function publicMarket(market: string): DashboardMarket {
  return market === "a_share" ? "a_share" : "cn_qdii_etf";
}

const fallbackLabels: Record<string, string> = {
  decision_lineage_missing: "尚未记录本期正式决策证据",
  prediction_artifact_missing: "尚无已晋级模型的预测文件",
  prediction_application_evidence_missing: "决策记录尚未包含模型应用证据",
  no_candidate_prediction_applied: "本期没有候选满足模型应用条件",
  declared_horizon_unavailable_or_ineligible: "策略要求的预测周期尚未就绪或未过门槛",
  prediction_inactive_low_confidence_or_invalid: "预测未激活、置信度不足或已失效",
};

function compact(value: number | null | undefined): string {
  if (value == null) return "-";
  return new Intl.NumberFormat("zh-CN", {
    notation: value >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: 1,
  }).format(value);
}

function usageLabel(usage?: StrategyModelUsage): string {
  if (usage?.status === "active") {
    return `已应用 ${usage.applied_candidates} 个候选`;
  }
  return "经典规则驱动";
}

function usageReason(usage?: StrategyModelUsage): string {
  if (!usage) return fallbackLabels.decision_lineage_missing;
  if (usage.status === "active") {
    const versions = Object.entries(usage.model_versions)
      .map(([horizon, version]) => `${horizon}日 ${version}`)
      .join(" · ");
    return versions || "本期决策已记录模型应用";
  }
  const reasons = usage.fallback_reason
    .split("|")
    .filter(Boolean)
    .map((reason) => fallbackLabels[reason] ?? reason);
  return reasons.join("；") || "未满足模型应用条件";
}

export default function SystemOverviewPanel({
  refreshToken = 0,
  onNavigate,
}: Props) {
  const [data, setData] = useState<SystemOverviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchSystemOverview(controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) setData(payload);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refreshToken]);

  const championCount = data?.models.filter(
    (item) => Boolean(item.iteration?.champion),
  ).length ?? 0;
  const activeStrategyCount = data?.strategy_model_usage.filter(
    (item) => item.status === "active",
  ).length ?? 0;
  const eligibleFactors = data?.intelligence.factorSupply.modelEligibleFactors.length ?? 0;
  const modelConnected = championCount > 0;
  const strategyConnected = activeStrategyCount > 0;
  const loop = data ? [
    {
      key: "source",
      index: "01",
      label: "公告与行情",
      value: compact(data.intelligence.pipeline.stages.catalogued),
      note: `PDF ${compact(data.intelligence.pipeline.stages.pdfReady)} · 已解析 ${compact(data.intelligence.pipeline.stages.parsed)}`,
      status: data.intelligence.pipeline.stages.parsed > 0 ? "connected" : "blocked",
      icon: Database,
      view: "data-intelligence" as const,
    },
    {
      key: "semantic",
      index: "02",
      label: "结构化情报",
      value: compact(data.intelligence.pipeline.stages.canonicalEvents),
      note: `已抽取 ${compact(data.intelligence.pipeline.stages.semanticCompleted)} · 失败 ${compact(data.intelligence.decisions.failed)}`,
      status: data.intelligence.pipeline.stages.semanticCompleted > 0 ? "connected" : "blocked",
      icon: Braces,
      view: "data-intelligence" as const,
    },
    {
      key: "factor",
      index: "03",
      label: "研究因子",
      value: `${eligibleFactors} 个可入模`,
      note: `${data.intelligence.factorSupply.suppliedFactors} 个已计算 · ${data.intelligence.modelImpact.adopted ? "已采用" : "仍在验证"}`,
      status: data.intelligence.modelImpact.adopted ? "connected" : "research",
      icon: Sigma,
      view: "data-intelligence" as const,
    },
    {
      key: "model",
      index: "04",
      label: "模型版本",
      value: `${championCount} 个正式版`,
      note: `${data.models.length} 个市场正在迭代`,
      status: modelConnected ? "connected" : "research",
      icon: BrainCircuit,
      view: "model-research" as const,
    },
    {
      key: "strategy",
      index: "05",
      label: "正式策略",
      value: `${activeStrategyCount} / ${data.strategy_model_usage.length} 已用模型`,
      note: strategyConnected ? "预测已改变部分候选评分" : "当前使用经典规则因子",
      status: strategyConnected ? "connected" : "blocked",
      icon: GitCompareArrows,
      view: "strategy" as const,
    },
  ] : [];

  const usageByKey = useMemo(
    () => new Map(
      (data?.strategy_model_usage ?? []).map(
        (item) => [`${item.market}:${item.agent}`, item],
      ),
    ),
    [data?.strategy_model_usage],
  );

  return (
    <section className="system-overview" role="region" aria-label="决策闭环总览">
      <header className="system-overview-header">
        <div>
          <span className="system-overview-mark"><RadioTower size={16} />LIVE RESEARCH SYSTEM</span>
          <h2>从数据到模拟订单，一条链路看完</h2>
          <p>每条连接都来自实际产物；研究结果没有被正式采用时，这里会直接标明。</p>
        </div>
        <div className={`system-connection-state ${strategyConnected ? "connected" : "blocked"}`}>
          {strategyConnected ? <CheckCircle2 size={17} /> : <ShieldAlert size={17} />}
          <span>{strategyConnected ? "模型已参与部分正式决策" : "正式策略当前完全由经典规则驱动"}</span>
        </div>
      </header>

      {error ? (
        <div className="system-overview-error" role="alert">
          <ShieldAlert size={18} />总览数据加载失败：{error}
        </div>
      ) : null}
      {data?.errors.length ? (
        <div className="system-overview-error" role="status">
          <ShieldAlert size={18} />
          <span>
            {data.errors.map((item) => item.message).join("；")}
          </span>
        </div>
      ) : null}
      {loading && !data ? (
        <div className="system-overview-loading" aria-label="决策总览加载中">
          <i /><i /><i /><i /><i />
        </div>
      ) : null}

      {data ? (
        <>
          <div className="decision-loop" aria-label="数据到策略的五段链路">
            {loop.map((item, index) => {
              const Icon = item.icon;
              return (
                <div className="decision-loop-step" key={item.key}>
                  <button
                    type="button"
                    className={`loop-node loop-${item.status}`}
                    onClick={() => onNavigate(
                      item.view === "strategy"
                        ? {
                            view: "strategy",
                            mode: "compare",
                            market: preferredMarket,
                          }
                        : {
                            view: item.view,
                            market: item.view === "data-intelligence"
                              ? "a_share"
                              : preferredMarket,
                          },
                    )}
                    aria-label={`查看${item.label}`}
                  >
                    <span>{item.index}</span>
                    <Icon size={18} aria-hidden="true" />
                    <strong>{item.label}</strong>
                    <b>{item.value}</b>
                    <small>{item.note}</small>
                    <i aria-hidden="true" />
                  </button>
                  {index < loop.length - 1 ? (
                    <ArrowRight className={`loop-link link-${loop[index + 1].status}`} size={17} aria-hidden="true" />
                  ) : null}
                </div>
              );
            })}
          </div>

          <section className="system-strategy-board" aria-labelledby="formal-strategy-title">
            <header>
              <div>
                <h3 id="formal-strategy-title">正式模拟策略</h3>
                <p>A 股与境内上市跨境 ETF 共用两种策略风格，账户和净值彼此独立。</p>
              </div>
              <button
                type="button"
                onClick={() => onNavigate({
                  view: "strategy",
                  mode: "compare",
                  market: preferredMarket,
                })}
              >
                全维度对比<ChevronRight size={15} />
              </button>
            </header>
            <div className="market-strategy-matrix">
              {data.markets.map((market) => (
                <article className="market-strategy-lane" key={market.market}>
                  <header>
                    <div>
                      <BarChart3 size={17} aria-hidden="true" />
                      <strong>{market.market === "cn_qdii_etf" ? "境内上市跨境ETF" : market.label}</strong>
                    </div>
                    <small>
                      {market.market === "cn_qdii_etf"
                        ? "人民币交易，追踪美国、香港等境外资产"
                        : "境内股票模拟账户"}
                    </small>
                  </header>
                  <div className="formal-strategy-list">
                    {market.agents.map((agent) => {
                      const usage = usageByKey.get(`${market.market}:${agent.agent}`);
                      const publicStrategyLabel = agent.strategy?.label
                        ?? (agent.agent === "claude"
                          ? "稳健防守"
                          : "趋势进攻");
                      return (
                        <button
                          type="button"
                          key={agent.agent}
                          className={`formal-strategy-row strategy-${agent.agent}`}
                          onClick={() => onNavigate({
                            view: "strategy",
                            mode: "detail",
                            market: publicMarket(market.market),
                            strategy: agent.agent === "claude"
                              ? "defensive"
                              : "trend",
                          })}
                          aria-label={`查看${publicStrategyLabel}明细`}
                        >
                          <span className="strategy-identity">
                            <Layers3 size={16} />
                            <span>
                              <strong>{publicStrategyLabel}</strong>
                              <small>{agent.strategy?.strategy_name ?? "策略明细"}</small>
                            </span>
                          </span>
                          <span className="strategy-nav">
                            <strong>{agent.nav.return_display}</strong>
                            <small>{agent.nav.latest_display}</small>
                          </span>
                          <span className={`strategy-model-state ${usage?.status === "active" ? "active" : "rule-only"}`}>
                            <strong>{usageLabel(usage)}</strong>
                            <small>{usageReason(usage)}</small>
                          </span>
                          <ChevronRight size={15} aria-hidden="true" />
                        </button>
                      );
                    })}
                  </div>
                </article>
              ))}
            </div>
          </section>

          <div className="system-research-grid">
            <section className="system-research-lane model-lane" aria-labelledby="model-research-title">
              <header>
                <span><FlaskConical size={16} />独立研究线</span>
                <h3 id="model-research-title">模型版本正在做什么</h3>
                <p>候选模型先在独立模拟组合中验证，晋级后才允许生成正式策略可读取的预测。</p>
              </header>
              <div className="model-market-list">
                {data.models.map((item) => {
                  const candidate = item.iteration?.candidate;
                  const champion = item.iteration?.champion;
                  return (
                    <article key={item.market}>
                      <div>
                        <strong>{item.market_label}</strong>
                        <small>{candidate?.display_version ?? item.iteration?.display_version ?? "等待候选版本"}</small>
                      </div>
                      <dl>
                        <div><dt>验证版本</dt><dd>{candidate?.status_label ?? "等待运行"}</dd></div>
                        <div><dt>正式版本</dt><dd>{champion?.display_version ?? "尚无 Champion"}</dd></div>
                      </dl>
                    </article>
                  );
                })}
              </div>
              <button
                type="button"
                className="system-drill-button"
                onClick={() => onNavigate({
                  view: "model-research",
                  market: preferredMarket,
                })}
                aria-label="查看模型迭代"
              >
                查看模型迭代<ChevronRight size={15} />
              </button>
            </section>

            <section className="system-research-lane intelligence-lane" aria-labelledby="intelligence-research-title">
              <header>
                <span><RadioTower size={16} />情报研究线</span>
                <h3 id="intelligence-research-title">情报怎样影响模型</h3>
                <p>LLM 只抽取带原文证据的事件；确定性校验通过后生成研究因子，再与无情报版本做增量比较。</p>
              </header>
              <div className="intelligence-effect-summary">
                <div>
                  <span>最近批次</span>
                  <strong>{data.intelligence.extraction.latestBatch?.model ?? "尚未运行"}</strong>
                  <small>
                    {data.intelligence.extraction.latestBatch
                      ? `${data.intelligence.extraction.latestBatch.succeeded + data.intelligence.extraction.latestBatch.noEvent} / ${data.intelligence.extraction.latestBatch.runs} 有效`
                      : "等待语义执行器"}
                  </small>
                </div>
                <div>
                  <span>可入模因子</span>
                  <strong>{eligibleFactors}</strong>
                  <small>{data.intelligence.factorSupply.suppliedFactors} 个因子已进入统计观察</small>
                </div>
              </div>
              <p className="intelligence-impact-verdict">{data.intelligence.modelImpact.reason}</p>
              <button
                type="button"
                className="system-drill-button"
                onClick={() => onNavigate({
                  view: "data-intelligence",
                  market: "a_share",
                })}
              >
                查看情报证据<ChevronRight size={15} />
              </button>
            </section>
          </div>
        </>
      ) : null}
    </section>
  );
}
