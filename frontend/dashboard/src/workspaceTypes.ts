export type WorkspaceStatus =
  | "success"
  | "running"
  | "waiting_schedule"
  | "waiting_upstream"
  | "failed"
  | "skipped"
  | "research"
  | "empty"
  | "unavailable";

export type WorkspaceStage = {
  key: string;
  label: string;
  status: WorkspaceStatus;
  primary: string;
  secondary: string;
  updatedAt?: string | null;
  issues?: string[];
};

export type WorkspaceMetric = {
  label: string;
  value: string;
  tone?: "default" | "positive" | "negative" | "warning";
};

export type BoundedColumn<T> = {
  key: string;
  label: string;
  render: (row: T) => string;
};
