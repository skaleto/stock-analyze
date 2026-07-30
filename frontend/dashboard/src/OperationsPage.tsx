import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { fetchOperationsCenter } from "./api";
import { StageFlow } from "./StageFlow";
import {
  BoundedTable,
  DetailPanel,
  WorkspaceStatusBadge,
} from "./WorkspacePrimitives";
import { useWorkspaceResource } from "./useWorkspaceResource";
import type {
  OperationsCenterData,
  OperationsChainStage,
  OperationsUnit,
} from "./workspaceTypes";

type Cadence = "daily" | "weekly" | "monthly";

const statusLabels: Record<string, string> = {
  active: "已启用",
  failed: "失败",
  inactive: "未启用",
  running: "运行中",
  skipped: "已跳过",
  success: "成功",
  unavailable: "状态不可用",
  waiting: "等待中",
  waiting_schedule: "等待计划时间",
  waiting_upstream: "等待上游",
};

const scopeLabels: Record<string, string> = {
  all: "全部市场",
  a_share: "A股",
  cn_qdii_etf: "跨境ETF",
  exceptions: "仅看异常",
};

const cadenceLabels: Record<Cadence, string> = {
  daily: "每日",
  weekly: "每周",
  monthly: "每月",
};

const severityLabels: Record<string, string> = {
  critical: "严重",
  warning: "警告",
  info: "提示",
};

const unitLabels: Record<string, string> = {
  "stock-analyze-intelligence.service": "情报增量采集",
  "stock-analyze-market-data.service": "行情与研究快照",
  "stock-analyze-research.service": "特征、预测与评估",
  "stock-analyze-model-iteration.service": "跨市场候选模型迭代",
  "stock-analyze-claude-daily.service": "稳健防守",
  "stock-analyze-codex-daily.service": "趋势进攻",
  "stock-analyze-claude-cn-qdii-etf-daily.service":
    "跨境ETF稳健防守",
  "stock-analyze-codex-cn-qdii-etf-daily.service":
    "跨境ETF趋势进攻",
  "stock-analyze-aggregate-dashboard.service": "Dashboard 聚合",
  "stock-analyze-daily-summary.service": "每日运行摘要",
  "stock-analyze-intelligence-artifact-backfill.service":
    "PDF 下载与解析回填",
  "stock-analyze-intelligence-reconcile.service": "情报对账",
  "stock-analyze-intelligence-semantic.service": "LLM 语义抽取",
};

function statusLabel(value: string | null | undefined): string {
  return value ? statusLabels[value] ?? "未知状态" : "未记录";
}

function unitLabel(unit: string): string {
  return unitLabels[unit] ?? "系统任务";
}

function loadStateLabel(value: string | null | undefined): string {
  if (value === "not-found") return "未安装";
  if (value === "masked") return "已屏蔽";
  if (value === "loaded") return "已加载";
  return value ? "加载状态异常" : "未记录";
}

function timestamp(value: string | null | undefined): string {
  return value ? value.replace("T", " ") : "未记录";
}

function workerBacklog(
  value: Record<string, number | undefined> | null | undefined,
): string {
  if (!value) return "无独立积压记录";
  const values = [
    ["待下载", value.download],
    ["待解析", value.parse],
    ["待语义抽取", value.semantic],
  ].filter((row): row is [string, number] => typeof row[1] === "number");
  return values.length
    ? values.map(([label, count]) => `${label} ${count}`).join(" · ")
    : `总计 ${value.total ?? 0}`;
}

function detailUnitStatus(unit: OperationsUnit): string {
  if (unit.loadState === "not-found" || unit.loadState === "masked") {
    return loadStateLabel(unit.loadState);
  }
  return statusLabel(unit.status);
}

function selectInitialStage(
  rows: OperationsChainStage[],
): string {
  return rows.some((row) => row.key === "intelligence")
    ? "intelligence"
    : rows[0]?.key ?? "";
}

