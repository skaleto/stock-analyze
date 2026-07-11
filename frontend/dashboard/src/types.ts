export type AgentId = string;

export type TaskStatus = {
  status?: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_summary?: string | null;
};

export type SummaryAgent = {
  agent: AgentId;
  nav: {
    latest: number | null;
    latest_display: string;
    date: string | null;
    return: number | null;
    return_display: string;
  };
  decision: {
    href: string;
    pending_orders: { total: number; buy: number; sell: number };
    weekly_report_href: string | null;
  };
  tasks: {
    daily: TaskStatus;
    weekly: TaskStatus;
  };
};

export type MarketSummary = {
  market: string;
  label: string;
  currency: string;
  agents: SummaryAgent[];
  monthly: { status?: string; href?: string | null; label?: string | null };
};

export type DashboardSummary = {
  generated_at: string;
  markets: MarketSummary[];
  sentiment: unknown[];
};

export type NavPoint = {
  date: string;
  cash?: number | null;
  market_value?: number | null;
  total_value?: number | null;
  total_value_display?: string;
  return?: number | null;
  return_display?: string;
  benchmark_code?: string | null;
  benchmark_codes?: string[];
  benchmark_close?: number | null;
  benchmark_date?: string | null;
};

export type OrderRow = Record<string, string | number | null | undefined> & {
  account_id?: string;
  code?: string;
  name?: string;
  side?: string;
  shares?: number;
  target_weight?: number;
  target_value?: number;
  trade_date?: string;
  score?: number;
  execute_after?: string;
  reason?: string;
};

export type DashboardDetail = {
  generated_at: string;
  market: string;
  market_label: string;
  currency: string;
  agent: string;
  nav: {
    latest: NavPoint | null;
    series: NavPoint[];
    accounts: Record<string, unknown>[];
    benchmark_codes?: string[];
  };
  orders: {
    summary: { total: number; buy: number; sell: number };
    rows: OrderRow[];
  };
  positions: {
    summary: { total: number; market_value?: number; market_value_display?: string };
    rows: OrderRow[];
  };
  trades: {
    summary: { total: number };
    rows: OrderRow[];
  };
  runs: {
    summary: { total: number };
    rows: OrderRow[];
  };
  weekly_report: {
    exists: boolean;
    href: string | null;
    markdown: string;
  };
};
