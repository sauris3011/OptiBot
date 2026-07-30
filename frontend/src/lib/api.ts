import type {
  AuditRow,
  ChatResponse,
  GovernanceSummary,
  HealthResponse,
  LlmConfigResponse,
  LlmConfigSaveResponse,
  LlmConfigUpdate,
  LlmModelsResponse,
  LlmTestRequest,
  LlmTestResponse,
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

export const getHealth = (probe = false) =>
  request<HealthResponse>(`/api/health${probe ? "?probe=true" : ""}`);

/**
 * The /api/llm-config endpoints answer 200 with `status: "error"` in the body
 * for gateway and configuration failures (matching /api/chat), so `request()`
 * will not throw for those — callers must branch on `status` as well as catch.
 * There is no client-side timeout here either; the backend caps the connection
 * test at LITELLM_TEST_TIMEOUT_S and model listing at LITELLM_MODELS_TIMEOUT_S
 * so the UI cannot hang for the 120s chat timeout.
 */
export const getLlmConfig = () => request<LlmConfigResponse>("/api/llm-config");

export const getLlmModels = () =>
  request<LlmModelsResponse>("/api/llm-config/models");

export const saveLlmConfig = (body: LlmConfigUpdate) =>
  request<LlmConfigSaveResponse>("/api/llm-config", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const testLlmConfig = (body: LlmTestRequest) =>
  request<LlmTestResponse>("/api/llm-config/test", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getSampleOrders = () =>
  request<{ orders: { order_id: string; status: string }[] }>(
    "/api/sample-orders",
  );

export const resetMetrics = () =>
  request<{ status: string }>("/api/metrics/reset", { method: "POST" });
