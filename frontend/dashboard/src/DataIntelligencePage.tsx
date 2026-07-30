import { useCallback, useEffect, useState } from "react";
import { fetchDataIntelligence } from "./api";
import { IntelligencePanel } from "./IntelligencePanel";
import { StageFlow } from "./StageFlow";
import { BoundedTable, DetailPanel } from "./WorkspacePrimitives";
import { useWorkspaceResource } from "./useWorkspaceResource";
import type {
  DataIntelligenceData,
  UsageEvidenceCell,
  WorkspaceStage,
} from "./workspaceTypes";

type SupplyLane = "structured" | "intelligence";

function usageLabel(status: string): string {
  return {
    used: "已使用",
    not_used: "未使用",
    observing: "观察中",
    unavailable: "状态不可用",
  }[status] ?? status;
}

function usageCellLabel(cell: UsageEvidenceCell): string {
  const namespaceCounts = (
    cell.formalCount > 0 || cell.researchCount > 0
      ? ` · 正式 ${cell.formalCount} · 研究 ${cell.researchCount}`
      : ` · ${cell.count}`
  );
  const lineage = cell.lineageStatus ? ` · ${cell.lineageStatus}` : "";
  const manifest = cell.missingManifest ? " · 清单缺失" : "";
  return `${usageLabel(cell.status)}${namespaceCounts}${lineage}${manifest}`;
}

function usageEvidence(cell: UsageEvidenceCell): string {
  const formal = cell.evidenceByNamespace.formal.length
    ? `正式策略证据：${cell.evidenceByNamespace.formal.join("、")}`
    : "正式策略证据：无";
  const research = cell.evidenceByNamespace.research.length
    ? `研究模型证据：${cell.evidenceByNamespace.research.join("、")}`
    : "研究模型证据：无";
  return `${formal}；${research}`;
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return "未记录";
  return /^\d{8}$/.test(value)
    ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
    : value;
}

