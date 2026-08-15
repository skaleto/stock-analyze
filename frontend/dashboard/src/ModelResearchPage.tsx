import { useCallback, useEffect, useRef, useState } from "react";
import {
  Archive,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  FlaskConical,
} from "lucide-react";
import { fetchModelResearch } from "./api";
import { StageFlow } from "./StageFlow";
import { TermDisplay } from "./TermDisplay";
import {
  BoundedTable,
  DetailPanel,
  WorkspaceStatusBadge,
} from "./WorkspacePrimitives";
import { useSystemOverviewResource } from "./useSystemOverviewResource";
import { useWorkspaceResource } from "./useWorkspaceResource";
import type { DashboardMarket } from "./workspaceRoute";
import type {
  ModelResearchData,
  ModelResearchAccountSummary,
  ModelResearchArchive,
  ModelResearchHistoricalComparison,
  ModelResearchModel,
  ModelResearchStrategyCampaign,
  ModelResearchTabularEvidence,
  ModelResearchTabularRun,
  BoundedColumn,
  WorkspaceStatus,
} from "./workspaceTypes";

const statusLabels: Record<string, string> = {
  active: "正式启用",
  available: "可用",
  complete: "已完成",
  declared: "已声明",
  empty: "暂无数据",
  failed: "失败",
  fallback: "规则策略兜底",
  fresh: "数据新鲜",
  missing: "缺失",
  no_candidate: "本轮无合格候选",
  not_recorded: "未记录",
  not_used: "未使用",
  observe: "继续观察",
  observing: "观察中",
  partial: "部分可用",
  passed: "已通过",
  pending: "等待中",
  ready: "已就绪",
  rejected: "未通过验收",
  research: "研究中",
  running: "运行中",
  shadow: "影子观察",
  source_unavailable: "数据源不可用",
  stale: "已过期",
  success: "成功",
  unavailable: "状态不可用",
  waiting: "等待中",
  waiting_schedule: "等待计划时间",
  waiting_upstream: "等待上游",
  baseline_only: "仅规则基线",
  falsified: "假设未成立",
  insufficient_data: "证据不足",
  shadow_ready: "可进入影子观察",
  transparent_complete: "透明策略验收完成",
};

const fallbackReasonLabels: Record<string, string> = {
  declared_horizon_unavailable_or_ineligible: "策略所需预测周期尚未就绪或未通过验收",
  decision_lineage_missing: "决策链路证据缺失",
  no_champion: "尚无可用的 Champion 模型",
  prediction_application_evidence_missing: "预测应用证据缺失",
  prediction_artifact_missing: "预测产物缺失",
  prediction_horizon_missing: "所需预测周期缺失",
};

function statusLabel(status: string | null | undefined): string {
  const normalized = String(status ?? "").trim().toLowerCase();
  if (!normalized) return "未记录";
  return statusLabels[normalized] ?? "未知状态";
}

function fallbackReasonLabel(reason: string | null | undefined): string {
  const normalized = String(reason ?? "").trim().toLowerCase();
  if (!normalized) return "-";
  return fallbackReasonLabels[normalized] ?? "原因待系统补充";
}

function value(input: unknown): string {
  if (input == null || input === "") return "-";
  if (typeof input === "number") return input.toFixed(4);
  return String(input);
}

function percent(input: unknown): string {
  if (typeof input !== "number" || !Number.isFinite(input)) return "-";
  return `${(input * 100).toFixed(2)}%`;
}

function bps(input: unknown): string {
  if (typeof input !== "number" || !Number.isFinite(input)) return "-";
  return `${input.toFixed(2)} bp`;
}

function rebalanceFrequencyLabel(input: string | null | undefined): string {
  const labels: Record<string, string> = {
    daily: "每日",
    weekly: "每周",
    monthly: "每月",
  };
  return labels[String(input ?? "").toLowerCase()] ?? "-";
}

function admissionGradeLabel(input: string | null | undefined): string {
  const labels: Record<string, string> = {
    promising: "前景型",
    exploratory: "探索型",
  };
  return labels[String(input ?? "").toLowerCase()] ?? "-";
}

function candidateKindLabel(input: string | null | undefined): string {
  const labels: Record<string, string> = {
    transparent_rule: "透明规则",
    trained_model: "训练模型",
  };
  return labels[String(input ?? "").toLowerCase()] ?? input ?? "-";
}

function shadowParticipationLabel(input: string | null | undefined): string {
  const labels: Record<string, string> = {
    shadow_running: "Shadow 运行中",
    cash_unavailable: "无候选，保持现金",
  };
  return labels[String(input ?? "").toLowerCase()] ?? statusLabel(input);
}

function rebalanceStateLabel(
  frequency: string | null | undefined,
  due: boolean | null | undefined,
): string {
  if (!frequency) return "-";
  return `${rebalanceFrequencyLabel(frequency)} · ${due ? "本期调仓" : "本期持有"}`;
}

function calibrationVersionLabel(input: string | null | undefined): string {
  if (input === "clustered-date-mean-se-v2") return "均值误差校准 v2";
  if (
    input === "clustered-date-isotonic-mean-se-v3"
    || input === "isotonic-date-bucket-v3"
  ) return "单调收益校准 v3";
  return input || "-";
}

function allocationContractLabel(input: string | null | undefined): string {
  const labels: Record<string, string> = {
    "core-plus-tilt-v1": "规则核心 + 模型倾斜",
    "rule-core-v1": "规则核心",
  };
  return labels[String(input ?? "")] ?? input ?? "-";
}

const noTradeReasonLabels: Record<string, string> = {
  insufficient_net_edge: "净收益不足以覆盖成本与不确定性",
  scheduled_rebalance_not_due: "非计划调仓日",
  target_change_below_band: "目标仓位变化过小",
  rank_buffer_hold: "仍在持仓缓冲区",
  hard_risk_exit: "触发风险退出",
  unknown: "原因未分类",
};

const calibrationNoTradeReasonLabels: Record<string, string> = {
  insufficient_net_edge: "成本与置信度过滤",
  scheduled_rebalance_not_due: "非计划调仓日",
  target_change_below_band: "目标变化未越过交易带",
  rank_buffer_hold: "排名缓冲继续持有",
};

const closureBlockerLabels: Record<string, string> = {
  active_drawdown: "主动回撤超限",
  multiplicity_confidence: "多次试验后可信度不足",
  top_tail: "最高分组排序不单调",
};

const closureConditionLabels: Record<string, string> = {
  historical_information_coverage: "资金流历史覆盖",
  untouched_lockbox: "新的前瞻验证窗口",
};

const closureEvidenceLabels: Record<string, string> = {
  exact_cost_walk_forward: "含成本滚动回测",
  score_bucket_spread: "预测分组收益",
  deflated_sharpe_probability: "Deflated Sharpe 校正",
  moneyflow_and_events: "资金流与公告历史覆盖",
  observed_final_already_opened: "历史最终窗口已被观察",
};

function closureDecisionLabel(decision: string): string {
  const labels: Record<string, string> = {
    retain_research_baseline: "保留研究基线",
    promote_to_shadow: "晋升影子观察",
  };
  return labels[decision] ?? decision;
}

function closureMetric(code: string, input: number | null): string {
  if (input == null || !Number.isFinite(input)) return "-";
  if (code === "untouched_lockbox") return String(input);
  return percent(input);
}

