"""OptiBot API entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configured before the app imports so the TLS-bootstrap messages that
# services/gateway.py emits at module scope are formatted like everything else.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("optibot")

from app import llm_settings  # noqa: E402
from app.config import settings  # noqa: E402
from app.routers import chat, health, llm_config, metrics  # noqa: E402
from app.services import llm_client, metrics_service, rag_service  # noqa: E402
from app.services import gateway  # noqa: E402,F401 - import runs the TLS bootstrap
from app.services.embeddings import backend_name  # noqa: E402


@asynccontextmanager
async def lifespan(_: FastAPI):
    metrics_service.init_db()
    # Embed the policy corpus once at boot so the first real request does not
    # pay for it and skew the latency comparison.
    chunks = rag_service.build_index()
    log.info("embedding backend: %s", backend_name())
    log.info("policy index ready: %d chunks", chunks)

    log.info("LiteLLM gateway: %s", llm_settings.base_url())
    # Log the resolved alias and its price source per slot. A "default" source
    # means the local price table does not know this model, so the headline cost
    # comparison would be built on the fallback rate — plausible, and wrong.
    for slot, alias in llm_settings.all_models().items():
        (price_in, price_out), source = llm_client.price_for_with_source(alias)
        log.log(
            logging.WARNING if source == "default" else logging.INFO,
            "model slot %-8s -> %-28s ($%.2f/$%.2f per Mtok, price source: %s)",
            slot,
            alias,
            price_in,
            price_out,
            source,
        )
    if not llm_settings.has_api_key():
        log.warning(
            "%s is not set — /api/chat will return an error until you set a key in "
            "backend/.env or via the gear icon in the UI.",
            llm_settings.API_KEY_ENV_VAR,
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
app.include_router(llm_config.router)


@app.get("/")
def root() -> dict:
    return {"service": "OptiBot", "docs": "/docs", "health": "/api/health"}
