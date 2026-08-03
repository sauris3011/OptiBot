"""The "Play" button — an automated run through the golden dataset.

Manual chat and this simulator write to the exact same tables through the exact
same ``chat_service.handle()`` call. There is no separate "demo mode" data path
to keep in sync with the real one — a simulated turn is a real turn, just typed
by code instead of a person, which is what makes the dashboard numbers it
produces trustworthy rather than staged.

Runs baseline first, then optimized, one case at a time — mirroring both the
PRD's demo script and ``scripts/run_evaluation.py``, so the story stays "same
questions, same data, different machinery" whether you click Play or run the
CLI. A small delay between calls keeps a full run under the per-session rate
guardrail (see ``app/services/guardrails.py``), the same reason
``run_evaluation.py`` sleeps between calls.

Only one run at a time — this is a demo tool, not a multi-tenant job queue. The
worker runs in a plain background thread rather than the FastAPI event loop
because ``chat_service.handle()`` is fully synchronous (it calls the LiteLLM
gateway with blocking I/O); a thread lets the HTTP request that starts the run
return immediately while the run itself continues in the background for the
frontend to poll.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from app.services import chat_service, evaluation, metrics_service
from app.services.cache_service import semantic_cache

Size = Literal["quick", "full"]
Status = Literal["idle", "running", "completed", "cancelled", "failed"]

# Long enough to stay well under the 20-requests/minute session guardrail even
# on a full 23-case run against a fast gateway; short enough to stay snappy for
# an audience watching a quick 8-case run.
_CALL_DELAY_S = 0.3


@dataclass
class SimulationStep:
    index: int
    total: int
    mode: str
    case_id: str
    query: str
    difficulty: str | None
    response: str
    error: str | None
    model: str | None
    tier: str | None
    input_tokens: int
    output_tokens: int
    tokens: int
    latency_ms: int
    cost_usd: float
    cache_hit: bool
    rag_used: bool
    sources: list[str]
    confidence: float
    blocked: bool
    guardrail_events: list[str]
    passed: bool | None
    reason: str
    # Lets the frontend replay the same "Pipeline trace" panel manual chat uses,
    # live, per step — {layer, detail, duration_ms} dicts, chat_service's own shape.
    trace: list[dict]


@dataclass
class SimulationState:
    status: Status = "idle"
    run_id: str = ""
    size: Size = "quick"
    total: int = 0
    completed: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    steps: list[SimulationStep] = field(default_factory=list)


_LOCK = threading.RLock()  # start()/cancel() call status() while already holding it
_CANCEL = threading.Event()
_WORKER: threading.Thread | None = None
_STATE = SimulationState()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def status() -> SimulationState:
    with _LOCK:
        # Shallow-copy the step list so a poll can't observe a half-appended
        # list while the worker thread is mid-append.
        return SimulationState(
            status=_STATE.status,
            run_id=_STATE.run_id,
            size=_STATE.size,
            total=_STATE.total,
            completed=_STATE.completed,
            started_at=_STATE.started_at,
            finished_at=_STATE.finished_at,
            error=_STATE.error,
            steps=list(_STATE.steps),
        )


class SimulationError(RuntimeError):
    pass


def start(*, size: Size = "quick", reset: bool = True) -> SimulationState:
    global _WORKER

    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            raise SimulationError(
                f"A simulation is already {_STATE.status} "
                f"({_STATE.completed}/{_STATE.total}). Cancel it first, or wait "
                "for it to finish."
            )

        cases = evaluation.load_cases(
            only_ids=evaluation.QUICK_DEMO_CASE_IDS if size == "quick" else None
        )
        run_id = uuid.uuid4().hex[:8]
        total = len(cases) * 2  # baseline + optimized

        _CANCEL.clear()
        _STATE.status = "running"
        _STATE.run_id = run_id
        _STATE.size = size
        _STATE.total = total
        _STATE.completed = 0
        _STATE.started_at = _now()
        _STATE.finished_at = None
        _STATE.error = None
        _STATE.steps = []

        _WORKER = threading.Thread(
            target=_run, args=(run_id, cases, reset), daemon=True,
            name=f"simulation-{run_id}",
        )
        _WORKER.start()
        return status()


def cancel() -> SimulationState:
    with _LOCK:
        if _STATE.status != "running":
            return status()
        _CANCEL.set()
    # The worker checks the flag between calls; it cannot interrupt a call
    # already in flight, so this returns before the run has necessarily
    # stopped. The caller polls status() to see the "cancelled" transition.
    return status()


def _append_step(step: SimulationStep) -> None:
    with _LOCK:
        _STATE.steps.append(step)
        _STATE.completed += 1


def _finish(final_status: Status, error: str | None = None) -> None:
    with _LOCK:
        _STATE.status = final_status
        _STATE.error = error
        _STATE.finished_at = _now()


def _step_from_result(
    *, index: int, total: int, mode: str, case: dict, result, elapsed_ms: int
) -> SimulationStep:
    if result.error:
        return SimulationStep(
            index=index, total=total, mode=mode, case_id=case["id"],
            query=case["query"], difficulty=case.get("difficulty"),
            response="", error=result.error, model=result.metrics.model,
            tier=result.metrics.tier, input_tokens=0, output_tokens=0,
            tokens=0, latency_ms=elapsed_ms,
            cost_usd=0.0, cache_hit=False, rag_used=False, sources=[],
            confidence=0.0, blocked=False, guardrail_events=[],
            passed=False, reason=result.error,
            trace=[s.model_dump() for s in result.trace],
        )

    verdict = evaluation.grade(case, result)
    m = result.metrics
    return SimulationStep(
        index=index, total=total, mode=mode, case_id=case["id"],
        query=case["query"], difficulty=case.get("difficulty"),
        # Truncated: this feeds a live UI feed, not the audit trail (which
        # already has its own, separately masked copy).
        response=(result.response or "")[:400],
        error=None, model=m.model, tier=m.tier,
        input_tokens=m.input_tokens, output_tokens=m.output_tokens,
        tokens=m.total_tokens,
        latency_ms=m.latency_ms, cost_usd=m.cost_usd, cache_hit=m.cache_hit,
        rag_used=m.rag_used, sources=list(result.sources), confidence=m.confidence,
        blocked=m.blocked, guardrail_events=list(m.guardrail_events),
        passed=verdict["passed"], reason=verdict["reason"],
        trace=[s.model_dump() for s in result.trace],
    )


def reset_for_tests() -> None:
    """Drop all module state and block until any worker thread has exited. Tests only."""
    global _WORKER
    with _LOCK:
        _CANCEL.set()
        worker = _WORKER
    if worker is not None and worker.is_alive():
        worker.join(timeout=5)
    with _LOCK:
        _WORKER = None
        _CANCEL.clear()
        _STATE.status = "idle"
        _STATE.run_id = ""
        _STATE.total = 0
        _STATE.completed = 0
        _STATE.started_at = None
        _STATE.finished_at = None
        _STATE.error = None
        _STATE.steps = []


def _run(run_id: str, cases: list[dict], reset: bool) -> None:
    try:
        if reset:
            metrics_service.init_db()
            metrics_service.reset()
        semantic_cache.clear()

        total = len(cases) * 2
        index = 0
        for mode in ("baseline", "optimized"):
            # A fresh cache per mode keeps the optimized-arm hit rate honest —
            # it measures repeats within this run, not leftovers from baseline.
            semantic_cache.clear()
            session_id = f"sim-{run_id}-{mode}"

            for case in cases:
                if _CANCEL.is_set():
                    _finish("cancelled")
                    return

                index += 1
                started = time.perf_counter()
                try:
                    result = chat_service.handle(case["query"], session_id, mode)
                except Exception as exc:  # noqa: BLE001 - the worker thread must not die
                    elapsed = int((time.perf_counter() - started) * 1000)
                    _append_step(SimulationStep(
                        index=index, total=total, mode=mode, case_id=case["id"],
                        query=case["query"], difficulty=case.get("difficulty"),
                        response="", error=str(exc), model=None, tier=None,
                        input_tokens=0, output_tokens=0,
                        tokens=0, latency_ms=elapsed, cost_usd=0.0,
                        cache_hit=False, rag_used=False, sources=[],
                        confidence=0.0, blocked=False, guardrail_events=[],
                        passed=False, reason=str(exc), trace=[],
                    ))
                    continue

                elapsed = int((time.perf_counter() - started) * 1000)
                _append_step(_step_from_result(
                    index=index, total=total, mode=mode, case=case,
                    result=result, elapsed_ms=elapsed,
                ))
                time.sleep(_CALL_DELAY_S)

        _finish("completed")
    except Exception as exc:  # noqa: BLE001 - surfaced via status(), not a crash
        _finish("failed", error=str(exc))
