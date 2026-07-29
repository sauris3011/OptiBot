"""OptiBot API entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import chat, health, metrics
from app.services import metrics_service, rag_service
from app.services.embeddings import backend_name

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("optibot")


@asynccontextmanager
async def lifespan(_: FastAPI):
    metrics_service.init_db()
    # Embed the policy corpus once at boot so the first real request does not
    # pay for it and skew the latency comparison.
    chunks = rag_service.build_index()
    log.info("embedding backend: %s", backend_name())
    log.info("policy index ready: %d chunks", chunks)
    if not settings.has_api_key:
        log.warning(
            "ANTHROPIC_API_KEY is not set — /api/chat will return an error "
            "until backend/.env is configured."
        )
    yield


app = FastAPI(
    title="OptiBot",
    description="Optimized GenAI workflow for eCommerce order tracking",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(metrics.router)


@app.get("/")
def root() -> dict:
    return {"service": "OptiBot", "docs": "/docs", "health": "/api/health"}