function closureRequirement(code: string, input: number | null): string {
  if (input == null || !Number.isFinite(input)) return "-";
  if (code === "active_drawdown") return `不高于 ${percent(input)}`;
  if (code === "top_tail") return `高于 ${percent(input)}`;
  if (code === "untouched_lockbox") return `至少 ${input.toFixed(0)} 个`;
  return `不低于 ${percent(input)}`;
}

function dominantNoTradeReason(counts: Record<string, number> | undefined): string {
  const dominant = Object.entries(counts ?? {})
    .filter(([, count]) => Number.isFinite(count))
    .sort((left, right) => right[1] - left[1])[0];
  if (!dominant) return "-";
  return noTradeReasonLabels[dominant[0]] ?? dominant[0];
}

function baselineLabel(input: string): string {
  const labels: Record<string, string> = {
    momentum_20: "20日动量",
    low_volatility_20: "20日低波动",
    no_trade: "空仓基线",
    transparent_baseline: "透明规则基线",
    candidate_increment: "机器学习增量",
  };
  return labels[input] ?? input;
}

function workspaceStatus(status: string | null | undefined): WorkspaceStatus {
  if (
    status === "complete"
    || status === "transparent_complete"
    || status === "active"
    || status === "passed"
  ) {
    return "success";
  }
  if (status === "running" || status === "research" || status === "failed") {
    return status;
  }
  if (status === "no_candidate") {
    return "research";
  }
  if (status === "waiting_schedule" || status === "waiting_upstream") {
    return status;
  }
  return "unavailable";
}

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    rank_ic_below_floor: "Rank IC 未达到门槛",
    icir_below_floor: "ICIR 稳定性未达到门槛",
    brier_above_ceiling: "概率校准误差超过上限",
    probability_gate_not_met: "上涨概率未达到入选门槛",
    net_excess_return: "扣费后未跑赢基准",
    annual_turnover: "年化换手超过上限",
    icir: "IC 稳定性不足",
    deflated_sharpe_probability: "扣除多次试验后可信度不足",
    probability_of_backtest_overfit: "回测过拟合风险过高",
    pbo_trial_count: "独立试验数量不足",
    subperiod_stability: "分阶段表现不稳定",
    feature_selection_stability: "特征选择不稳定",
    all_accounts_positive_active: "存在未跑赢基准的独立账户",
    execution_evidence_unavailable: "执行成本证据不完整",
    trade_activity: "没有由模型门槛允许的真实模拟成交",
    edge_calibration_available: "预期收益校准不可用",
    attribution_status: "收益归因无法完整对账",
    top_tail: "最高分组未稳定优于次高分组",
    active_max_drawdown: "相对基准回撤超过上限",
    max_drawdown: "组合最大回撤超过上限",
    capital_utilization: "资金利用率不足",
    positive_folds: "分段超额收益并非全部为正",
    point_in_time_audit: "点时数据审计未通过",
    positive_rank_ic: "候选排序能力未转为正值",
    positive_candidate_net_return: "候选扣费后收益未转为正值",
    positive_net_increment: "机器学习增量没有战胜透明基线",
    positive_fold_majority: "多数开发折没有获得正增量",
    absolute_turnover: "绝对换手率超过上限",
    relative_turnover: "相对基线的换手增幅超过上限",
    turnover_delta: "相对基线的换手增幅超过上限",
    drawdown_delta: "相对基线的回撤恶化超过上限",
    model_replay_contract: "冻结模型未按正式组合规则回放",
    deployable_eligible_folds: "冻结模型缺少完整的三折回放证据",
    deployable_positive_fold_majority: "冻结模型多数开发折未跑赢透明基线",
    positive_deployable_net_return: "冻结模型扣费后收益未转为正值",
    deployable_candidate_beats_baseline: "冻结模型未跑赢透明基线",
    deployable_drawdown_delta: "冻结模型相对回撤恶化超过上限",
    deployable_relative_turnover: "冻结模型相对换手增幅超过上限",
    deployable_absolute_turnover: "冻结模型绝对换手超过上限",
    deployable_trade_activity: "冻结模型没有形成可执行模拟成交",
    deployable_capital_utilization: "冻结模型资金利用率不足",
    execution_evidence_status: "冻结模型执行成本证据不完整",
    training_input_provenance: "缺少可验证的 ECS 训练输入包，只生成研究报告",
  };
  const [maybeRole, code] = reason.includes(":")
    ? reason.split(":", 2)
    : ["", reason];
  const roleLabels: Record<string, string> = {
    classifier: "方向判断",
    ranker: "股票排序",
    portfolio: "组合收益",
  };
  const translated = labels[code] ?? code;
  const prefix = roleLabels[maybeRole];
  return `${prefix ? `${prefix}：` : ""}${translated}（${reason}）`;
}

function diagnosticNetExcessReturn(model: ModelResearchModel): unknown {
  return model.diagnosticNetExcessReturn
    ?? model.metrics.diagnostic_net_excess_return
    ?? model.metrics.diagnosticNetExcessReturn;
}

function deployableNetExcessReturn(model: ModelResearchModel): unknown {
  return model.netExcessReturn
    ?? model.metrics.net_excess_return
    ?? model.metrics.netExcessReturn;
}

function modelCapitalUtilization(model: ModelResearchModel): unknown {
  return model.capitalUtilization
    ?? model.metrics.capital_utilization
    ?? model.metrics.capitalUtilization;
}

function calibrationState(model: ModelResearchModel): {
  label: string;
  version: string | null;
} {
  const rawStatus = model.calibrationStatus
    ?? model.metrics.calibration_status
    ?? model.metrics.calibrationStatus;
  const normalized = String(rawStatus ?? "").trim().toLowerCase();
  const available = model.metrics.edge_calibration_available;
  const version = typeof model.metrics.edge_calibration_version === "string"
    ? calibrationVersionLabel(model.metrics.edge_calibration_version)
    : null;

  if (
    available === true
    || ["active", "available", "calibrated", "complete", "enabled", "passed"]
      .includes(normalized)
  ) {
    return { label: "校准可用", version };
  }
  if (
    available === false
    || ["disabled", "failed", "missing", "rejected", "unavailable"]
      .includes(normalized)
  ) {
    return { label: "校准不可用", version };
  }
  return { label: normalized ? statusLabel(normalized) : "未记录", version };
}

function modelIdentity(model: ModelResearchModel): string {
  return [
    model.accountScope || "legacy",
    model.horizon,
    model.modelVersion,
    model.specId ?? "",
  ].join(":");
}

