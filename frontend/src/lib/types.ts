export type Mode = "baseline" | "optimized";

export interface TraceStep {
  layer: string;
  detail: string;
  duration_ms: number;
}

export interface ChatMetrics {
  mode: Mode;
  model: string | null;
  tier: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  latency_ms: number;
  cost_usd: number;
  cache_hit: boolean;
  cache_similarity: number;
  rag_used: boolean;
  rag_sources: string[];
  confidence: number;
  guardrail_events: string[];
  pii_detected: string[];
  pii_masked: boolean;
  blocked: boolean;
}

export interface ChatResponse {
  response: string;
  metrics: ChatMetrics;
  trace: TraceStep[];
  sources: string[];
  error: string | null;
}

/* ---------------- automated simulation ("Play" button) ---------------- */
/* Mirrors backend/app/models/schemas.py — keep the two in step. */

export type SimulationSize = "quick" | "full";
export type SimulationRunStatus =
  | "idle"
  | "running"
  | "completed"
  | "cancelled"
  | "failed";

export interface SimulationStep {
  index: number;
  total: number;
  mode: Mode;
  case_id: string;
  query: string;
  difficulty: string | null;
  response: string;
  error: string | null;
  model: string | null;
  tier: string | null;
  input_tokens: number;
  output_tokens: number;
  tokens: number;
  latency_ms: number;
  cost_usd: number;
  cache_hit: boolean;
  rag_used: boolean;
  sources: string[];
  confidence: number;
  blocked: boolean;
  guardrail_events: string[];
  passed: boolean | null;
  reason: string;
  trace: TraceStep[];
}

export interface SimulationState {
  status: SimulationRunStatus;
  run_id: string;
  size: SimulationSize;
  total: number;
  completed: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  steps: SimulationStep[];
}

export interface SimulationStartRequest {
  size?: SimulationSize;
  reset?: boolean;
}

export interface SimulationStartResponse {
  status: "started" | "cancelled" | "error";
  message: string;
  state: SimulationState;
}

export interface ModeSummary {
  mode: string;
  requests: number;
  avg_tokens: number;
  total_tokens: number;
  avg_latency_ms: number;
  avg_cost_usd: number;
  total_cost_usd: number;
  avg_confidence: number;
  cache_hits: number;
  cache_hit_rate: number;
  rag_used: number;
  rag_rate: number;
  blocked: number;
  model_mix: Record<string, number>;
  tier_mix: Record<string, number>;
}

export interface MetricsSummary {
  baseline: ModeSummary;
  optimized: ModeSummary;
  deltas: {
    tokens_reduction_pct: number;
    latency_reduction_pct: number;
    cost_reduction_pct: number;
    confidence_gain_pct: number;
    monthly_cost_baseline_10k: number;
    monthly_cost_optimized_10k: number;
  };
  cache: {
    entries: number;
    lookups: number;
    hits: number;
    hit_rate: number;
    threshold: number;
  };
}

export interface GovernanceSummary {
  event_counts: Record<string, number>;
  pii_events: number;
  pii_masked_events: number;
  pii_unmasked_events: number;
  blocked_requests: number;
  audit_records: number;
  interactions: number;
  audit_coverage_pct: number;
}

export interface AuditRow {
  id: number;
  timestamp: string;
  session_id: string;
  mode: string;
  event_type: string;
  query_masked: string | null;
  response_masked: string | null;
  model: string | null;
  pii_detected: string | null;
  pii_masked: number;
  guardrail_events: string | null;
  detail: string | null;
}

export interface HealthResponse {
  status: string;
  provider: "litellm";
  /** Refers to the LiteLLM gateway key (LITELLM_API_KEY or the settings panel). */
  api_key_configured: boolean;
  api_key_source: "runtime" | "env" | "none";
  litellm_base_url: string;
  /** null when the request did not ask for a gateway probe. */
  litellm_reachable: boolean | null;
  embedding_backend: string;
  rag_index: { chunks: number; dimensions: number; sources: Record<string, number> };
  models: Record<string, string>;
}

/* ---------------- LiteLLM gateway configuration ---------------- */
/* Mirrors backend/app/models/schemas.py — keep the two in step. */

export type ModelSlot = "baseline" | "simple" | "complex";

export interface ApiKeyStatus {
  env_var: string;
  is_set: boolean;
  source: "runtime" | "env" | "none";
  masked_value: string;
}

export interface TlsStatus {
  verify: boolean;
  ca_bundle: string | null;
  source: "default" | "LITELLM_CA_BUNDLE" | "LITELLM_SSL_VERIFY";
  /** Set when LITELLM_CA_BUNDLE points at a file that cannot be read. */
  warning: string | null;
  note: string;
}

export interface SlotConfig {
  slot: ModelSlot;
  label: string;
  description: string;
  model: string;
  source: "runtime" | "env" | "fallback";
  price_in_per_mtok: number;
  price_out_per_mtok: number;
  price_source: "exact" | "prefix" | "default";
}

export interface LlmConfig {
  provider: "litellm";
  base_url: string;
  chat_url: string;
  models_url: string;
  slots: SlotConfig[];
  api_key_status: ApiKeyStatus;
  tls: TlsStatus;
  request_timeout_s: number;
  max_attempts: number;
  runtime_config_path: string;
  persisted: boolean;
}

export interface LlmModelsResponse {
  status: "success" | "error";
  models: string[];
  models_source: "litellm" | "fallback";
  warning: string | null;
}

export interface LlmConfigResponse extends LlmModelsResponse {
  config: LlmConfig;
}

export interface LlmConfigUpdate {
  models?: Partial<Record<ModelSlot, string>>;
  base_url?: string;
  api_key?: string;
}

export interface LlmConfigSaveResponse {
  status: "success" | "error";
  message: string;
  config: LlmConfig | null;
}

export interface LlmTestRequest {
  slot?: ModelSlot;
  model_id?: string;
  /** Unsaved overrides, so a pending key or URL can be tested before saving. */
  api_key?: string;
  base_url?: string;
}

export interface LlmUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_basis: string | null;
}

export interface LlmTestResponse {
  status: "success" | "error";
  message: string;
  slot: ModelSlot | null;
  model: string | null;
  latency_ms: number | null;
  response: string | null;
  usage: LlmUsage | null;
  config_used: Record<string, string>;
}