export function OperationsPage({
  scope,
  refreshToken,
}: {
  scope: string;
  refreshToken: number;
}) {
  const loader = useCallback(
    (signal: AbortSignal) => fetchOperationsCenter(scope, signal),
    [scope],
  );
  const resource = useWorkspaceResource<OperationsCenterData>(
    scope,
    true,
    loader,
  );
  const [selected, setSelected] = useState("intelligence");
  const [cadence, setCadence] = useState<Cadence>("daily");
  const previousRequest = useRef({ scope, refreshToken });

  useEffect(() => {
    setSelected("intelligence");
    setCadence("daily");
  }, [scope]);

  useEffect(() => {
    const previous = previousRequest.current;
    previousRequest.current = { scope, refreshToken };
    if (
      previous.scope === scope
      && previous.refreshToken !== refreshToken
    ) {
      resource.refresh();
    }
  }, [scope, refreshToken, resource.refresh]);

  useEffect(() => {
    if (
      resource.data
      && !resource.data.mainChain.some((row) => row.key === selected)
    ) {
      setSelected(selectInitialStage(resource.data.mainChain));
    }
  }, [resource.data, selected]);

  const selectedTask = useMemo(
    () => resource.data?.mainChain.find((row) => row.key === selected) ?? null,
    [resource.data, selected],
  );

  if (resource.loading && !resource.data) {
    return (
      <div className="skeleton-grid" aria-label="运行中心加载中">
        <div /><div /><div /><div />
      </div>
    );
  }

  if (!resource.data) {
    return (
      <div className="error-banner" role="alert">
        运行中心不可用：{resource.error ?? "未知错误"}
      </div>
    );
  }

  const data = resource.data;
  const schedules = data.schedules[cadence];
  const staleRuntime = data.runtime.status === "unavailable";
  const cadenceOrder = Object.keys(cadenceLabels) as Cadence[];

  const onCadenceKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    current: Cadence,
  ) => {
    const currentIndex = cadenceOrder.indexOf(current);
    let next: Cadence | null = null;
    if (event.key === "ArrowRight") {
      next = cadenceOrder[(currentIndex + 1) % cadenceOrder.length];
    } else if (event.key === "ArrowLeft") {
      next = cadenceOrder[
        (currentIndex - 1 + cadenceOrder.length) % cadenceOrder.length
      ];
    } else if (event.key === "Home") {
      next = cadenceOrder[0];
    } else if (event.key === "End") {
      next = cadenceOrder[cadenceOrder.length - 1];
    }
    if (!next) return;
    event.preventDefault();
    setCadence(next);
    document.getElementById(`operations-${next}-tab`)?.focus();
  };

  return (
    <section
      className="workspace-page operations-page"
      aria-label="运行中心"
    >
      {data.errors?.length ? (
        <div className="error-banner" role="status">
          部分状态不可用：
          {data.errors.map((item) => item.resource).join("、")}
        </div>
      ) : null}
      <header className="section-heading">
        <div>
          <h1>运行中心</h1>
          <p>
            {scopeLabels[data.scope] ?? "未知范围"}
            {" · "}
            {data.dailyFreshness.asOfDate}
            {" · "}
            {statusLabel(data.dailyFreshness.status)}
          </p>
        </div>
      </header>

      {resource.stale ? (
        <div className="stale-banner" role="status">
          刷新失败，显示 {timestamp(data.generated_at)} 的最后成功快照
          {resource.error ? `：${resource.error}` : ""}
        </div>
      ) : null}
      {staleRuntime ? (
        <div className="stale-banner" role="status">
          运行时状态不可用
          {data.runtime.lastKnownAt
            ? `，显示 ${timestamp(data.runtime.lastKnownAt)} 的最后已知快照`
            : "，当前没有可用的最后已知快照"}
        </div>
      ) : null}
      {data.truncated ? (
        <div className="stale-banner" role="status">
          响应内容已裁剪，主任务状态仍保留，部分任务明细暂不展示
        </div>
      ) : null}

      <section aria-labelledby="daily-chain-title">
        <header className="section-heading">
          <div><h2 id="daily-chain-title">今日主任务链</h2></div>
        </header>
        {data.mainChain.length ? (
          <StageFlow
            stages={data.mainChain}
            selectedKey={selected}
            ariaLabel="今日主任务链"
            onSelect={setSelected}
          />
        ) : (
          <p>当前范围没有异常主任务</p>
        )}
      </section>

      {selectedTask ? (
        <TaskDetail task={selectedTask} generatedAt={data.generated_at} />
      ) : null}

      <section
        className="background-worker-section"
        aria-labelledby="background-worker-title"
      >
        <header className="section-heading">
          <div><h2 id="background-worker-title">后台队列</h2></div>
        </header>
        <dl className="workspace-metric-grid">
          <div>
            <dt>待下载</dt>
            <dd>{data.background.backlog.download ?? 0}</dd>
          </div>
          <div>
            <dt>待解析</dt>
            <dd>{data.background.backlog.parse ?? 0}</dd>
          </div>
          <div>
            <dt>待语义抽取</dt>
            <dd>{data.background.backlog.semantic ?? 0}</dd>
          </div>
          <div>
            <dt>积压总量</dt>
            <dd>{data.background.backlog.total ?? 0}</dd>
          </div>
          <div>
            <dt>情报快照</dt>
            <dd>{statusLabel(data.background.status)}</dd>
          </div>
          <div>
            <dt>Worker 状态</dt>
            <dd>{statusLabel(data.background.artifactWorkers.status)}</dd>
          </div>
          <div>
            <dt>活跃租约</dt>
            <dd>{data.background.artifactWorkers.activeLeases}</dd>
          </div>
          <div>
            <dt>最近完成</dt>
            <dd>
              {timestamp(data.background.artifactWorkers.latestFinishedAt)}
            </dd>
          </div>
        </dl>
        <div className="background-worker-grid">
          {data.backgroundWorkers.length ? data.backgroundWorkers.map(
            (worker) => (
              <article key={worker.key}>
                <header>
                  <strong>{worker.label}</strong>
                  <WorkspaceStatusBadge status={worker.status} />
                </header>
                <dl>
                  <div>
                    <dt>任务加载</dt>
                    <dd>{loadStateLabel(worker.loadState)}</dd>
                  </div>
                  <div>
                    <dt>上次结果</dt>
                    <dd>{statusLabel(worker.lastResult)}</dd>
                  </div>
                  <div>
                    <dt>下次触发</dt>
                    <dd>{timestamp(worker.nextTriggerAt)}</dd>
                  </div>
                  <div>
                    <dt>对应积压</dt>
                    <dd>{workerBacklog(worker.backlog)}</dd>
                  </div>
                </dl>
              </article>
            ),
          ) : (
            <p>{data.truncated ? "后台明细已裁剪" : "没有异常后台任务"}</p>
          )}
        </div>
      </section>

      <section className="schedule-section" aria-labelledby="schedule-title">
        <header className="section-heading">
          <div><h2 id="schedule-title">周期计划</h2></div>
        </header>
        <div
          role="tablist"
          aria-label="周期计划"
          className="workspace-tabs"
        >
          {cadenceOrder.map((key) => (
            <button
              key={key}
              id={`operations-${key}-tab`}
              type="button"
              role="tab"
              aria-selected={cadence === key}
              aria-controls={`operations-${key}-panel`}
              tabIndex={cadence === key ? 0 : -1}
              className={cadence === key ? "active" : ""}
              onClick={() => setCadence(key)}
              onKeyDown={(event) => onCadenceKeyDown(event, key)}
            >
              {cadenceLabels[key]}
            </button>
          ))}
        </div>
        <div
          id={`operations-${cadence}-panel`}
          role="tabpanel"
          aria-labelledby={`operations-${cadence}-tab`}
        >
          <BoundedTable
            rows={schedules}
            rowKey={(row) => row.unit}
            emptyLabel={
              data.truncated
                ? "周期计划明细已裁剪"
                : "当前周期没有已安装计划"
            }
            columns={[
              {
                key: "label",
                label: "计划",
                render: (row) => row.label,
              },
              {
                key: "status",
                label: "状态",
                render: (row) => (
                  row.loadState === "not-found" || row.loadState === "masked"
                    ? loadStateLabel(row.loadState)
                    : statusLabel(row.status)
                ),
              },
              {
                key: "last",
                label: "上次触发",
                render: (row) => timestamp(row.lastTriggerAt),
              },
              {
                key: "next",
                label: "下次触发",
                render: (row) => timestamp(row.nextTriggerAt),
              },
              {
                key: "automation",
                label: "执行方式",
                render: () => "自动执行",
              },
            ]}
          />
        </div>
      </section>

      <section className="operations-lower-grid">
        <div>
          <header className="section-heading">
            <div><h2>最近运行</h2></div>
          </header>
          <BoundedTable
            rows={data.recentRuns}
            rowKey={(row) => row.runId}
            emptyLabel="没有匹配的运行记录"
            columns={[
              {
                key: "time",
                label: "开始",
                render: (row) => timestamp(row.startedAt),
              },
              {
                key: "market",
                label: "市场",
                render: (row) => scopeLabels[row.market] ?? row.market,
              },
              {
                key: "strategy",
                label: "策略",
                render: (row) => row.strategyLabel,
              },
              {
                key: "command",
                label: "任务",
                render: (row) => row.command,
              },
              {
                key: "status",
                label: "结果",
                render: (row) => statusLabel(row.status),
              },
              {
                key: "error",
                label: "错误摘要",
                render: (row) => row.errorSummary || "无",
              },
            ]}
          />
        </div>
        <div className="intervention-panel">
          <header className="section-heading">
            <div><h2>需要你介入</h2></div>
          </header>
          {data.interventions.length === 0 ? (
            <p>当前无需人工介入</p>
          ) : data.interventions.map((item) => (
            <article key={item.key}>
              <strong>
                {severityLabels[item.severity] ?? "提示"} · {item.title}
              </strong>
              <small>{item.evidence}</small>
            </article>
          ))}
        </div>
      </section>
    </section>
  );
}