function ModelTable({
  models,
  validation,
}: {
  models: ModelResearchModel[];
  validation: boolean;
}) {
  return (
    <section className="current-mainline-panel" aria-label="当前经典主线">
      <header className="current-mainline-heading">
        <div>
          <span>当前经典主线</span>
          <strong>{models.length} 个账户</strong>
        </div>
        <div>
          <span>{validation ? "验收通过" : "已生成产物"}</span>
          <strong>
            {validation
              ? `${models.filter((model) => model.gatePassed).length} / ${models.length}`
              : String(models.filter((model) => model.artifactStatus === "available").length)}
          </strong>
        </div>
      </header>
      <BoundedTable
        className={`model-metrics-table${validation ? " model-metrics-validation" : ""}`}
        rows={models}
        rowKey={modelIdentity}
        emptyLabel="尚无当前经典主线产物"
        columns={[
        {
          key: "account",
          label: "研究账户",
          render: (row) => accountScopeLabel(row.accountScope),
        },
        {
          key: "version",
          label: "版本",
          render: (row) => (
            <span className="model-cell-stack">
              <strong>{row.modelVersion || "-"}</strong>
              {row.specId ? <small>{row.specId}</small> : null}
            </span>
          ),
        },
        { key: "horizon", label: "周期", render: (row) => `${row.horizon} 日` },
        { key: "samples", label: "样本", render: (row) => String(row.sampleSupport) },
        {
          key: "lifecycle",
          label: "生命周期",
          render: (row) => statusLabel(row.lifecycleStatus),
        },
        {
          key: "rank_ic",
          label: "Rank IC",
          render: (row) => value(
            row.metrics.rank_ic ?? row.metrics.mean_rank_ic,
          ),
        },
        { key: "icir", label: "稳定性 ICIR", render: (row) => value(row.metrics.icir) },
        {
          key: "diagnostic-active",
          label: "排名诊断组合 · 净超额",
          render: (row) => percent(diagnosticNetExcessReturn(row)),
        },
        {
          key: "deployable-active",
          label: "可部署组合 · 净超额",
          render: (row) => percent(deployableNetExcessReturn(row)),
        },
        {
          key: "transparent-baseline",
          label: "透明基线 · 净超额",
          render: (row) => percent(
            row.baselineComparison?.transparent_baseline?.net_excess_return,
          ),
        },
        {
          key: "candidate-increment",
          label: "机器学习增量",
          render: (row) => percent(
            row.baselineComparison?.candidate_increment
              ?.net_excess_return_delta,
          ),
        },
        {
          key: "calibration",
          label: "校准状态",
          render: (row) => {
            const calibration = calibrationState(row);
            return (
              <span className="model-cell-stack">
                <strong>{calibration.label}</strong>
                {calibration.version ? <small>{calibration.version}</small> : null}
              </span>
            );
          },
        },
        {
          key: "trades",
          label: "模拟成交",
          render: (row) => value(row.metrics.trade_count),
        },
        {
          key: "turnover",
          label: "年化换手",
          render: (row) => value(
            row.metrics.annual_turnover ?? row.metrics.turnover,
          ),
        },
        {
          key: "capital_utilization",
          label: "资金利用率",
          render: (row) => percent(modelCapitalUtilization(row)),
        },
        {
          key: "cost",
          label: "成交成本率",
          render: (row) => bps(row.metrics.execution_cost_bps),
        },
        {
          key: "evidence",
          label: "执行证据",
          render: (row) => statusLabel(
            typeof row.metrics.execution_evidence_status === "string"
              ? row.metrics.execution_evidence_status
              : undefined,
          ),
        },
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
                render: (row: ModelResearchModel) => statusLabel(
                  row.artifactStatus,
                ),
              },
              {
                key: "artifactRef",
                label: "产物引用",
                render: (row: ModelResearchModel) => row.artifactRef ?? "-",
              },
            ]
          : []),
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
    </section>
  );
}

function ModelArchivePanel({ archive }: { archive: ModelResearchArchive | undefined }) {
  if (!archive) return null;
  const statusRows = Object.entries(archive.byStatus);
  return (
    <details className="model-archive-panel">
      <summary>
        <span className="model-archive-title">
          <Archive size={15} aria-hidden="true" />
          <strong>历史归档</strong>
        </span>
        <span>{archive.total} 个版本</span>
        <ChevronDown size={15} aria-hidden="true" />
      </summary>
      <div className="model-archive-body" role="region" aria-label="历史归档明细">
        {statusRows.length ? (
          <div className="model-archive-statuses" aria-label="历史归档状态统计">
            {statusRows.map(([status, count]) => (
              <span key={status}>{statusLabel(status)} {count}</span>
            ))}
          </div>
        ) : null}
        <BoundedTable
          className="model-archive-table"
          rows={archive.recent}
          rowKey={modelIdentity}
          emptyLabel="暂无最近归档版本"
          columns={[
            {
              key: "account",
              label: "研究账户",
              render: (row) => accountScopeLabel(row.accountScope),
            },
            { key: "version", label: "版本", render: (row) => row.modelVersion || "-" },
            { key: "horizon", label: "周期", render: (row) => `${row.horizon} 日` },
            {
              key: "status",
              label: "归档状态",
              render: (row) => statusLabel(row.lifecycleStatus),
            },
            {
              key: "diagnostic",
              label: "排名诊断净超额",
              render: (row) => percent(diagnosticNetExcessReturn(row)),
            },
            {
              key: "deployable",
              label: "可部署净超额",
              render: (row) => percent(deployableNetExcessReturn(row)),
            },
            { key: "trained", label: "训练时间", render: (row) => row.trainedAt ?? "-" },
          ]}
        />
      </div>
    </details>
  );
}

function accountScopeLabel(input: string): string {
  const labels: Record<string, string> = {
    hs300: "沪深 300",
    zz500: "中证 500",
    not_recorded: "未记录",
  };
  return (labels[input] ?? input) || "未记录";
}

function estimatorLabel(input: string): string {
  const labels: Record<string, string> = {
    lightgbm_regression: "LightGBM 回归排序",
    lightgbm_lambdarank: "LightGBM LambdaRank",
    lightgbm_top_tail_classifier: "LightGBM 顶端分类",
    not_recorded: "未记录",
  };
  return (labels[input] ?? input) || "未记录";
}

function targetLabel(input: string): string {
  const labels: Record<string, string> = {
    residualized_cross_sectional_rank_v1: "风险剥离后的横截面收益排名",
    total_cross_sectional_rank_v1: "原始横截面收益排名",
    not_recorded: "未记录",
  };
  return (labels[input] ?? input) || "未记录";
}

function ForwardObservationPanel({
  observation,
}: {
  observation: NonNullable<ModelResearchTabularEvidence["forwardObservation"]>;
}) {
  const driftLabel = observation.drift.status === "normal" ? "正常" : "需检查";
  return (
    <section
      className="tabular-loop-closure"
      aria-label="前瞻研究观察"
    >
      <header className="tabular-research-heading">
        <div>
          <span>冻结模型</span>
          <h3>前瞻研究观察</h3>
          <p>
            {observation.modelId} · {observation.latestPredictionDate ?? observation.observationStart}
          </p>
        </div>
        <WorkspaceStatusBadge status="research" />
      </header>
      <dl className="workspace-metric-grid tabular-research-metrics">
        <div>
          <dt>观察交易日</dt>
          <dd>{observation.observationDays} / 60</dd>
        </div>
        <div>
          <dt>到期检验日</dt>
          <dd>{observation.maturedEvidence.maturedDays} / 12</dd>
        </div>
        <div>
          <dt>本日入选 / 候选</dt>
          <dd>{observation.latestSelected} / {observation.latestCandidates}</dd>
        </div>
        <div>
          <dt>前瞻 Rank IC</dt>
          <dd>{value(observation.maturedEvidence.rankIc)}</dd>
        </div>
        <div>
          <dt>扣费后前瞻超额</dt>
          <dd>{percent(observation.portfolio.netExcessReturn)}</dd>
        </div>
        <div>
          <dt>特征覆盖率</dt>
          <dd>{percent(observation.drift.medianFeatureCoverage)}</dd>
        </div>
        <div>
          <dt>数据漂移</dt>
          <dd>{driftLabel}</dd>
        </div>
        <div>
          <dt>晋升证据</dt>
          <dd>
            {observation.promotion.passedChecks} / {observation.promotion.totalChecks}
          </dd>
        </div>
        <div>
          <dt>正式接入</dt>
          <dd>
            {observation.formalOrderSource || observation.formalStrategyWeight > 0
              ? percent(observation.formalStrategyWeight)
              : "未接入正式策略"}
          </dd>
        </div>
      </dl>
    </section>
  );
}

