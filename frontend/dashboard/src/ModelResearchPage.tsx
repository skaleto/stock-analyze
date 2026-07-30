import { useCallback, useEffect, useState } from "react";
import { fetchModelResearch } from "./api";
import { StageFlow } from "./StageFlow";
import {
  BoundedTable,
  DetailPanel,
} from "./WorkspacePrimitives";
import { useWorkspaceResource } from "./useWorkspaceResource";
import type {
  ModelResearchData,
  ModelResearchModel,
} from "./workspaceTypes";

function value(input: unknown): string {
  if (input == null || input === "") return "-";
  if (typeof input === "number") return input.toFixed(4);
  return String(input);
}

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    rank_ic_below_floor: "Rank IC 未达到门槛",
    icir_below_floor: "ICIR 稳定性未达到门槛",
    brier_above_ceiling: "概率校准误差超过上限",
    probability_gate_not_met: "上涨概率未达到入选门槛",
  };
  return labels[reason] ? `${labels[reason]}（${reason}）` : reason;
}

function ModelTable({
  models,
  validation,
}: {
  models: ModelResearchModel[];
  validation: boolean;
}) {
  return (
    <BoundedTable
      rows={models}
      rowKey={(row) => `${row.horizon}:${row.modelVersion}`}
      emptyLabel="尚无研究模型产物"
      columns={[
        { key: "version", label: "版本", render: (row) => row.modelVersion || "-" },
        { key: "horizon", label: "周期", render: (row) => `${row.horizon} 日` },
        { key: "algorithm", label: "算法族", render: (row) => row.algorithmFamily },
        { key: "samples", label: "样本", render: (row) => String(row.sampleSupport) },
        ...(!validation
          ? [
              {
                key: "trainedAt",
                label: "训练时间",
                render: (row: ModelResearchModel) => row.trainedAt ?? "-",
              },
              {
                key: "artifactStatus",
                label: "产物状态",
                render: (row: ModelResearchModel) => row.artifactStatus,
              },
              {
                key: "artifactRef",
                label: "产物引用",
                render: (row: ModelResearchModel) => row.artifactRef ?? "-",
              },
            ]
          : []),
        {
          key: "rank_ic",
          label: "Rank IC",
          render: (row) => value(
            row.metrics.rank_ic ?? row.metrics.mean_rank_ic,
          ),
        },
        { key: "icir", label: "ICIR", render: (row) => value(row.metrics.icir) },
        {
          key: "brier",
          label: "Brier",
          render: (row) => value(row.metrics.brier_score),
        },
        { key: "auc", label: "AUC", render: (row) => value(row.metrics.auc) },
        {
          key: "lift",
          label: "命中率提升",
          render: (row) => value(row.metrics.hit_rate_lift),
        },
        {
          key: "net",
          label: "扣费超额收益",
          render: (row) => value(row.metrics.net_excess_return),
        },
        {
          key: "turnover",
          label: "换手",
          render: (row) => value(row.metrics.turnover),
        },
        ...(validation
          ? [{
              key: "result",
              label: "验收结果",
              render: (row: ModelResearchModel) => row.gatePassed
                ? "通过"
                : row.gateReasons.map(reasonLabel).join("；") || "未通过",
            }]
          : []),
      ]}
    />
  );
}

