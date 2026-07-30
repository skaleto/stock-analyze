import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  BrainCircuit,
  Braces,
  Cpu,
  Database,
  ExternalLink,
  FileSearch,
  RadioTower,
  Sigma,
  X,
} from "lucide-react";
import type {
  IntelligenceDecision,
  IntelligenceDecisionRow,
  MarketIntelligence,
} from "./types";
import { useIntelligenceData } from "./useDashboardData";

const eventLabels: Record<string, string> = {
  earnings_forecast: "业绩预告",
  earnings_flash: "业绩快报",
  buyback: "股份回购",
  shareholder_change: "股东持股变动",
  dividend: "利润分配",
  major_contract: "重大合同",
  merger_restructuring: "并购重组",
  equity_financing: "股权融资",
  guarantee: "对外担保",
  pledge_freeze: "质押冻结",
  litigation_arbitration: "诉讼仲裁",
  investigation_penalty: "调查处罚",
  risk_warning_delisting: "风险警示或退市",
  capacity_project: "产能项目",
  control_change: "控制权变更",
};

const lifecycleLabels: Record<string, string> = {
  announced: "已公告",
  approved: "已批准",
  completed: "已完成",
  cancelled: "已取消",
  revised: "已修订",
  uncertain: "待确认",
};

const decisionLabels: Record<IntelligenceDecision, string> = {
  canonical: "已确认",
  no_event: "无事件",
  quarantined: "已隔离",
  failed: "失败",
};

const reasonLabels: Record<string, string> = {
  no_material_event: "公告不包含满足定义的重大事件",
  validation_failed: "语义结果未通过确定性校验",
  evidence_quote_mismatch: "证据引文无法与原文严格对齐",
  provider_schema_invalid: "模型输出未通过结构校验",
};

const sourceLabels: Record<string, string> = {
  tushare_anns_d: "Tushare 全量公告",
  tushare_announcement: "Tushare 全量公告",
  ifind: "iFinD 资讯",
  ifind_announcement: "iFinD 交叉核验",
  cninfo: "巨潮资讯",
  gov_policy: "国家政策",
  ndrc_policy: "发改委政策",
};

const freshnessLabels: Record<string, string> = {
  fresh: "新鲜",
  aging: "临近过期",
  stale: "已过期",
  unknown: "未知",
};

const runStatusLabels: Record<string, string> = {
  succeeded: "成功",
  success: "成功",
  running: "运行中",
  failed: "失败",
  failed_terminal: "失败",
  failed_retryable: "待重试",
  unavailable: "不可用",
  unknown: "未知",
};

const batchQualityLabels: Record<string, string> = {
  healthy: "健康",
  partial: "部分有效",
  degraded: "需关注",
  awaiting_executor: "等待执行器",
  idle: "无待处理",
};

const factorLabels: Record<string, string> = {
  event_net_strength_5d: "事件净强度（5日）",
  event_net_materiality_20d: "事件净重要性（20日）",
  event_relevance_20d: "事件相关性（20日）",
  event_certainty_20d: "事件确定性（20日）",
  event_revision_risk_20d: "事件修订风险（20日）",
  announcement_novelty_20d: "公告新颖度（20日）",
  event_source_confirmation: "多源确认度",
  event_data_coverage: "事件数据覆盖率",
};

const factorStateLabels: Record<string, string> = {
  observing: "观察中",
  research: "研究中",
  model_iteration: "模型迭代",
  active: "正式启用",
  unconfigured: "未配置",
};

const metricLabels: Record<string, string> = {
  macro_f1: "宏平均 F1",
  rank_ic: "Rank IC",
  log_loss: "Log Loss",
  brier_score: "Brier 分数",
  accuracy: "准确率",
};

type IntelligencePanelProps = {
  intelligence?: MarketIntelligence;
  eager?: boolean;
  refreshToken?: number;
  standalone?: boolean;
  mode?: "full" | "ledger";
};

function percent(value?: number | null): string {
  if (value == null) return "-";
  const scaled = value * 100;
  return `${scaled.toFixed(Number.isInteger(scaled) ? 0 : 1)}%`;
}

