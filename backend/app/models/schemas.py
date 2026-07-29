"""Request/response contracts for the API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Mode = Literal["baseline", "optimized"]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    mode: Mode = "optimized"
    session_id: str = Field(default="demo-session", max_length=64)


class TraceStep(BaseModel):
    layer: str
    detail: str
    duration_ms: int = 0


class ChatMetrics(BaseModel):
    mode: Mode
    model: str | None = None
    tier: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False
    cache_similarity: float = 0.0
    rag_used: bool = False
    rag_sources: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    guardrail_events: list[str] = Field(default_factory=list)
    pii_detected: list[str] = Field(default_factory=list)
    pii_masked: bool = False
    blocked: bool = False


class ChatResponse(BaseModel):
    response: str
    metrics: ChatMetrics
    trace: list[TraceStep] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    api_key_configured: bool
    embedding_backend: str
    rag_index: dict[str, Any]
    models: dict[str, str]
