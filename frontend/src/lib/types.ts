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
  api_key_configured: boolean;
  embedding_backend: string;
  rag_index: { chunks: number; dimensions: number; sources: Record<string, number> };
  models: Record<string, string>;
}
