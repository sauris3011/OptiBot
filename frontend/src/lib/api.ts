import type {
  AuditRow,
  ChatResponse,
  GovernanceSummary,
  HealthResponse,
  MetricsSummary,
  Mode,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} on ${path}`);
  }
  return (await res.json()) as T;
}

export function sendChat(
  message: string,
  mode: Mode,
  sessionId: string,
): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message, mode, session_id: sessionId }),
  });
}

export const getSummary = () => request<MetricsSummary>("/api/metrics/summary");

export const getGovernance = () =>
  request<GovernanceSummary>("/api/metrics/governance");

export const getAudit = (limit = 100) =>
  request<{ audit: AuditRow[] }>(`/api/metrics/audit?limit=${limit}`);

export const getHealth = () => request<HealthResponse>("/api/health");

export const getSampleOrders = () =>
  request<{ orders: { order_id: string; status: string }[] }>(
    "/api/sample-orders",
  );

export const resetMetrics = () =>
  request<{ status: string }>("/api/metrics/reset", { method: "POST" });