export function ModelResearchPage({
  market,
  refreshToken,
}: {
  market: string;
  refreshToken: number;
}) {
  const loader = useCallback(
    (signal: AbortSignal) => fetchModelResearch(market, signal),
    [market],
  );
  const resource = useWorkspaceResource<ModelResearchData>(
    market,
    true,
    loader,
  );
  const [selected, setSelected] = useState("data");

  useEffect(() => {
    setSelected("data");
  }, [market]);

  useEffect(() => {
    if (refreshToken > 0) resource.refresh();
  }, [refreshToken, resource.refresh]);

  if (resource.loading && !resource.data) {
    return (
      <div className="skeleton-grid" aria-label="模型研究加载中">
        <div /><div /><div /><div /><div />
      </div>
    );
  }

  if (!resource.data) {
    return (
      <div className="error-banner" role="alert">
        模型研究数据不可用：{resource.error ?? "unknown"}
      </div>
    );
  }

  const data = resource.data;
  const stage = data.stages.find((item) => item.key === selected)
    ?? data.stages[0];
  if (!stage) {
    return (
      <div
        className="error-banner"
        role="alert"
        aria-label="模型研究阶段不可用"
      >
        模型研究数据未提供阶段信息
      </div>
    );
  }

  return (
    <section
      className="workspace-page model-research-page"
      aria-label="模型研究"
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
      <StageFlow
        stages={data.stages}
        selectedKey={stage.key}
        ariaLabel="模型研究进度"
        onSelect={setSelected}
      />
      <DetailPanel
        title={stage.label}
        status={stage.status}
        updatedAt={data.generated_at}
      >
        {stage.key === "data" ? (
          <div className="detail-stack">
            <dl className="workspace-metric-grid">
              <div>
                <dt>候选特征</dt>
                <dd>{data.dataPreparation.candidateFeatureCount}</dd>
              </div>
              <div>
                <dt>已选特征</dt>
                <dd>{data.dataPreparation.selectedFeatureCount}</dd>
              </div>
              <div>
                <dt>结构化特征</dt>
                <dd>{data.dataPreparation.structuredFeatureCount}</dd>
              </div>
              <div>
                <dt>情报特征</dt>
                <dd>{data.dataPreparation.intelligenceFeatureCount}</dd>
              </div>
              <div>
                <dt>未分类特征</dt>
                <dd>
                  {data.dataPreparation.unclassifiedFeatureCount ?? 0} 个未分类
                </dd>
              </div>
              <div>
                <dt>点时审计</dt>
                <dd>{data.dataPreparation.pointInTimeAudit}</dd>
              </div>
            </dl>
            {(data.dataPreparation.unclassifiedFeatures ?? []).length ? (
              <div>
                <span>未分类：</span>
                {(data.dataPreparation.unclassifiedFeatures ?? []).map(
                  (feature) => <span key={feature}>{feature}</span>,
                )}
              </div>
            ) : null}
            <BoundedTable
              rows={data.dataPreparation.sources}
              rowKey={(row) => [
                row.source,
                row.status,
                row.rows ?? "",
                row.error ?? "",
              ].join(":")}
              emptyLabel="尚无数据源健康记录"
              columns={[
                { key: "source", label: "来源", render: (row) => row.source },
                { key: "status", label: "状态", render: (row) => row.status },
                { key: "rows", label: "记录数", render: (row) => value(row.rows) },
                {
                  key: "error",
                  label: "缺口",
                  render: (row) => row.error ?? "-",
                },
              ]}
            />
          </div>
        ) : null}
        {stage.key === "training" ? (
          <ModelTable models={data.training.models} validation={false} />
        ) : null}
        {stage.key === "validation" ? (
          <ModelTable models={data.validation.models} validation />
        ) : null}
        {stage.key === "simulation" ? (
          <dl className="workspace-metric-grid">
            <div>
              <dt>候选版本</dt>
              <dd>
                {String(
                  data.simulation.candidate?.display_version ?? "等待候选",
                )}
              </dd>
            </div>
            <div>
              <dt>预测产物</dt>
              <dd>
                {data.simulation.predictionStatus} ·{" "}
                {data.simulation.predictionAsOf ?? "-"}
              </dd>
            </div>
            <div>
              <dt>观察周期</dt>
              <dd>
                {data.simulation.cyclesCompleted} /{" "}
                {data.simulation.cyclesRequired}
              </dd>
            </div>
            <div>
              <dt>独立账户</dt>
              <dd>{data.simulation.account?.accountLabel || "-"}</dd>
            </div>
            <div>
              <dt>账户 ID</dt>
              <dd>{data.simulation.account?.accountId || "-"}</dd>
            </div>
            <div>
              <dt>隔离状态</dt>
              <dd>{data.simulation.account?.isolation || "-"}</dd>
            </div>
            <div>
              <dt>执行结果</dt>
              <dd>成交 {data.simulation.decision.tradesExecuted} 笔</dd>
            </div>
            <div>
              <dt>挂单状态</dt>
              <dd>待执行 {data.simulation.decision.pendingOrders} 笔</dd>
            </div>
            <div>
              <dt>候选证券</dt>
              <dd>{data.simulation.decision.candidateRows}</dd>
            </div>
            <div>
              <dt>通过门槛</dt>
              <dd>{data.simulation.decision.eligibleRows}</dd>
            </div>
            <div>
              <dt>本期决策</dt>
              <dd>{data.simulation.decision.selectedCount} 个入选</dd>
            </div>
            <div>
              <dt>保持现金原因</dt>
              <dd>
                {data.simulation.decision.cashReason
                  ? reasonLabel(data.simulation.decision.cashReason)
                  : "-"}
              </dd>
            </div>
          </dl>
        ) : null}
        {stage.key === "adoption" ? (
          <div className="detail-stack">
            <strong>
              {data.adoption.champions.length
                ? `${data.adoption.champions.length} 个 Champion`
                : "正式策略仍由规则驱动"}
            </strong>
            <span>
              可回滚版本：
              {data.adoption.rollbackCandidates
                .map((row) => row.displayVersion)
                .join("、") || "暂无"}
            </span>
            <BoundedTable
              rows={data.adoption.champions}
              rowKey={(row) => `${row.horizon}:${row.modelVersion}`}
              emptyLabel="尚无 Champion 模型"
              columns={[
                {
                  key: "version",
                  label: "Champion 版本",
                  render: (row) => row.modelVersion,
                },
                {
                  key: "horizon",
                  label: "周期",
                  render: (row) => `${row.horizon} 日`,
                },
                {
                  key: "activated",
                  label: "激活时间",
                  render: (row) => row.activatedAt ?? "-",
                },
                {
                  key: "artifact",
                  label: "产物引用",
                  render: (row) => row.artifactRef ?? "-",
                },
              ]}
            />
            <BoundedTable
              rows={data.adoption.strategyUsage}
              rowKey={(row) => [
                row.strategy_label,
                row.as_of ?? "",
                row.status,
              ].join(":")}
              emptyLabel="尚无正式采用记录"
              columns={[
                {
                  key: "strategy",
                  label: "正式策略",
                  render: (row) => row.strategy_label,
                },
                {
                  key: "status",
                  label: "采用状态",
                  render: (row) => row.status,
                },
                {
                  key: "version",
                  label: "正式版本",
                  render: (row) => (
                    Object.values(row.model_versions).join("、") || "未采用"
                  ),
                },
                {
                  key: "date",
                  label: "采用日期",
                  render: (row) => row.as_of ?? "-",
                },
                {
                  key: "coverage",
                  label: "模型覆盖率",
                  render: (row) => (
                    `${(row.candidate_coverage * 100).toFixed(1)}%`
                  ),
                },
                {
                  key: "reason",
                  label: "未采用原因",
                  render: (row) => row.fallback_reason || "-",
                },
              ]}
            />
          </div>
        ) : null}
        {!["data", "training", "validation", "simulation", "adoption"]
          .includes(stage.key) ? (
            <p>该阶段暂无可展示详情</p>
          ) : null}
      </DetailPanel>
    </section>
  );
}
