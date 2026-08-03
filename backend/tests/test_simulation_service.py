"""The Play button's background runner.

chat_service.handle() is stubbed here — these tests are about the runner's own
correctness (progress accounting, cancellation, error containment, reset
behaviour), not about the gateway. That is what test_gateway.py and a live run
are for.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.models.schemas import ChatMetrics, ChatResponse, TraceStep
from app.services import chat_service, metrics_service, simulation_service


def _ok_response(mode: str, *, error: str | None = None) -> ChatResponse:
    return ChatResponse(
        response="canned answer",
        metrics=ChatMetrics(
            mode=mode, model="fake-model", tier="simple",
            input_tokens=5, output_tokens=5, total_tokens=10,
            latency_ms=5, cost_usd=0.0001, confidence=0.9,
        ),
        trace=[TraceStep(layer="llm", detail="ok", duration_ms=5)],
        sources=[],
        error=error,
    )


@pytest.fixture(autouse=True)
def fast_and_isolated(monkeypatch):
    """No real network, no real delay, and a clean slate before/after each test."""
    monkeypatch.setattr(simulation_service, "_CALL_DELAY_S", 0.0)
    simulation_service.reset_for_tests()
    yield
    simulation_service.reset_for_tests()


def _wait_until_terminal(timeout_s: float = 5.0) -> simulation_service.SimulationState:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = simulation_service.status()
        if state.status != "running":
            return state
        time.sleep(0.02)
    raise TimeoutError(f"simulation did not finish within {timeout_s}s")


class TestHappyPath:
    def test_quick_run_completes_with_16_steps(self, monkeypatch):
        monkeypatch.setattr(chat_service, "handle", lambda q, s, mode: _ok_response(mode))
        started = simulation_service.start(size="quick", reset=False)
        assert started.status == "running"
        assert started.total == 16

        final = _wait_until_terminal()
        assert final.status == "completed"
        assert final.completed == 16
        assert len(final.steps) == 16
        # Baseline block runs fully before optimized starts, matching the CLI
        # script and the PRD's demo script ("baseline, then switch to optimized").
        assert [s.mode for s in final.steps[:8]] == ["baseline"] * 8
        assert [s.mode for s in final.steps[8:]] == ["optimized"] * 8

    def test_full_run_is_46_steps(self, monkeypatch):
        monkeypatch.setattr(chat_service, "handle", lambda q, s, mode: _ok_response(mode))
        simulation_service.start(size="full", reset=False)
        final = _wait_until_terminal()
        assert final.total == 46
        assert final.completed == 46

    def test_step_carries_the_trace_through(self, monkeypatch):
        monkeypatch.setattr(chat_service, "handle", lambda q, s, mode: _ok_response(mode))
        simulation_service.start(size="quick", reset=False)
        final = _wait_until_terminal()
        assert final.steps[0].trace == [{"layer": "llm", "detail": "ok", "duration_ms": 5}]
        assert final.steps[0].input_tokens == 5
        assert final.steps[0].output_tokens == 5


class TestResetBehavior:
    """Whether the runner *asks* for a clean slate — not whether rows land, since
    chat_service.handle is stubbed above and its real writes are exactly what's
    being replaced. Row-level proof that reset genuinely clears the dashboard
    lives in the live end-to-end run, not a unit test."""

    def test_reset_true_calls_metrics_reset_before_running(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(metrics_service, "init_db", lambda: calls.append("init_db"))
        monkeypatch.setattr(metrics_service, "reset", lambda: calls.append("reset"))
        monkeypatch.setattr(chat_service, "handle", lambda q, s, mode: _ok_response(mode))

        simulation_service.start(size="quick", reset=True)
        _wait_until_terminal()
        assert calls == ["init_db", "reset"]

    def test_reset_false_never_touches_metrics(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(metrics_service, "init_db", lambda: calls.append("init_db"))
        monkeypatch.setattr(metrics_service, "reset", lambda: calls.append("reset"))
        monkeypatch.setattr(chat_service, "handle", lambda q, s, mode: _ok_response(mode))

        simulation_service.start(size="quick", reset=False)
        _wait_until_terminal()
        assert calls == []


class TestErrorHandling:
    def test_a_chat_response_error_becomes_a_failed_step_without_stopping_the_run(self, monkeypatch):
        monkeypatch.setattr(
            chat_service, "handle",
            lambda q, s, mode: _ok_response(mode, error="gateway unreachable"),
        )
        simulation_service.start(size="quick", reset=False)
        final = _wait_until_terminal()
        assert final.status == "completed"
        assert final.completed == 16
        assert all(s.error == "gateway unreachable" for s in final.steps)
        assert all(s.passed is False for s in final.steps)

    def test_an_exception_from_chat_service_does_not_kill_the_worker(self, monkeypatch):
        """The worker thread has no supervisor — an unhandled exception here
        would silently freeze the run at "running" forever with no error surfaced."""
        def boom(q, s, mode):
            raise RuntimeError("unexpected crash")

        monkeypatch.setattr(chat_service, "handle", boom)
        simulation_service.start(size="quick", reset=False)
        final = _wait_until_terminal()
        assert final.status == "completed"
        assert final.completed == 16
        assert all(s.error == "unexpected crash" for s in final.steps)


class TestConcurrencyAndCancellation:
    def test_second_start_while_running_is_rejected(self, monkeypatch):
        # Block the very first call indefinitely so "still running" is a fact,
        # not a timing guess — the same guess bit a manual browser test earlier
        # (a fast fake gateway finished a 16-call run before the check landed).
        release = threading.Event()

        def blocking_handle(q, s, mode):
            release.wait(timeout=5)
            return _ok_response(mode)

        monkeypatch.setattr(chat_service, "handle", blocking_handle)
        simulation_service.start(size="quick", reset=False)
        try:
            with pytest.raises(simulation_service.SimulationError, match="already running"):
                simulation_service.start(size="quick", reset=False)
        finally:
            release.set()
        _wait_until_terminal()

    def test_cancel_stops_before_the_next_case(self, monkeypatch):
        """Deterministic version of "cancel mid-run": block exactly the 3rd
        call, confirm exactly 2 steps landed, cancel, then unblock and confirm
        no 4th call happens."""
        calls = 0
        reached_third = threading.Event()
        release_third = threading.Event()

        def handle(q, s, mode):
            nonlocal calls
            calls += 1
            if calls == 3:
                reached_third.set()
                release_third.wait(timeout=5)
            return _ok_response(mode)

        monkeypatch.setattr(chat_service, "handle", handle)
        simulation_service.start(size="full", reset=False)
        assert reached_third.wait(timeout=5), "3rd call never started"
        assert simulation_service.status().completed == 2

        cancelled = simulation_service.cancel()
        assert cancelled.status == "running"  # the 3rd call is still in flight
        release_third.set()

        final = _wait_until_terminal()
        assert final.status == "cancelled"
        assert final.completed == 3
        assert calls == 3  # no 4th call was ever started

    def test_can_start_again_after_a_cancelled_run(self, monkeypatch):
        monkeypatch.setattr(chat_service, "handle", lambda q, s, mode: _ok_response(mode))
        simulation_service.start(size="quick", reset=False)
        simulation_service.cancel()
        _wait_until_terminal()

        restarted = simulation_service.start(size="quick", reset=False)
        assert restarted.status == "running"
        final = _wait_until_terminal()
        assert final.status == "completed"
        assert final.completed == 16

    def test_cancel_with_nothing_running_is_a_no_op(self):
        state = simulation_service.cancel()
        assert state.status == "idle"