function TaskDetail({
  task,
  generatedAt,
}: {
  task: OperationsChainStage;
  generatedAt: string;
}) {
  return (
    <DetailPanel
      title={task.label}
      status={task.status}
      updatedAt={generatedAt}
    >
      <BoundedTable
        rows={task.units}
        rowKey={(row) => row.unit}
        emptyLabel="任务明细已裁剪或尚无本日执行记录"
        columns={[
          { key: "unit", label: "任务", render: (row) => unitLabel(row.unit) },
          {
            key: "status",
            label: "状态",
            render: detailUnitStatus,
          },
          {
            key: "result",
            label: "结果",
            render: (row) => statusLabel(row.result),
          },
          {
            key: "started",
            label: "开始",
            render: (row) => timestamp(row.startedAt),
          },
          {
            key: "finished",
            label: "结束",
            render: (row) => timestamp(row.finishedAt),
          },
        ]}
      />
      {task.crossMarketUnits.length ? (
        <section aria-labelledby="cross-market-evidence-title">
          <h3 id="cross-market-evidence-title">跨市场候选模型证据</h3>
          <p>该结果属于跨市场任务，不能归属到单一市场完成度。</p>
          <BoundedTable
            rows={task.crossMarketUnits}
            rowKey={(row) => row.unit}
            emptyLabel="暂无跨市场候选模型证据"
            columns={[
              {
                key: "unit",
                label: "任务",
                render: (row) => unitLabel(row.unit),
              },
              {
                key: "status",
                label: "状态",
                render: detailUnitStatus,
              },
              {
                key: "result",
                label: "结果",
                render: (row) => statusLabel(row.result),
              },
            ]}
          />
        </section>
      ) : null}
    </DetailPanel>
  );
}
