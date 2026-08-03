"""The "Play" button — start, poll, and cancel an automated simulation run.

Following the same convention as ``llm_config.py``: a rejected or failed
operation returns HTTP 200 with ``status: "error"`` in the body, not a 5xx —
the frontend needs to show *why* a start was refused (e.g. one is already
running), not just that something went wrong.
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter

from app.models.schemas import (
    SimulationStartRequest,
    SimulationStartResponse,
    SimulationState,
)
from app.services import simulation_service

router = APIRouter(prefix="/api", tags=["simulate"])


def _state_model(state: simulation_service.SimulationState) -> SimulationState:
    return SimulationState(
        status=state.status,
        run_id=state.run_id,
        size=state.size,
        total=state.total,
        completed=state.completed,
        started_at=state.started_at,
        finished_at=state.finished_at,
        error=state.error,
        steps=[dataclasses.asdict(step) for step in state.steps],
    )


@router.post("/simulate/start", response_model=SimulationStartResponse)
def start_simulation(payload: SimulationStartRequest) -> SimulationStartResponse:
    try:
        state = simulation_service.start(size=payload.size, reset=payload.reset)
    except simulation_service.SimulationError as exc:
        return SimulationStartResponse(
            status="error", message=str(exc), state=_state_model(simulation_service.status())
        )

    label = "Quick demo" if payload.size == "quick" else "Full set"
    message = (
        f"{label} started — {state.total} chats "
        f"({state.total // 2} baseline, {state.total // 2} optimized)."
    )
    if payload.reset:
        message += " Previous dashboard data was cleared."
    return SimulationStartResponse(status="started", message=message, state=_state_model(state))


@router.get("/simulate/status", response_model=SimulationState)
def simulation_status() -> SimulationState:
    return _state_model(simulation_service.status())


@router.post("/simulate/cancel", response_model=SimulationStartResponse)
def cancel_simulation() -> SimulationStartResponse:
    # Check *before* setting the cancel flag: the flag takes effect between
    # calls, not instantly, so reading status() right after cancel() is racy
    # and can report "running" even though a stop was just requested. The
    # frontend keeps polling /status regardless, so this message only needs to
    # say whether a stop was requested — not confirm it already landed.
    was_running = simulation_service.status().status == "running"
    state = simulation_service.cancel()
    if was_running:
        message = "Stopping after the current chat finishes."
    else:
        message = f"No simulation is running (last status: {state.status})."
    return SimulationStartResponse(status="cancelled", message=message, state=_state_model(state))
