import type { ReactNode } from "react";
import {
  CheckCircle2,
  Clock3,
  FlaskConical,
  PauseCircle,
  PlayCircle,
  ShieldAlert,
  SkipForward,
  X,
} from "lucide-react";
import type {
  BoundedColumn,
  WorkspaceStatus,
} from "./workspaceTypes";

const statusMeta: Record<WorkspaceStatus, {
  label: string;
  tone: string;
  icon: typeof CheckCircle2;
}> = {
  success: { label: "成功", tone: "ok", icon: CheckCircle2 },
  running: { label: "运行中", tone: "active", icon: PlayCircle },
  waiting_schedule: { label: "等待计划时间", tone: "muted", icon: Clock3 },
  waiting_upstream: { label: "等待上游", tone: "muted", icon: PauseCircle },
  failed: { label: "失败", tone: "warn", icon: ShieldAlert },
  skipped: { label: "已跳过", tone: "muted", icon: SkipForward },
  research: { label: "研究中", tone: "research", icon: FlaskConical },
  empty: { label: "暂无数据", tone: "muted", icon: Clock3 },
  unavailable: { label: "状态不可用", tone: "warn", icon: ShieldAlert },
};

const explicitZoneTimestamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const zoneFreeTimestamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/;

function formatTimestamp(timestamp: string): string {
  if (explicitZoneTimestamp.test(timestamp)) {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) {
      return timestamp;
    }

    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
    const values = Object.fromEntries(
      parts.map(({ type, value }) => [type, value]),
    );
    return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
  }

  return zoneFreeTimestamp.test(timestamp)
    ? timestamp.replace("T", " ")
    : timestamp;
}

export function WorkspaceStatusBadge({
  status,
}: {
  status: WorkspaceStatus;
}) {
  const meta = statusMeta[status];
  const Icon = meta.icon;

  return (
    <span className={`workspace-status status-${meta.tone}`}>
      <Icon size={14} aria-hidden="true" />
      {meta.label}
    </span>
  );
}

export function DetailPanel({
  title,
  status,
  updatedAt,
  onClose,
  children,
}: {
  title: string;
  status: WorkspaceStatus;
  updatedAt?: string | null;
  onClose?: () => void;
  children: ReactNode;
}) {
  return (
    <section className="workspace-detail-panel" aria-label={`${title}详情`}>
      <header>
        <div>
          <h2>{title}</h2>
          <WorkspaceStatusBadge status={status} />
        </div>
        <div>
          {updatedAt ? (
            <time dateTime={updatedAt}>{formatTimestamp(updatedAt)}</time>
          ) : null}
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              aria-label={`关闭${title}详情`}
            >
              <X size={16} aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </header>
      <div className="workspace-detail-body">{children}</div>
    </section>
  );
}

export function BoundedTable<T>({
  rows,
  columns,
  rowKey,
  emptyLabel,
}: {
  rows: T[];
  columns: BoundedColumn<T>[];
  rowKey: (row: T) => string;
  emptyLabel: string;
}) {
  const bounded = rows.slice(0, 20);
  if (columns.length === 0) {
    throw new Error("BoundedTable requires at least one column.");
  }

  const columnKeys = new Set<string>();
  for (const column of columns) {
    if (columnKeys.has(column.key)) {
      throw new Error(
        `BoundedTable received duplicate column key "${column.key}".`,
      );
    }
    columnKeys.add(column.key);
  }

  const boundedRows = bounded.map((row) => ({ row, key: rowKey(row) }));
  const rowKeys = new Set<string>();
  for (const { key } of boundedRows) {
    if (rowKeys.has(key)) {
      throw new Error(`BoundedTable received duplicate row key "${key}".`);
    }
    rowKeys.add(key);
  }

  return (
    <div className="bounded-table-wrap">
      <table className="bounded-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {boundedRows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="empty-cell">
                {emptyLabel}
              </td>
            </tr>
          ) : boundedRows.map(({ row, key }) => (
            <tr key={key}>
              {columns.map((column) => (
                <td key={column.key}>{column.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
