export interface OverviewSnapshot {
  snapshot_date: string;
  equity: number;
  cash: number;
}

export interface HeartbeatOut {
  last_seen_at: string;
  last_run_type: string | null;
  last_ticker: string | null;
}

export interface BreakerOut {
  id: string;
  breaker_type: string;
  trigger_reason: string;
  tripped_at: string;
}

export interface OverviewResponse {
  snapshot: OverviewSnapshot | null;
  heartbeat: HeartbeatOut | null;
  heartbeat_stale: boolean;
  active_breakers: BreakerOut[];
}

export interface PositionOut {
  ticker: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  latest_decision: string | null;
  latest_rating: string | null;
}

export interface CandidateOut {
  ticker: string;
  held: boolean;
}

export interface PositionsResponse {
  positions: PositionOut[];
  candidates: CandidateOut[];
}

export interface DecisionSummary {
  id: string;
  rating: string;
  decision: string;
  reasoning_summary: string;
}

export interface AgentRunSummary {
  id: string;
  ticker: string;
  run_type: string;
  started_at: string;
  outcome: string | null;
  decision: DecisionSummary | null;
}

export interface DecisionsResponse {
  runs: AgentRunSummary[];
}

export interface TranscriptEntry {
  role: string;
  content: string;
}

export interface DecisionDetailResponse {
  id: string;
  ticker: string;
  run_type: string;
  started_at: string;
  outcome: string | null;
  market_status: string;
  decision: DecisionSummary | null;
  transcripts: TranscriptEntry[];
  order_id: string | null;
}

export interface FillOut {
  filled_at: string;
  fill_qty: number;
  fill_price: number;
}

export interface OrderOut {
  id: string;
  submitted_at: string;
  ticker: string;
  side: string;
  qty: number;
  limit_price: number;
  status: string;
  decision_id: string;
  agent_run_id: string;
  fills: FillOut[];
  cancellable: boolean;
}

export interface OrdersResponse {
  orders: OrderOut[];
}

export interface TickerDetailResponse {
  ticker: string;
  runs: AgentRunSummary[];
  orders: OrderOut[];
}

export interface RealizedPnlOut {
  id: string;
  ticker: string;
  pnl_amount: number;
  closed_at: string;
  decision_ids: string[];
}

export interface PnlResponse {
  snapshots: OverviewSnapshot[];
  realized: RealizedPnlOut[];
}

export interface PnlSeriesPoint {
  date: string;
  equity: number;
}

export interface PnlSeriesResponse {
  points: PnlSeriesPoint[];
}

export interface ProcessStatusOut {
  alive: boolean;
  pid: number | null;
}

export interface ControlStatusResponse {
  scheduler: ProcessStatusOut;
  watchdog: ProcessStatusOut;
  scheduler_log: string | null;
  watchdog_log: string | null;
  run_once_log: string | null;
}

export interface RiskConfigOut {
  max_position_pct: number;
  cash_reserve_pct: number;
  stop_loss_pct: number;
  daily_drawdown_breaker_pct: number;
  weekly_drawdown_breaker_pct: number;
  stale_data_max_age_minutes: number;
}

export interface ConfigResponse {
  tickers: string[];
  risk_config: RiskConfigOut;
  env_values: Record<string, string | null>;
}