function TabularResearchPanel({
  evidence,
}: {
  evidence: ModelResearchTabularEvidence | undefined;
}) {
  const run = evidence?.best ?? evidence?.latest;
  if (!run) return null;
  const reasons = run.gate.reasons.map((reason) => ({ reason }));
  const latest = evidence?.latest;
  const latestCalibration = latest?.calibration?.enabled
    ? latest.calibration
    : null;
  const comparisonRuns = [
    ...(evidence?.best ? [{ role: "历史最佳", run: evidence.best }] : []),
    ...(latest && latest.configHash !== evidence?.best?.configHash
      ? [{ role: "最近试验", run: latest }]
      : []),
  ];

  return (
    <section className="tabular-research-panel" aria-label="历史经典表格模型归档">
      {evidence?.forwardObservation ? (
        <ForwardObservationPanel observation={evidence.forwardObservation} />
      ) : null}
      {evidence?.closure ? (
        <section
          className="tabular-loop-closure"
          aria-label="本轮自主优化结论"
        >
          <header className="tabular-research-heading">
            <div>
              <span>自主优化闭环</span>
              <h3>本轮自主优化结论</h3>
              <p>
                {evidence.closure.asOf} · {evidence.closure.bestConfigHash}
              </p>
            </div>
            <WorkspaceStatusBadge status="research" />
          </header>
          <dl className="workspace-metric-grid tabular-research-metrics">
            <div>
              <dt>当前决定</dt>
              <dd>{closureDecisionLabel(evidence.closure.decision)}</dd>
            </div>
            <div>
              <dt>门槛通过</dt>
              <dd>
                {evidence.closure.passedChecks} / {evidence.closure.totalChecks}
              </dd>
            </div>
            <div>
              <dt>正式不可变试验</dt>
              <dd>{evidence.closure.officialImmutableTrials}</dd>
            </div>
            <div>
              <dt>诊断变体</dt>
              <dd>{evidence.closure.diagnosticExperiments}</dd>
            </div>
            <div>
              <dt>正式策略权重</dt>
              <dd>{percent(evidence.closure.formalStrategyWeight)}</dd>
            </div>
          </dl>
          <h4>当前候选未通过的模型门槛</h4>
          <BoundedTable
            rows={evidence.closure.blockers}
            rowKey={(row) => row.code}
            emptyLabel="本轮没有剩余阻塞项"
            columns={[
              {
                key: "blocker",
                label: "阻塞项",
                render: (row) => closureBlockerLabels[row.code] ?? row.code,
              },
              {
                key: "measured",
                label: "当前",
                render: (row) => closureMetric(row.code, row.measured),
              },
              {
                key: "required",
                label: "要求",
                render: (row) => closureRequirement(row.code, row.required),
              },
              {
                key: "evidence",
                label: "证据",
                render: (row) => closureEvidenceLabels[row.evidence] ?? row.evidence,
              },
            ]}
          />
          {evidence.closure.nextRunConditions.length ? (
            <>
              <h4>下一轮数据条件</h4>
              <BoundedTable
                rows={evidence.closure.nextRunConditions}
                rowKey={(row) => row.code}
                emptyLabel="当前无需等待额外数据"
                columns={[
                  {
                    key: "condition",
                    label: "条件",
                    render: (row) => closureConditionLabels[row.code] ?? row.code,
                  },
                  {
                    key: "measured",
                    label: "当前",
                    render: (row) => closureMetric(row.code, row.measured),
                  },
                  {
                    key: "required",
                    label: "触发参考",
                    render: (row) => closureRequirement(row.code, row.required),
                  },
                  {
                    key: "evidence",
                    label: "依据",
                    render: (row) => closureEvidenceLabels[row.evidence] ?? row.evidence,
                  },
                ]}
              />
            </>
          ) : null}
        </section>
      ) : null}
      <header className="tabular-research-heading">
        <div>
          <span>历史最佳试验</span>
          <h3>经典表格模型</h3>
          <p>
            {accountScopeLabel(run.accountScope)} · {estimatorLabel(run.estimator)} ·
            {" "}{run.configHash} · {run.asOf || "日期未记录"}
          </p>
        </div>
        <WorkspaceStatusBadge status={run.gate.passed ? "success" : "research"} />
      </header>
      <dl className="workspace-metric-grid tabular-research-metrics">
        <div>
          <dt>验收状态</dt>
          <dd>{run.gate.passed ? "研究门槛通过" : "仍有阻塞项"}</dd>
        </div>
        <div>
          <dt>正式策略权重</dt>
          <dd>{percent(evidence?.formalStrategyWeight ?? 0)}</dd>
        </div>
        <div>
          <dt>组合年化收益</dt>
          <dd>{percent(run.metrics.portfolioCagr)}</dd>
        </div>
        <div>
          <dt>基准年化收益</dt>
          <dd>{percent(run.metrics.benchmarkCagr)}</dd>
        </div>
        <div>
          <dt>扣费后年化超额</dt>
          <dd>{percent(run.metrics.netExcessReturn)}</dd>
        </div>
        <div>
          <dt>组合排序 Rank IC</dt>
          <dd>{value(run.metrics.rankIc)}</dd>
        </div>
        <div>
          <dt>模型增量 Rank IC</dt>
          <dd>{value(run.metrics.rawRankIc)}</dd>
        </div>
        <div>
          <dt>稳定性 ICIR</dt>
          <dd>{value(run.metrics.icir)}</dd>
        </div>
        <div>
          <dt>信息比率</dt>
          <dd>{value(run.metrics.informationRatio)}</dd>
        </div>
        <div>
          <dt>组合最大回撤</dt>
          <dd>{percent(run.metrics.maxDrawdown)}</dd>
        </div>
        <div>
          <dt>相对基准回撤</dt>
          <dd>{percent(run.metrics.activeMaxDrawdown)}</dd>
        </div>
        <div>
          <dt>正超额分段</dt>
          <dd>{run.gate.positiveFolds} / 3</dd>
        </div>
        <div>
          <dt>多次试验后可信度 DSR</dt>
          <dd>{percent(run.metrics.deflatedSharpeProbability)}</dd>
        </div>
        <div>
          <dt>过拟合概率 PBO</dt>
          <dd>{percent(run.metrics.probabilityOfBacktestOverfit)}</dd>
        </div>
        <div>
          <dt>目标标签</dt>
          <dd>{targetLabel(run.target)}</dd>
        </div>
      </dl>
      {comparisonRuns.length > 1 ? (
        <section aria-label="最佳与最近试验对比">
          <BoundedTable
            rows={comparisonRuns}
            rowKey={(row) => row.role}
            emptyLabel="暂无可比较试验"
            columns={[
              { key: "role", label: "记录身份", render: (row) => row.role },
              {
                key: "hash",
                label: "配置",
                render: (row) => row.run.configHash,
              },
              {
                key: "return",
                label: "扣费后年化超额",
                render: (row) => percent(row.run.metrics.netExcessReturn),
              },
              {
                key: "drawdown",
                label: "相对基准回撤",
                render: (row) => percent(row.run.metrics.activeMaxDrawdown),
              },
              {
                key: "dsr",
                label: "DSR",
                render: (row) => percent(
                  row.run.metrics.deflatedSharpeProbability,
                ),
              },
              {
                key: "gates",
                label: "通过门槛",
                render: (row) => {
                  const checks = Object.values(row.run.gate.checks);
                  return `${checks.filter(Boolean).length} / ${checks.length}`;
                },
              },
            ]}
          />
        </section>
      ) : null}
      <div className="tabular-research-evidence-grid">
        <BoundedTable
          rows={run.buckets}
          rowKey={(row) => String(row.bucket)}
          emptyLabel="暂无分组检验数据"
          columns={[
            { key: "bucket", label: "预测分组", render: (row) => `第 ${row.bucket} 组` },
            {
              key: "return",
              label: "平均未来超额",
              render: (row) => percent(row.meanExcessReturn),
            },
            {
              key: "observations",
              label: "样本数",
              render: (row) => row.observations.toLocaleString("zh-CN"),
            },
          ]}
        />
        <BoundedTable
          rows={reasons}
          rowKey={(row) => row.reason}
          emptyLabel="研究门槛已全部通过"
          columns={[
            { key: "gate", label: "未通过门槛", render: (row) => reasonLabel(row.reason) },
          ]}
        />
      </div>
      {latest && latestCalibration ? (
        <section
          className="tabular-latest-diagnostic"
          aria-label="最近试验诊断"
        >
          <header className="tabular-research-heading">
            <div>
              <span>最近试验诊断</span>
              <h3>{latest.configHash}</h3>
              <p>
                {latest.configHash === evidence?.best?.configHash
                  ? "当前最佳"
                  : "未替代当前最佳"}
                {" · "}{latest.gate.reasons.length} 项门槛未通过
              </p>
            </div>
            <WorkspaceStatusBadge status={latest.gate.passed ? "success" : "failed"} />
          </header>
          <dl className="workspace-metric-grid tabular-research-metrics">
            <div>
              <dt>资金利用率</dt>
              <dd>{percent(latest.metrics.capitalUtilization)}</dd>
            </div>
            <div>
              <dt>正置信下界覆盖率</dt>
              <dd>{percent(latestCalibration.positiveLowerBoundCoverage)}</dd>
            </div>
            <div>
              <dt>预测不确定性 P50</dt>
              <dd>{bps(latestCalibration.uncertaintyBpsP50)}</dd>
            </div>
            <div>
              <dt>预测不确定性 P90</dt>
              <dd>{bps(latestCalibration.uncertaintyBpsP90)}</dd>
            </div>
            <div>
              <dt>跟踪误差 P50</dt>
              <dd>{percent(latestCalibration.optimizerTrackingErrorP50)}</dd>
            </div>
            <div>
              <dt>跟踪误差 P90</dt>
              <dd>{percent(latestCalibration.optimizerTrackingErrorP90)}</dd>
            </div>
            <div>
              <dt>经济预测覆盖率</dt>
              <dd>{percent(latestCalibration.economicPredictionCoverage)}</dd>
            </div>
            <div>
              <dt>校准分段数</dt>
              <dd>{latestCalibration.foldCount}</dd>
            </div>
          </dl>
          <BoundedTable
            rows={latestCalibration.noTradeReasons}
            rowKey={(row) => row.reason}
            emptyLabel="暂无未交易原因"
            columns={[
              {
                key: "reason",
                label: "未交易原因",
                render: (row) => (
                  calibrationNoTradeReasonLabels[row.reason] ?? row.reason
                ),
              },
              {
                key: "count",
                label: "触发次数",
                render: (row) => row.count.toLocaleString("zh-CN"),
              },
            ]}
          />
        </section>
      ) : null}
      {(evidence?.experiments.length ?? 0) > 0 ? (
        <BoundedTable
          rows={evidence?.experiments ?? []}
          rowKey={(row: ModelResearchTabularRun) => row.configHash}
          emptyLabel="暂无不可变试验记录"
          columns={[
            {
              key: "role",
              label: "记录身份",
              render: (row) => {
                const roles: string[] = [];
                if (row.configHash === evidence?.best?.configHash) {
                  roles.push("当前最佳");
                }
                if (row.configHash === evidence?.latest?.configHash) {
                  roles.push("最近试验");
                }
                return roles.join(" / ") || "历史试验";
              },
            },
            { key: "asOf", label: "试验日期", render: (row) => row.asOf || "-" },
            { key: "hash", label: "配置", render: (row) => row.configHash },
            {
              key: "estimator",
              label: "估计器",
              render: (row) => estimatorLabel(row.estimator),
            },
            {
              key: "return",
              label: "扣费后年化超额",
              render: (row) => percent(row.metrics.netExcessReturn),
            },
            {
              key: "drawdown",
              label: "相对基准回撤",
              render: (row) => percent(row.metrics.activeMaxDrawdown),
            },
            {
              key: "result",
              label: "验收",
              render: (row) => row.gate.passed ? "通过" : `${row.gate.reasons.length} 项未通过`,
            },
          ]}
        />
      ) : null}
    </section>
  );
}