function integer(value?: number | null): string {
  return value == null ? "-" : Math.round(value).toLocaleString("zh-CN");
}

function shortTime(value?: string | null): string {
  if (!value) return "-";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return String(value).replace("T", " ").slice(0, 16);
  }
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(timestamp).map(({ type, value: part }) => [type, part]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

function directionLabel(value?: number | null): string {
  if (value == null) return "方向未判定";
  if (value > 0.05) return `上涨倾向 ${percent(Math.abs(value))}`;
  if (value < -0.05) return `下跌倾向 ${percent(Math.abs(value))}`;
  return "方向中性";
}

function rowLabel(row: IntelligenceDecisionRow): string {
  const subject = row.event_subject ? ` ${row.event_subject}` : "";
  return `${row.issuer_name || row.issuer_code || "未知主体"} ${eventLabels[row.event_type || ""] || decisionLabels[row.decision]}${subject}`;
}

function deltaLabel(value: number | null | undefined): string {
  if (value == null) return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(2)}%`;
}

export function IntelligencePanel({
  intelligence,
  eager = false,
  refreshToken = 0,
  standalone = false,
  mode = "full",
}: IntelligencePanelProps) {
  const showOverview = mode === "full";
  const panelRef = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(eager);
  const [filter, setFilter] = useState<IntelligenceDecision>("canonical");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const market = intelligence?.market ?? "";
  const agent = intelligence?.agent ?? "";
  const {
    summary,
    summaryError,
    summaryLoading,
    detail,
    detailError,
    detailLoading,
    documentDetail,
    loadDocument,
  } = useIntelligenceData(
    market,
    agent,
    visible && Boolean(market && agent),
    selectedId,
    refreshToken,
  );

  useEffect(() => {
    if (eager) {
      setVisible(true);
      return undefined;
    }
    const target = panelRef.current;
    if (!target) return undefined;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "160px" },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [eager]);

  const rows = useMemo(
    () => (summary?.rowsByDecision?.[filter] ?? summary?.rows ?? [])
      .filter((row) => row.decision === filter),
    [filter, summary?.rows, summary?.rowsByDecision],
  );

  const latestBatch = summary?.extraction.latestBatch;
  const totalTokens = (latestBatch?.inputTokens ?? 0) + (latestBatch?.outputTokens ?? 0);
  const flow = summary ? [
    {
      key: "pipeline",
      step: "01",
      label: "语料获取",
      value: integer(summary.pipeline.documents),
      detail: `PDF ${integer(summary.pipeline.stages.pdfReady)} · 解析 ${integer(summary.pipeline.stages.parsed)}`,
      icon: Database,
      status: summary.pipeline.status,
    },
    {
      key: "extraction",
      step: "02",
      label: "语义抽取",
      value: integer(
        (summary.extraction.semanticRuns.succeeded ?? 0)
        + (summary.extraction.semanticRuns.no_event ?? 0),
      ),
      detail: `事件 ${integer(summary.decisions.canonical)} · 隔离 ${integer(summary.decisions.quarantined)}`,
      icon: Braces,
      status: summary.extraction.status,
    },
    {
      key: "factor",
      step: "03",
      label: "因子供给",
      value: integer(summary.factorSupply.suppliedFactors),
      detail: `${summary.factorSupply.factorSet ?? "未形成因子集"} · ${summary.factorSupply.snapshotDate ?? "-"}`,
      icon: Sigma,
      status: summary.factorSupply.status,
    },
    {
      key: "impact",
      step: "04",
      label: "模型影响",
      value: summary.modelImpact.adopted ? "已入模" : "当前未入模",
      detail: `可评估周期 ${integer(summary.modelImpact.qualifiedHorizons)} · ${summary.modelImpact.activation}`,
      icon: BrainCircuit,
      status: summary.modelImpact.status,
    },
  ] : [];

  return (
    <section
      ref={panelRef}
      className={`intelligence-panel terminal-section${standalone ? " intelligence-workbench" : ""}`}
      aria-label="情报与模型影响"
    >
      {showOverview ? <header className="section-heading intelligence-heading">
        <div>
          <span className="section-kicker">
            <RadioTower size={14} aria-hidden="true" />
            INTELLIGENCE LINEAGE
          </span>
          <h2>情报链路总览</h2>
          <p>公告语料、结构化事件、研究因子和正式模型采用状态</p>
        </div>
        <div className="section-stat intelligence-adoption">
          <span>正式模型采用</span>
          <strong className={summary?.modelImpact.adopted ? "adopted" : "research-only"}>
            {summary?.modelImpact.adopted ? "已入模" : "当前未入模"}
          </strong>
        </div>
      </header> : null}

      {!intelligence ? (
        <p className="intelligence-empty">当前工作区未配置公告语义链路。</p>
      ) : summaryError ? (
        <p className="intelligence-error" role="alert">公告情报加载失败：{summaryError}</p>
      ) : summaryLoading || !summary ? (
        <div className="intelligence-skeleton" aria-label="公告情报加载中">
          <i /><i /><i />
        </div>
      ) : (
        <>
          {showOverview ? <div className="intelligence-flow" aria-label="语料到模型四层链路">
            {flow.map((item, index) => {
              const Icon = item.icon;
              return (
                <div className="intelligence-flow-segment" key={item.key}>
                  <article>
                    <span className="flow-step">{item.step}</span>
                    <span className="flow-icon"><Icon size={17} aria-hidden="true" /></span>
                    <div>
                      <h3>{item.label}</h3>
                      <strong>{item.value}</strong>
                      <small>{item.detail}</small>
                    </div>
                    <i className={`flow-status status-${item.status}`} aria-label={item.status} />
                  </article>
                  {index < flow.length - 1 ? <ArrowRight className="flow-arrow" size={15} aria-hidden="true" /> : null}
                </div>
              );
            })}
          </div> : null}

          {showOverview ? <section className="intelligence-band" aria-labelledby="source-freshness-title">
            <header>
              <div>
                <span>PIPELINE</span>
                <h3 id="source-freshness-title">数据源新鲜度</h3>
              </div>
              <small>待下载 {integer(summary.pipeline.backlog.download)} · 待解析 {integer(summary.pipeline.backlog.parse)} · 待抽取 {integer(summary.pipeline.backlog.semantic)}</small>
            </header>
            <div className="source-freshness-list">
              {summary.pipeline.sources.length ? summary.pipeline.sources.map((source) => (
                <article key={source.source}>
                  <div>
                    <strong>{sourceLabels[source.source] ?? source.source}</strong>
                    <small>{integer(source.documents)} 篇 · 游标 {source.cursor ?? "-"}</small>
                  </div>
                  <dl>
                    <div><dt>最近入库</dt><dd>{shortTime(source.lastIngestedAt)}</dd></div>
                    <div><dt>最新公告</dt><dd>{shortTime(source.latestPublishedAt)}</dd></div>
                    <div><dt>本批新增</dt><dd>{integer(source.inserted)} / {integer(source.fetched)}</dd></div>
                  </dl>
                  <span className={`freshness-state ${source.freshnessStatus}`}>
                    {freshnessLabels[source.freshnessStatus] ?? source.freshnessStatus}
                    <small>{runStatusLabels[source.latestRunStatus] ?? source.latestRunStatus}</small>
                  </span>
                </article>
              )) : <p className="intelligence-empty">尚无数据源运行记录。</p>}
            </div>
            {summary.pipeline.artifactWorkers?.status === "available" ? (
              <article className="artifact-worker-strip">
                <span className="artifact-worker-icon">
                  <Cpu size={17} aria-hidden="true" />
                </span>
                <div>
                  <strong>本机历史计算节点</strong>
                  <small>
                    {`已解析 ${integer(summary.pipeline.artifactWorkers.parsedDocuments)} 篇 · 已下载 ${integer(summary.pipeline.artifactWorkers.downloadedDocuments)} 篇`}
                    {summary.pipeline.artifactWorkers.latestFinishedAt
                      ? ` · 最近完成 ${shortTime(summary.pipeline.artifactWorkers.latestFinishedAt)}`
                      : ""}
                  </small>
                </div>
                <dl>
                  <div>
                    <dt>当前</dt>
                    <dd>
                      {`运行中 ${integer(summary.pipeline.artifactWorkers.activeLeases)} 批 · ${integer(summary.pipeline.artifactWorkers.leasedDocuments)} 篇`}
                    </dd>
                  </div>
                  <div>
                    <dt>下载批次</dt>
                    <dd>{integer(summary.pipeline.artifactWorkers.stages.download.imported)} 完成 · {integer(summary.pipeline.artifactWorkers.stages.download.partial)} 部分</dd>
                  </div>
                  <div>
                    <dt>解析批次</dt>
                    <dd>{integer(summary.pipeline.artifactWorkers.stages.parse.imported)} 完成 · {integer(summary.pipeline.artifactWorkers.stages.parse.partial)} 部分</dd>
                  </div>
                </dl>
              </article>
            ) : null}
          </section> : null}

          {showOverview ? <section className="intelligence-band semantic-batch" aria-labelledby="latest-batch-title">
            <header>
              <div>
                <span>EXTRACTION</span>
                <h3 id="latest-batch-title">最新语义批次</h3>
              </div>
              <small>
                {latestBatch?.finishedAt
                  ? `完成于 ${shortTime(latestBatch.finishedAt)} · ${batchQualityLabels[latestBatch.qualityStatus] ?? latestBatch.qualityStatus}`
                  : "尚无批次"}
              </small>
            </header>
            {latestBatch ? (
              <>
                <div className="semantic-batch-identities">
                  <div>
                    <span>本批执行模型</span>
                    <strong>{latestBatch.model}</strong>
                    <small>{latestBatch.provider} · {latestBatch.promptVersion}</small>
                  </div>
                  <div>
                    <span>统一抽取契约</span>
                    <strong>{latestBatch.profileId}</strong>
                    <small>
                      {latestBatch.schemaVersion} · {latestBatch.taxonomyVersion}
                    </small>
                  </div>
                </div>
                <div className="semantic-batch-metrics">
                  <div><span>总 Token</span><strong>{integer(totalTokens)}</strong><small>输入 {integer(latestBatch.inputTokens)} · 输出 {integer(latestBatch.outputTokens)}</small></div>
                  <div><span>成功</span><strong>{integer(latestBatch.succeeded + latestBatch.noEvent)} / {integer(latestBatch.runs)}</strong><small>含无事件 {integer(latestBatch.noEvent)} · 请求 {integer(latestBatch.requestCount)}</small></div>
                  <div><span>隔离</span><strong>{integer(latestBatch.quarantined)}</strong><small>等待复核或重抽</small></div>
                  <div><span>失败</span><strong>{integer(latestBatch.failed)}</strong><small>校验纠正 {integer(latestBatch.validationRepairs)} · 未修复 {integer(latestBatch.validationRepairFailures)} · 剩余 {integer(latestBatch.remaining)}</small></div>
                </div>
              </>
            ) : <p className="intelligence-empty">尚无语义抽取批次。</p>}
          </section> : null}

          {showOverview ? <div className="factor-impact-grid">
            <section className="intelligence-band factor-supply" aria-labelledby="factor-supply-title">
              <header>
                <div>
                  <span>FACTOR SUPPLY</span>
                  <h3 id="factor-supply-title">因子供给</h3>
                </div>
                <small>{summary.factorSupply.factorSet ?? "未形成因子集"}</small>
              </header>
              <div className="factor-supply-summary">
                <div><span>验证样本</span><strong>{integer(summary.factorSupply.rows)}</strong></div>
                <div><span>已计算因子</span><strong>{integer(summary.factorSupply.suppliedFactors)}</strong></div>
                <div><span>可入模因子</span><strong>{integer(summary.factorSupply.modelEligibleFactors.length)}</strong></div>
              </div>
              <div className="factor-list">
                {summary.factorSupply.factors.length ? summary.factorSupply.factors.slice(0, 8).map((factor) => (
                  <article key={factor.name}>
                    <div>
                      <strong>{factorLabels[factor.name] ?? factor.name}</strong>
                      <small>{factorStateLabels[factor.state] ?? factor.state}</small>
                    </div>
                    <dl>
                      <div><dt>覆盖率</dt><dd>{percent(factor.coverage)}</dd></div>
                      <div><dt>激活率</dt><dd>{percent(factor.activationRate)}</dd></div>
                      <div><dt>Rank IC</dt><dd>{factor.meanRankIc == null ? "-" : factor.meanRankIc.toFixed(3)}</dd></div>
                    </dl>
                  </article>
                )) : <p className="intelligence-empty">尚无因子验证结果。</p>}
              </div>
            </section>

            <section className="intelligence-band model-impact" aria-labelledby="model-impact-title">
              <header>
                <div>
                  <span>MODEL IMPACT</span>
                  <h3 id="model-impact-title">模型增量影响</h3>
                </div>
                <span className={`model-adoption-state ${summary.modelImpact.adopted ? "adopted" : "research-only"}`}>
                  {summary.modelImpact.adopted ? "已入模" : "当前未入模"}
                </span>
              </header>
              <p className="model-impact-reason">{summary.modelImpact.reason}</p>
              <div className="impact-horizons">
                {summary.modelImpact.horizons.length ? summary.modelImpact.horizons.map((horizon) => {
                  const deltas = Object.entries(horizon.deltas);
                  return (
                    <article key={horizon.horizon}>
                      <header><strong>{horizon.horizon} 日周期</strong><span>{horizon.status === "complete" ? "可评估" : "样本不足"}</span></header>
                      <dl>
                        <div><dt>样本</dt><dd>{integer(horizon.support.rows)}</dd></div>
                        <div><dt>覆盖率</dt><dd>{percent(horizon.support.covered_ratio)}</dd></div>
                        <div><dt>激活率</dt><dd>{percent(horizon.support.active_ratio)}</dd></div>
                      </dl>
                      {deltas.length ? (
                        <div className="impact-deltas">
                          {deltas.slice(0, 3).map(([metric, value]) => (
                            <span key={metric}>
                              {metricLabels[metric] ?? metric}
                              <strong className={(value ?? 0) >= 0 ? "positive" : "negative"}>
                                {deltaLabel(value)}
                              </strong>
                            </span>
                          ))}
                        </div>
                      ) : <small>{horizon.reason || "等待形成增量指标"}</small>}
                    </article>
                  );
                }) : <p className="intelligence-empty">尚无可评估的模型增量周期。</p>}
              </div>
            </section>
          </div> : null}

          <section className="intelligence-band decision-ledger" aria-labelledby="decision-ledger-title">
            <header>
              <div>
                <span>DECISION LEDGER</span>
                <h3 id="decision-ledger-title">语义决策明细</h3>
              </div>
              <small>每类最多展示 30 条</small>
            </header>
            <div className="intelligence-filters" role="group" aria-label="决策状态筛选">
              {(Object.keys(decisionLabels) as IntelligenceDecision[]).map((decision) => (
                <button
                  className={filter === decision ? "active" : ""}
                  key={decision}
                  type="button"
                  onClick={() => setFilter(decision)}
                >
                  {decisionLabels[decision]} {summary.decisions[decision] ?? 0}
                </button>
              ))}
            </div>
            <div className="intelligence-table-wrap">
              {rows.length === 0 ? (
                <p className="intelligence-empty">此状态暂无决策记录。</p>
              ) : (
                <table className="intelligence-table">
                  <thead>
                    <tr><th>主体与事件</th><th>决策</th><th>方向</th><th>置信度</th><th>生效时间</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.decision_id}>
                        <td>
                          <button type="button" onClick={() => setSelectedId(row.decision_id)} aria-label={`查看 ${rowLabel(row)}`}>
                            <strong>{row.issuer_name || "未知主体"}</strong>
                            <small>{row.issuer_code || "-"}　{eventLabels[row.event_type || ""] || decisionLabels[row.decision]}</small>
                            {row.event_subject ? <small>{row.event_subject}</small> : null}
                            {row.reason ? <small>{reasonLabels[row.reason] || row.reason}</small> : null}
                          </button>
                        </td>
                        <td><span className={`decision-state ${row.decision}`}>{decisionLabels[row.decision]}</span></td>
                        <td>{directionLabel(row.direction)}</td>
                        <td>{percent(row.confidence)}</td>
                        <td><time>{shortTime(row.effective_at)}</time></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>
        </>
      )}

      {selectedId ? (
        <div className="intelligence-drawer-backdrop" onMouseDown={() => setSelectedId(null)}>
          <aside
            className="intelligence-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="决策详情"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <div><span>语义决策</span><h3>{detail ? eventLabels[detail.event.event_type || ""] || decisionLabels[detail.decision] : "加载中"}</h3></div>
              <button type="button" onClick={() => setSelectedId(null)} aria-label="关闭决策详情"><X size={18} /></button>
            </header>
            {detailError ? <p className="intelligence-error" role="alert">{detailError}</p> : null}
            {detailLoading || !detail ? (
              <div className="drawer-loading"><i /><i /><i /></div>
            ) : (
              <div className="intelligence-drawer-body">
                <section>
                  <h4>决策结论</h4>
                  <dl className="decision-definition">
                    <div><dt>状态</dt><dd>{decisionLabels[detail.decision]}</dd></div>
                    <div><dt>生命周期</dt><dd>{lifecycleLabels[detail.event.lifecycle || ""] || detail.event.lifecycle || "-"}</dd></div>
                    <div><dt>主体</dt><dd>{detail.issuer.name || "-"} <code>{detail.issuer.code || "-"}</code></dd></div>
                    <div><dt>方向</dt><dd>{directionLabel(detail.scores.direction)}</dd></div>
                    <div><dt>重要性</dt><dd>{percent(detail.scores.materiality)}</dd></div>
                    <div><dt>相关性</dt><dd>{percent(detail.scores.relevance)}</dd></div>
                    <div><dt>新颖度</dt><dd>{percent(detail.scores.novelty)}</dd></div>
                    <div><dt>置信度</dt><dd>{percent(detail.scores.confidence)}</dd></div>
                  </dl>
                  {detail.reason ? <p className="decision-reason">{reasonLabels[detail.reason] || detail.reason}</p> : null}
                </section>

                <section>
                  <h4>证据</h4>
                  {detail.evidence.length ? detail.evidence.map((item) => (
                    <blockquote key={item.evidence_id}>
                      <span>第 {item.page_number} 页</span>
                      <p>{item.quote}</p>
                    </blockquote>
                  )) : <p className="intelligence-empty">此决策没有可展示的证据引文。</p>}
                  <a href={detail.document.source_url} target="_blank" rel="noreferrer">
                    <ExternalLink size={14} />查看原始 PDF
                  </a>
                </section>

                <section>
                  <h4>版本追溯</h4>
                  <dl className="version-definition">
                    <div><dt>模型</dt><dd>{detail.versions.model}</dd></div>
                    <div><dt>Prompt</dt><dd>{detail.versions.prompt_version}</dd></div>
                    <div><dt>Schema</dt><dd>{detail.versions.schema_version}</dd></div>
                    <div><dt>Taxonomy</dt><dd>{detail.versions.taxonomy_version}</dd></div>
                  </dl>
                  <button className="document-lineage-button" type="button" onClick={() => void loadDocument(detail.document.document_id)}>
                    <FileSearch size={14} />查看文档处理记录
                  </button>
                  {documentDetail ? <p className="document-lineage-summary">处理产物 {documentDetail.artifacts.length} 个，关联决策 {documentDetail.decisions.length} 条</p> : null}
                </section>
              </div>
            )}
          </aside>
        </div>
      ) : null}
    </section>
  );
}