export function DataIntelligencePage({
  market,
  refreshToken,
}: {
  market: string;
  refreshToken: number;
}) {
  const loader = useCallback(
    (signal: AbortSignal) => fetchDataIntelligence(market, signal),
    [market],
  );
  const resource = useWorkspaceResource<DataIntelligenceData>(
    market,
    true,
    loader,
  );
  const [selected, setSelected] = useState("structured:sources");

  useEffect(() => {
    setSelected("structured:sources");
  }, [market]);

  useEffect(() => {
    if (refreshToken > 0) resource.refresh();
  }, [refreshToken, resource.refresh]);

  if (resource.loading && !resource.data) {
    return (
      <div className="skeleton-grid" aria-label="数据与情报加载中">
        <div /><div /><div /><div />
      </div>
    );
  }

  if (!resource.data) {
    return (
      <div className="error-banner" role="alert">
        数据与情报不可用：{resource.error ?? "unknown"}
      </div>
    );
  }

  const data = resource.data;
  const separator = selected.indexOf(":");
  const lane = selected.slice(0, separator) as SupplyLane;
  const key = selected.slice(separator + 1);
  const stages = lane === "structured"
    ? data.structured.stages
    : data.intelligence.stages;
  const stage = stages.find((item) => item.key === key) ?? stages[0];
  if (!stage) {
    return (
      <div className="error-banner" role="alert">
        数据与情报未提供可展示的阶段
      </div>
    );
  }

  const select = (nextLane: SupplyLane) => (nextKey: string) => {
    setSelected(`${nextLane}:${nextKey}`);
  };

  return (
    <section
      className="workspace-page data-intelligence-page"
      aria-label="数据与情报"
    >
      {resource.stale ? (
        <div className="stale-banner" role="status">
          刷新失败，显示 {data.generated_at.replace("T", " ")} 的最后成功快照
          {resource.error ? `：${resource.error}` : ""}
        </div>
      ) : null}
      {data.truncated ? (
        <div className="stale-banner" role="status">
          响应内容已截断
          {data.truncationReason ? `：${data.truncationReason}` : ""}
        </div>
      ) : null}

      <div className="supply-lanes">
        <section aria-labelledby="structured-lane-title">
          <h2 id="structured-lane-title">结构化数据</h2>
          <StageFlow
            stages={data.structured.stages}
            selectedKey={lane === "structured" ? key : ""}
            ariaLabel="结构化数据供给链"
            onSelect={select("structured")}
          />
        </section>
        <section aria-labelledby="intelligence-lane-title">
          <h2 id="intelligence-lane-title">文本情报</h2>
          <StageFlow
            stages={data.intelligence.stages}
            selectedKey={lane === "intelligence" ? key : ""}
            ariaLabel="文本情报供给链"
            onSelect={select("intelligence")}
          />
        </section>
      </div>

      <SupplyDetail
        data={data}
        lane={lane}
        stage={stage}
        market={market}
        refreshToken={refreshToken}
      />

      <section className="usage-matrix-section">
        <header className="section-heading">
          <div><h2>实际使用去向</h2></div>
        </header>
        <div className="bounded-table-wrap">
          <table className="bounded-table" aria-label="实际使用去向">
            <thead>
              <tr>
                <th>使用对象</th>
                <th>结构化数据</th>
                <th>传统因子</th>
                <th>情报因子</th>
                <th>当前影响</th>
              </tr>
            </thead>
            <tbody>
              {data.usageMatrix.map((row) => (
                <tr key={row.consumerKey}>
                  <td><strong>{row.consumerLabel}</strong></td>
                  <td title={usageEvidence(row.structuredData)}>
                    {usageCellLabel(row.structuredData)}
                  </td>
                  <td title={usageEvidence(row.traditionalFactors)}>
                    {usageCellLabel(row.traditionalFactors)}
                  </td>
                  <td title={usageEvidence(row.intelligenceFactors)}>
                    {usageCellLabel(row.intelligenceFactors)}
                  </td>
                  <td>
                    {row.impact}
                    {row.modelAdoption?.missingManifestCount
                      ? ` · ${row.modelAdoption.missingManifestCount} 个模型清单缺失`
                      : ""}
                    {row.lineageStatus ? ` · ${row.lineageStatus}` : ""}
                    {row.missingManifest ? " · 模型清单缺失" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

function SupplyDetail({
  data,
  lane,
  stage,
  market,
  refreshToken,
}: {
  data: DataIntelligenceData;
  lane: SupplyLane;
  stage: WorkspaceStage;
  market: string;
  refreshToken: number;
}) {
  if (lane === "intelligence" && stage.key === "semantic") {
    return (
      <IntelligencePanel
        intelligence={{ market, agent: "model_shadow" }}
        eager
        refreshToken={refreshToken}
        mode="ledger"
      />
    );
  }

  return (
    <DetailPanel
      title={stage.label}
      status={stage.status}
      updatedAt={data.generated_at}
    >
      {lane === "structured" && stage.key === "sources" ? (
        <div className="detail-stack">
          <dl className="workspace-metric-grid">
            <div>
              <dt>数据覆盖区间</dt>
              <dd>
                {dateLabel(data.structured.coverage.rangeStart)}
                {" 至 "}
                {dateLabel(data.structured.coverage.rangeEnd)}
              </dd>
            </div>
            <div>
              <dt>最近交易日</dt>
              <dd>{dateLabel(data.structured.coverage.latestTradeDate)}</dd>
            </div>
            <div>
              <dt>快照日期</dt>
              <dd>{dateLabel(data.structured.coverage.snapshotAsOf)}</dd>
            </div>
            <div>
              <dt>最新快照</dt>
              <dd>{data.structured.coverage.latestSnapshot ?? "未记录"}</dd>
            </div>
          </dl>
          <BoundedTable
            rows={data.structured.sources}
            rowKey={(row) => row.source}
            emptyLabel="尚无结构化数据源记录"
            columns={[
              { key: "source", label: "数据源", render: (row) => row.source },
              { key: "status", label: "状态", render: (row) => row.status },
              {
                key: "research",
                label: "研究模型特征",
                render: (row) => (
                  `${row.selectedModelFeatureCount} / ${row.researchFeatureCount}`
                ),
              },
              {
                key: "strategy",
                label: "正式策略因子",
                render: (row) => (
                  `${row.activeStrategyFactorCount} / ${row.strategyFactorCount}`
                ),
              },
              {
                key: "usedBy",
                label: "使用位置",
                render: (row) => row.useLocations.join("、") || "尚未使用",
              },
            ]}
          />
        </div>
      ) : null}

      {lane === "structured" && stage.key === "quality" ? (
        <dl className="workspace-metric-grid">
          <div><dt>模型清单</dt><dd>{data.structured.quality.modelCount}</dd></div>
          <div>
            <dt>点时审计通过</dt>
            <dd>{data.structured.quality.pointInTimeAuditedModels}</dd>
          </div>
          <div>
            <dt>点时审计未通过</dt>
            <dd>{data.structured.quality.pointInTimeFailedModels}</dd>
          </div>
          <div>
            <dt>缺失率产物</dt>
            <dd>{data.structured.quality.missingRateStatus}</dd>
          </div>
          <div>
            <dt>异常值产物</dt>
            <dd>{data.structured.quality.outlierStatus}</dd>
          </div>
        </dl>
      ) : null}

      {lane === "structured" && stage.key === "traditional" ? (
        <div className="detail-stack">
          <dl className="workspace-metric-grid">
            <div>
              <dt>正式策略因子</dt>
              <dd>
                {data.structured.formalFactorNamespace.activeFactorCount}
                {" / "}
                {data.structured.formalFactorNamespace.definedFactorCount}
              </dd>
            </div>
            <div>
              <dt>研究模型特征</dt>
              <dd>
                {data.structured.researchFeatureNamespace.selectedFeatures.length}
                {" / "}
                {data.structured.researchFeatureNamespace.definedFeatureCount}
              </dd>
            </div>
          </dl>
          <BoundedTable
            rows={data.structured.factorGroups}
            rowKey={(row) => row.family}
            emptyLabel="尚无研究特征分组记录"
            columns={[
              { key: "family", label: "研究特征组", render: (row) => row.family },
              {
                key: "defined",
                label: "已定义",
                render: (row) => String(row.definedFeatureCount),
              },
              {
                key: "selected",
                label: "已选入研究模型",
                render: (row) => String(row.selectedFeatureCount),
              },
            ]}
          />
        </div>
      ) : null}

      {lane === "intelligence" && stage.key === "documents" ? (
        <BoundedTable
          rows={data.intelligence.pipeline.sources}
          rowKey={(row) => row.source}
          emptyLabel="尚无情报来源运行记录"
          columns={[
            { key: "source", label: "来源", render: (row) => row.source },
            {
              key: "freshness",
              label: "每日增量新鲜度",
              render: (row) => row.freshnessStatus,
            },
            {
              key: "published",
              label: "最新发布日期",
              render: (row) => row.latestPublishedAt ?? "-",
            },
            {
              key: "ingested",
              label: "最近拉取",
              render: (row) => row.lastIngestedAt ?? "-",
            },
            {
              key: "cursor",
              label: "增量游标",
              render: (row) => row.cursor ?? "-",
            },
          ]}
        />
      ) : null}

      {lane === "intelligence" && stage.key === "artifacts" ? (
        <dl className="workspace-metric-grid">
          <div>
            <dt>公告目录</dt>
            <dd>{data.intelligence.pipeline.documents}</dd>
          </div>
          <div>
            <dt>PDF 就绪</dt>
            <dd>{data.intelligence.pipeline.stages.pdfReady}</dd>
          </div>
          <div>
            <dt>已解析</dt>
            <dd>{data.intelligence.pipeline.stages.parsed}</dd>
          </div>
          <div>
            <dt>下载回填积压</dt>
            <dd>{data.intelligence.pipeline.backlog.download}</dd>
          </div>
          <div>
            <dt>解析回填积压</dt>
            <dd>{data.intelligence.pipeline.backlog.parse}</dd>
          </div>
          <div>
            <dt>Worker 状态</dt>
            <dd>{data.intelligence.pipeline.artifactWorkers.status}</dd>
          </div>
        </dl>
      ) : null}

      {lane === "intelligence" && stage.key === "intelligence_factors" ? (
        <div className="detail-stack">
          <dl className="workspace-metric-grid">
            <div>
              <dt>情报特征定义</dt>
              <dd>{data.intelligence.featureNamespace.definedFeatureCount}</dd>
            </div>
            <div>
              <dt>已选入研究模型</dt>
              <dd>{data.intelligence.featureNamespace.selectedFeatureCount}</dd>
            </div>
          </dl>
          <BoundedTable
            rows={data.intelligence.factorSupply.factors}
            rowKey={(row) => row.name}
            emptyLabel="尚无情报因子验证记录"
            columns={[
              { key: "name", label: "因子", render: (row) => row.name },
              { key: "state", label: "生命周期", render: (row) => row.state },
              {
                key: "coverage",
                label: "覆盖率",
                render: (row) => row.coverage == null
                  ? "-"
                  : `${(row.coverage * 100).toFixed(1)}%`,
              },
              {
                key: "activation",
                label: "激活率",
                render: (row) => row.activationRate == null
                  ? "-"
                  : `${(row.activationRate * 100).toFixed(1)}%`,
              },
              {
                key: "ic",
                label: "平均 Rank IC",
                render: (row) => row.meanRankIc == null
                  ? "-"
                  : row.meanRankIc.toFixed(4),
              },
              {
                key: "recommendation",
                label: "采用建议",
                render: (row) => row.recommendation ?? "-",
              },
            ]}
          />
        </div>
      ) : null}
    </DetailPanel>
  );
}