function AccountSummaryTable({
  accounts,
}: {
  accounts: ModelResearchAccountSummary[] | undefined;
}) {
  return (
    <BoundedTable
      rows={accounts ?? []}
      rowKey={(row) => row.accountScope || "legacy"}
      emptyLabel="尚无账户级研究结果"
      columns={[
        { key: "account", label: "研究账户", render: (row) => row.accountLabel },
        { key: "candidates", label: "候选", render: (row) => String(row.candidateCount) },
        { key: "shadow", label: "进入影子", render: (row) => String(row.shadowCount) },
        { key: "rejected", label: "未通过", render: (row) => String(row.rejectedCount) },
        { key: "status", label: "当前阶段", render: (row) => statusLabel(row.latestStatus) },
        { key: "best", label: "最佳版本", render: (row) => row.bestModelVersion || "-" },
        { key: "rankIc", label: "最佳 Rank IC", render: (row) => value(row.bestRankIc) },
        { key: "trades", label: "模拟成交", render: (row) => String(row.bestTradeCount) },
        {
          key: "active",
          label: "可交易净超额",
          render: (row) => (
            row.bestTradeCount > 0 && row.bestEdgeCalibrationAvailable
              ? percent(row.bestNetExcessReturn)
              : "尚未形成"
          ),
        },
      ]}
    />
  );
}

