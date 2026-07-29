from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import ChatRequest, ChatResponse
from app.services import chat_service

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Answer one customer query through the selected pipeline.

    Errors are returned in the response body rather than raised as a 5xx: the
    dashboard needs to record and display a failed turn, not lose it.
    """
    return chat_service.handle(request.message, request.session_id, request.mode)
