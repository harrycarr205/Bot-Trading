import type {
  OverviewResponse,
  PositionsResponse,
  DecisionsResponse,
  DecisionDetailResponse,
  OrdersResponse,
  PnlResponse,
  PnlSeriesResponse,
  ControlStatusResponse,
  ConfigResponse,
  TickerDetailResponse,
  RiskConfigOut,
  ControlStartResult,
  ControlStopResult,
  EnvSettingsIn,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  overview: () => request<OverviewResponse>("/overview"),
  positions: () => request<PositionsResponse>("/positions"),
  ticker: (symbol: string) =>
    request<TickerDetailResponse>(`/ticker/${encodeURIComponent(symbol)}`),
  decisions: (ticker?: string) =>
    request<DecisionsResponse>(
      `/decisions${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ""}`
    ),
  decisionDetail: (id: string) =>
    request<DecisionDetailResponse>(`/decisions/${id}`),
  orders: () => request<OrdersResponse>("/orders"),
  cancelOrder: (id: string) =>
    request<{ cancelled: boolean }>(`/orders/${id}/cancel`, {
      method: "POST",
    }),
  pnl: () => request<PnlResponse>("/pnl"),
  pnlSeries: () => request<PnlSeriesResponse>("/pnl/series"),
  controlStatus: () => request<ControlStatusResponse>("/control/status"),
  controlStart: (name: "scheduler" | "watchdog") =>
    request<ControlStartResult>(`/control/${name}/start`, { method: "POST" }),
  controlStop: (name: "scheduler" | "watchdog") =>
    request<ControlStopResult>(`/control/${name}/stop`, { method: "POST" }),
  controlForceStop: (name: "scheduler" | "watchdog") =>
    request<ControlStopResult>(`/control/${name}/force-stop`, { method: "POST" }),
  runNow: () => request<ControlStartResult>("/control/run-now", { method: "POST" }),
  config: () => request<ConfigResponse>("/config"),
  saveCandidateUniverse: (tickers: string[]) =>
    request<{ saved: boolean }>("/config/candidate-universe", {
      method: "POST",
      body: JSON.stringify({ tickers }),
    }),
  saveRiskConfig: (updates: RiskConfigOut, note: string) =>
    request<{ saved: boolean }>("/config/risk-config", {
      method: "POST",
      body: JSON.stringify({ ...updates, note }),
    }),
  saveEnvSettings: (updates: EnvSettingsIn) =>
    request<{ saved: boolean }>("/config/env-settings", {
      method: "POST",
      body: JSON.stringify(updates),
    }),
};