function HistoricalComparisonPanel({
  comparison,
}: {
  comparison: ModelResearchHistoricalComparison | undefined;
}) {
  if (!comparison || !comparison.scopes.length) return null;
  type HistoricalRow = ModelResearchHistoricalComparison["scopes"][number]["participants"][number] & {
    accountScope: string;
    winner: boolean;
  };
  const rows: HistoricalRow[] = comparison.scopes.flatMap((scope) =>
    scope.participants.map((participant) => ({
      ...participant,
      accountScope: scope.accountScope,
      winner: scope.winner?.participantId === participant.participantId,
    })),
  );
  const participantTypeLabels: Record<string, string> = {
    formal_rule: "正式规则",
    candidate_model: "候选模型",
    baseline: "基线",
  };
  const hasAttribution = rows.some((row) => [
    row.metrics.cashPositionEffectTotal,
    row.metrics.securitySelectionReturnTotal,
    row.metrics.executionCostEffectTotal,
  ].some((metric) => typeof metric === "number" && Number.isFinite(metric)));
  const columns: BoundedColumn<HistoricalRow>[] = [
    {
      key: "account",
      label: "账户",
      render: (row) => accountScopeLabel(row.accountScope),
    },
    {
      key: "name",
      label: "策略 / 模型",
      render: (row) => row.winner ? `${row.name} · 当前最佳` : row.name,
    },
    {
      key: "type",
      label: "身份",
      render: (row) => participantTypeLabels[row.participantType] ?? row.participantType,
    },
    { key: "net", label: "净收益", render: (row) => percent(row.metrics.netReturn) },
    {
      key: "benchmark",
      label: "基准收益",
      render: (row) => percent(row.metrics.benchmarkReturn),
    },
    {
      key: "excess",
      label: "净超额",
      render: (row) => percent(row.metrics.netExcessReturn),
    },
    ...(hasAttribution ? [
      {
        key: "cash-position-effect",
        label: "现金仓位贡献",
        render: (row: HistoricalRow) => percent(row.metrics.cashPositionEffectTotal),
      },
      {
        key: "security-selection",
        label: "选股贡献",
        render: (row: HistoricalRow) => percent(row.metrics.securitySelectionReturnTotal),
      },
      {
        key: "execution-cost-effect",
        label: "交易成本贡献",
        render: (row: HistoricalRow) => percent(row.metrics.executionCostEffectTotal),
      },
    ] : []),
    {
      key: "drawdown",
      label: "最大回撤",
      render: (row) => percent(row.metrics.maxDrawdown),
    },
    {
      key: "ir",
      label: "信息比率",
      render: (row) => value(row.metrics.informationRatio),
    },
    {
      key: "turnover",
      label: "年化换手",
      render: (row) => value(row.metrics.annualTurnover),
    },
  ];
  return (
    <section
      className="tabular-research-panel"
      aria-label="同窗历史对比"
    >
      <header className="tabular-research-heading">
        <div>
          <span>历史诊断 · {comparison.asOf ?? "-"}</span>
          <h3>同窗策略对比</h3>
          <p>
            {comparison.horizon} 日周期 · {comparison.scopes.length} 个独立账户
          </p>
        </div>
        <WorkspaceStatusBadge status={workspaceStatus(comparison.status)} />
      </header>
      <BoundedTable
        rows={rows}
        rowKey={(row) => `${row.accountScope}:${row.participantId}`}
        emptyLabel="尚无同窗比较结果"
        columns={columns}
      />
    </section>
  );
}

function campaignReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    ml_no_proven_increment: "机器学习没有证明净增量",
    no_transparent_candidate_passed_gates_1_2: "透明候选未通过经济与稳健性门",
    point_in_time_audit: "点时数据审计未通过",
  };
  return labels[reason] ?? reasonLabel(reason);
}

function StrategyCampaignPanel({
  campaign,
}: {
  campaign: ModelResearchStrategyCampaign | undefined;
}) {
  if (!campaign || campaign.status === "unavailable" || !campaign.scopes.length) {
    return null;
  }
  return (
    <section className="workspace-detail-panel" aria-label="封闭策略验证">
      <header className="workspace-detail-header">
        <div>
          <h3>封闭策略验证</h3>
          <span>{campaign.campaignId ?? "-"}</span>
        </div>
        <WorkspaceStatusBadge status={workspaceStatus(campaign.status)} />
      </header>
      <div className="workspace-detail-body detail-stack">
        <dl className="workspace-metric-grid">
          <div>
            <dt>试验状态</dt>
            <dd>{statusLabel(campaign.status)}</dd>
          </div>
          <div>
            <dt>正式策略</dt>
            <dd>
              {campaign.formalStrategyActivated ? "已接入" : "未接入正式策略"}
            </dd>
          </div>
          <div>
            <dt>完成时间</dt>
            <dd>{campaign.completedAt?.replace("T", " ") ?? "-"}</dd>
          </div>
          <div>
            <dt>输入封印</dt>
            <dd>{campaign.manifestHash?.slice(0, 12) ?? "-"}</dd>
          </div>
        </dl>
        <BoundedTable
          rows={campaign.scopes}
          rowKey={(row) => row.accountScope}
          emptyLabel="本市场暂无 Campaign 范围"
          columns={[
            {
              key: "scope",
              label: "范围",
              render: (row) => row.accountScope,
            },
            {
              key: "status",
              label: "结论",
              render: (row) => statusLabel(row.status),
            },
            {
              key: "rule",
              label: "规则 / 诊断版本",
              render: (row) => row.selectedRuleSpecId
                ?? (row.diagnosticOnly && row.bestDiagnosticSpecId
                  ? `${row.bestDiagnosticSpecId}（仅诊断）`
                  : "无"),
            },
            {
              key: "ml",
              label: "ML 增量",
              render: (row) => row.selectedIncrementalSpecId ?? "未采用",
            },
            {
              key: "return",
              label: "净收益 / 基准",
              render: (row) => `${percent(row.netReturn)} / ${percent(row.benchmarkReturn)}`,
            },
            {
              key: "excess",
              label: "净超额",
              render: (row) => percent(row.netExcessReturn),
            },
            {
              key: "stress",
              label: "2x 成本超额",
              render: (row) => percent(row.costStressNetExcessReturn),
            },
            {
              key: "reason",
              label: "停止原因",
              render: (row) => row.reasons.map(campaignReasonLabel).join("、") || "-",
            },
          ]}
        />
      </div>
    </section>
  );
}

function ModelResearchDetail({
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
  const refreshState = useRef({
    mounted: false,
    market,
    token: refreshToken,
  });

  useEffect(() => {
    setSelected("data");
  }, [market]);

  useEffect(() => {
    const previous = refreshState.current;
    if (!previous.mounted) {
      refreshState.current = {
        mounted: true,
        market,
        token: refreshToken,
      };
      return;
    }
    const marketChanged = previous.market !== market;
    const tokenChanged = previous.token !== refreshToken;
    refreshState.current = {
      mounted: true,
      market,
      token: refreshToken,
    };
    if (!marketChanged && tokenChanged) resource.refresh();
  }, [market, refreshToken, resource.refresh]);

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
      {data.errors?.length ? (
        <div className="error-banner" role="status">
          部分状态不可用：
          {data.errors.map((item) => item.resource).join("、")}
        </div>
      ) : null}
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
      <StrategyCampaignPanel campaign={data.strategyCampaign} />
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
                <dd>{statusLabel(data.dataPreparation.pointInTimeAudit)}</dd>
              </div>
            </dl>
            {(data.dataPreparation.unclassifiedFeatures ?? []).length ? (
              <div>
                <span>未分类：</span>
                <div className="unclassified-terms">
                  {(data.dataPreparation.unclassifiedFeatures ?? []).map(
                    (feature) => (
                      <TermDisplay key={feature} code={feature} kind="feature" />
                    ),
                  )}
                </div>
              </div>
            ) : null}
            <BoundedTable
              rows={data.dataPreparation.sources}
              rowKey={(row) => [
                row.source,
                row.status,
                row.rows ?? "",
                row.as_of ?? "",
                row.error ?? "",
              ].join(":")}
              emptyLabel="尚无数据源健康记录"
              columns={[
                {
                  key: "source",
                  label: "来源",
                  render: (row) => (
                    <TermDisplay code={row.source} kind="source" />
                  ),
                },
                {
                  key: "status",
                  label: "状态",
                  render: (row) => statusLabel(row.status),
                },
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
          <div className="detail-stack">
            <AccountSummaryTable accounts={data.training.accounts} />
            <ModelTable models={data.training.models} validation={false} />
            <ModelArchivePanel archive={data.training.archive} />
          </div>
        ) : null}
        {stage.key === "validation" ? (
          <div className="detail-stack">
            <HistoricalComparisonPanel
              comparison={data.historicalComparison}
            />
            <TabularResearchPanel evidence={data.tabularResearch} />
            <AccountSummaryTable accounts={data.validation.accounts} />
            <ModelTable models={data.validation.models} validation />
          </div>
        ) : null}
        {stage.key === "simulation" ? (
          <div className="detail-stack">
            <dl className="workspace-metric-grid">
              <div>
                <dt>候选版本</dt>
                <dd>
                  {String(
                    data.simulation.candidate?.display_version ?? "等待候选",
                  )}
                </dd>
              </div>
              {data.simulation.candidate?.candidate_kind ? (
                <div>
                  <dt>候选类型</dt>
                  <dd>{candidateKindLabel(data.simulation.candidate.candidate_kind)}</dd>
                </div>
              ) : null}
              {data.simulation.candidate?.admission_grade ? (
                <div>
                  <dt>准入级别</dt>
                  <dd>{admissionGradeLabel(data.simulation.candidate.admission_grade)}</dd>
                </div>
              ) : null}
              <div>
                <dt>预测产物</dt>
                <dd>
                  {statusLabel(data.simulation.predictionStatus)} ·{" "}
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
                <dt>模型门槛通过</dt>
                <dd>{data.simulation.decision.modelEligibleRows}</dd>
              </div>
              <div>
                <dt>范围外剔除</dt>
                <dd>{data.simulation.decision.scopeRejectedRows}</dd>
              </div>
              <div>
                <dt>可投资候选</dt>
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
            {(data.simulation.accounts ?? []).length ? (
              <BoundedTable
                rows={data.simulation.accounts ?? []}
                rowKey={(row) => row.accountId}
                emptyLabel="尚无独立账户状态"
                columns={[
                  { key: "account", label: "账户", render: (row) => row.accountId },
                  {
                    key: "candidate",
                    label: "当前候选",
                    render: (row) => row.candidateLabel || row.candidateVersion || "-",
                  },
                  {
                    key: "grade",
                    label: "准入级别",
                    render: (row) => admissionGradeLabel(row.admissionGrade),
                  },
                  {
                    key: "status",
                    label: "运行状态",
                    render: (row) => shadowParticipationLabel(row.participationStatus),
                  },
                  {
                    key: "historical-evidence",
                    label: "历史证据",
                    render: (row) => row.historicalNetReturn == null ? "-" : (
                      <span className="shadow-history-evidence">
                        <strong>净 {percent(row.historicalNetReturn)}</strong>
                        <small>
                          超额 {percent(row.historicalNetExcessReturn)} · 回撤 {percent(row.historicalMaxDrawdown)}
                        </small>
                        <small>
                          压力 {percent(row.historicalCostStressNetExcessReturn)} · 成交 {percent(row.historicalTargetFillRatio)}
                        </small>
                      </span>
                    ),
                  },
                  {
                    key: "rebalance",
                    label: "调仓状态",
                    render: (row) => rebalanceStateLabel(
                      row.rebalanceFrequency,
                      row.rebalanceDue,
                    ),
                  },
                  { key: "selected", label: "入选", render: (row) => String(row.selectedCount) },
                  { key: "nav", label: "最新净值", render: (row) => value(row.totalValue) },
                ]}
              />
            ) : null}
            {data.simulation.evaluation ? (
              <>
                <dl className="workspace-metric-grid">
                  <div><dt>精确回放</dt><dd>{statusLabel(data.simulation.evaluation.status)}</dd></div>
                  <div><dt>毛收益</dt><dd>{percent(data.simulation.evaluation.grossReturn)}</dd></div>
                  <div><dt>净收益</dt><dd>{percent(data.simulation.evaluation.netReturn)}</dd></div>
                  <div><dt>基准收益</dt><dd>{percent(data.simulation.evaluation.benchmarkReturn)}</dd></div>
                  <div><dt>净超额</dt><dd>{percent(data.simulation.evaluation.netExcessReturn)}</dd></div>
                  <div><dt>最大回撤</dt><dd>{percent(data.simulation.evaluation.maxDrawdown)}</dd></div>
                  <div><dt>年化换手</dt><dd>{value(data.simulation.evaluation.annualTurnover)}</dd></div>
                  <div><dt>资金利用率</dt><dd>{percent(data.simulation.evaluation.capitalUtilization)}</dd></div>
                  <div><dt>现金比例</dt><dd>{percent(data.simulation.evaluation.cashRatio)}</dd></div>
                  <div><dt>调仓频率</dt><dd>{rebalanceFrequencyLabel(data.simulation.evaluation.rebalanceFrequency)}</dd></div>
                  <div><dt>计划调仓期数</dt><dd>{data.simulation.evaluation.scheduledRebalancePeriods ?? "-"}</dd></div>
                  <div><dt>Sharpe</dt><dd>{value(data.simulation.evaluation.sharpe)}</dd></div>
                  <div><dt>执行成本金额</dt><dd>{value(data.simulation.evaluation.executionCost)}</dd></div>
                  <div><dt>成交成本率</dt><dd>{bps(data.simulation.evaluation.executionCostBps)}</dd></div>
                  <div><dt>冲击成本 P50</dt><dd>{bps(data.simulation.evaluation.impactBpsP50)}</dd></div>
                  <div><dt>冲击成本 P90</dt><dd>{bps(data.simulation.evaluation.impactBpsP90)}</dd></div>
                  <div><dt>成本封顶占比</dt><dd>{percent(data.simulation.evaluation.impactCappedNotionalRatio)}</dd></div>
                  <div><dt>流动性缺失占比</dt><dd>{percent(data.simulation.evaluation.missingLiquidityNotionalRatio)}</dd></div>
                  <div><dt>执行证据</dt><dd>{statusLabel(data.simulation.evaluation.executionEvidenceStatus)}</dd></div>
                  <div><dt>收益校准</dt><dd>{calibrationVersionLabel(data.simulation.evaluation.edgeCalibrationVersion)}</dd></div>
                  <div><dt>分配方式</dt><dd>{allocationContractLabel(data.simulation.evaluation.allocationContract)}</dd></div>
                  <div><dt>模型权重上限</dt><dd>{percent(data.simulation.evaluation.modelTiltCap)}</dd></div>
                  <div><dt>交易放行</dt><dd>{data.simulation.evaluation.decisionCount == null ? "-" : `${data.simulation.evaluation.tradeAllowedCount ?? 0} / ${data.simulation.evaluation.decisionCount}`}</dd></div>
                  <div><dt>主动不交易</dt><dd>{data.simulation.evaluation.noTradeCount ?? "-"}</dd></div>
                  <div><dt>主要不交易原因</dt><dd>{dominantNoTradeReason(data.simulation.evaluation.noTradeReasonCounts)}</dd></div>
                  <div><dt>独立试验</dt><dd>{data.simulation.evaluation.validTrialCount ?? 0}</dd></div>
                </dl>
                <BoundedTable
                  rows={Object.entries(
                    data.simulation.evaluation.baselineComparison ?? {},
                  ).map(([key, metrics]) => ({ key, metrics }))}
                  rowKey={(row) => row.key}
                  emptyLabel="尚无简单规则基线"
                  columns={[
                    { key: "baseline", label: "规则基线", render: (row) => baselineLabel(row.key) },
                    {
                      key: "return",
                      label: "净超额收益",
                      render: (row) => percent(row.metrics.net_excess_return),
                    },
                    {
                      key: "turnover",
                      label: "年化换手",
                      render: (row) => value(
                        row.metrics.annual_turnover ?? row.metrics.turnover,
                      ),
                    },
                  ]}
                />
              </>
            ) : null}
          </div>
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
              rowKey={(row) => row.agent}
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
                  render: (row) => statusLabel(row.status),
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
                  render: (row) => fallbackReasonLabel(row.fallback_reason),
                },
              ]}
            />
            {data.attribution ? (
              <>
                <dl className="workspace-metric-grid">
                  <div>
                    <dt>正式策略采用模型</dt>
                    <dd>{data.attribution.formalModelApplied ? "已采用" : "未采用"}</dd>
                  </div>
                  <div>
                    <dt>完整归因</dt>
                    <dd>{data.attribution.completeCount} / {data.attribution.totalCount}</dd>
                  </div>
                </dl>
                <BoundedTable
                  rows={data.attribution.rows}
                  rowKey={(row) => [row.asOf, row.strategyId, row.accountId].join(":")}
                  emptyLabel="尚无正式策略收益归因"
                  columns={[
                    { key: "date", label: "日期", render: (row) => row.asOf ?? "-" },
                    { key: "strategy", label: "策略 / 账户", render: (row) => `${row.strategyId} · ${row.accountId}` },
                    { key: "status", label: "归因状态", render: (row) => statusLabel(row.status) },
                    { key: "model", label: "模型状态", render: (row) => row.modelPolicyStatus === "rule_only" ? "规则策略" : statusLabel(row.modelPolicyStatus) },
                    { key: "pnl", label: "净盈亏", render: (row) => value(row.netPnl) },
                    { key: "modelPnl", label: "模型贡献", render: (row) => value(row.modelSelectionPnl) },
                    { key: "explained", label: "可解释比例", render: (row) => percent(row.explainedRatio) },
                  ]}
                />
              </>
            ) : null}
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

function dashboardMarket(value: string | undefined): DashboardMarket | null {
  if (value === "a_share" || value === "cn_qdii_etf") return value;
  return null;
}

function ModelResearchOverview({
  refreshToken,
  onFocusMarket,
}: {
  refreshToken: number;
  onFocusMarket: (market: DashboardMarket) => void;
}) {
  const resource = useSystemOverviewResource(refreshToken);

  if (resource.loading && !resource.data) {
    return (
      <div className="skeleton-grid" aria-label="模型研究总览加载中">
        <div /><div /><div /><div />
      </div>
    );
  }
  if (!resource.data) {
    return (
      <div className="error-banner" role="alert">
        模型研究总览不可用：{resource.error ?? "未知错误"}
      </div>
    );
  }

  const data = resource.data;
  const candidateCount = data.models.filter(
    (item) => Boolean(item.iteration?.candidate),
  ).length;
  const championCount = data.models.filter(
    (item) => Boolean(item.iteration?.champion),
  ).length;
  const activeUsage = data.strategy_model_usage.filter(
    (item) => item.status === "active",
  ).length;

  return (
    <section
      className="workspace-page global-workspace-page model-research-overview"
      aria-label="模型研究跨市场总览"
    >
      <header className="global-workspace-heading">
        <div>
          <h2>跨市场模型进度</h2>
          <p>{data.generated_at.replace("T", " ")}</p>
        </div>
        <dl className="global-summary-strip">
          <div><dt>候选版本</dt><dd>{candidateCount}</dd></div>
          <div><dt>正式版本</dt><dd>{championCount}</dd></div>
          <div>
            <dt>策略采用</dt>
            <dd>{activeUsage} / {data.strategy_model_usage.length}</dd>
          </div>
        </dl>
      </header>

      {resource.stale ? (
        <div className="stale-banner" role="status">
          刷新失败，继续显示最近一次模型总览
        </div>
      ) : null}
      <div className="global-market-grid">
        {data.models.map((item) => {
          const market = dashboardMarket(item.market);
          const candidate = item.iteration?.candidate;
          const champion = item.iteration?.champion;
          const usageRows = data.strategy_model_usage.filter(
            (usage) => usage.market === item.market,
          );
          const activeRows = usageRows.filter(
            (usage) => usage.status === "active",
          );
          return (
            <article className="global-market-row" key={item.market}>
              <header>
                <span className="global-market-identity">
                  <FlaskConical size={17} aria-hidden="true" />
                  <span>
                    <strong>{item.market_label}</strong>
                    <small>{item.iteration?.as_of ?? "尚无运行日期"}</small>
                  </span>
                </span>
                <WorkspaceStatusBadge status={workspaceStatus(item.iteration?.status)} />
              </header>
              <dl className="global-market-metrics">
                <div>
                  <dt>当前候选</dt>
                  <dd>
                    {candidate?.display_version
                      ?? (item.iteration?.status === "no_candidate"
                        ? "本轮无合格候选"
                        : "等待候选")}
                  </dd>
                  <small>
                    {candidate?.status_label
                      ?? (item.iteration?.status === "no_candidate"
                        ? "最新主线未通过验收"
                        : "未开始")}
                  </small>
                </div>
                <div>
                  <dt>观察周期</dt>
                  <dd>{candidate?.shadow_cycles ?? 0}</dd>
                  <small>剩余 {candidate?.shadow_cycles_remaining ?? 0}</small>
                </div>
                <div>
                  <dt>正式版本</dt>
                  <dd>{champion?.display_version ?? "尚无 Champion"}</dd>
                  <small>{champion?.status_label ?? "未采用"}</small>
                </div>
                <div>
                  <dt>正式策略采用</dt>
                  <dd>{activeRows.length} / {usageRows.length}</dd>
                  <small>
                    {activeRows.length
                      ? `${activeRows.reduce((sum, row) => sum + row.applied_candidates, 0)} 个候选已应用`
                      : "规则策略兜底"}
                  </small>
                </div>
              </dl>
              <button
                type="button"
                className="global-market-drill"
                disabled={!market}
                aria-label={`查看${item.market_label}模型详情`}
                onClick={() => market && onFocusMarket(market)}
              >
                查看训练与验收指标<ChevronRight size={15} aria-hidden="true" />
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function ModelResearchPage({
  focus,
  market: legacyMarket,
  refreshToken,
  onFocusMarket,
}: {
  focus?: DashboardMarket;
  market?: string;
  refreshToken: number;
  onFocusMarket?: (market?: DashboardMarket) => void;
}) {
  const market = focus ?? dashboardMarket(legacyMarket);
  if (!market) {
    return (
      <ModelResearchOverview
        refreshToken={refreshToken}
        onFocusMarket={(next) => onFocusMarket?.(next)}
      />
    );
  }
  return (
    <>
      {focus && onFocusMarket ? (
        <div className="workspace-detail-toolbar">
          <button
            type="button"
            className="icon-text-button"
            aria-label="返回模型研究总览"
            onClick={() => onFocusMarket(undefined)}
          >
            <ArrowLeft size={15} aria-hidden="true" />跨市场总览
          </button>
          <span>{market === "a_share" ? "A股" : "跨境ETF"}</span>
        </div>
      ) : null}
      <ModelResearchDetail market={market} refreshToken={refreshToken} />
    </>
  );
}
