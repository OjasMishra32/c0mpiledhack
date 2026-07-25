"""End-to-end: the flagship run, including the moment that wins the judging.

Five workers → compile → parallel execution → a worker drops out mid-action →
reassignment → completion. This is the regression alarm for everyone's changes.
"""

from __future__ import annotations

import pytest

from app.host_commands import HOST_HANDLERS as H

from .conftest import complete, drive_to_completion, run_ticks, stage_on_the_floor

pytestmark = pytest.mark.asyncio


async def test_flagship_completes_with_worker_failure(state):
    # The wave below is only observable if no delivery starts already satisfied — a random
    # spawn inside its own target zone verifies in one tick, correctly. See the helper.
    stage_on_the_floor(state)
    goal = state.scenario.build_goal(state.scene)
    await H["host_compile_goal"]({"text": goal})

    assert state.goal is not None
    assert state.goal.status == "compiled"
    assert len(state.actions) >= 6, "flagship plan should be substantial"

    await H["host_start_execution"]({})
    await run_ticks(3)

    live = [a for a in state.actions.values() if a.status in ("dispatched", "acknowledged", "executing")]
    assert len(live) >= 3, f"opening wave must be parallel, got {len(live)}"
    assert len({a.assigned_worker_id for a in live}) == len(live), "each worker takes one action"

    # Complete part of the wave so the run is genuinely mid-flight.
    for a in live[:2]:
        complete(a.id)
    await run_ticks(3)

    # Now pull a worker offline while they hold an action.
    victim = next(
        (a for a in state.actions.values() if a.status in ("dispatched", "acknowledged", "executing")),
        None,
    )
    assert victim is not None, "expected an in-flight action to disrupt"
    previous = victim.assigned_worker_id
    await H["host_inject_failure"]({"kind": "worker_down", "target_id": previous})
    await run_ticks(8)

    assert victim.assigned_worker_id != previous, "action must move off the downed worker"
    assert victim.status not in ("failed", "cancelled")
    assert state.metrics.reassignments >= 1
    assert any(e.type == "recovery_started" for e in state.events)

    assert await drive_to_completion(), "goal must reach completed"
    assert state.goal.status == "completed"
    assert state.execution_status == "completed"
    assert state.metrics.parallel_peak >= 3
    assert state.metrics.actions_verified >= 6


async def test_no_api_key_still_plans(state):
    """The entire demo must survive with no model access at all."""
    await H["host_compile_goal"]({"text": state.scenario.build_goal(state.scene)})
    assert state.goal is not None
    assert state.goal.plan_source == "template"
    assert len(state.actions) > 0


async def test_reset_is_total_and_repeatable(state):
    await H["host_compile_goal"]({"text": state.scenario.build_goal(state.scene)})
    await H["host_start_execution"]({})
    await run_ticks(3)

    for _ in range(10):
        await H["host_reset"]({})
        assert state.actions == {}
        assert state.locks == {}
        assert state.goal is None
        assert state.execution_status == "idle"
        assert state.metrics.recoveries == 0
        # Reset must never make five people re-scan a QR code.
        assert all(w.connected for w in state.workers.values())


async def test_object_deviation_isolates_rather_than_restarting(state):
    """The pitch: freeze only the affected chain; unrelated work keeps running."""
    await H["host_compile_goal"]({"text": state.scenario.build_goal(state.scene)})
    await H["host_start_execution"]({})
    await run_ticks(2)

    for a in list(state.actions.values()):
        if a.status in ("dispatched", "acknowledged", "executing"):
            complete(a.id)
    await run_ticks(4)

    verified_before = {a.id for a in state.actions.values() if a.status == "verified"}
    assert verified_before, "need at least one verified action to regress"

    await H["host_inject_failure"]({"kind": "verification_regress"})
    await run_ticks(6)

    assert state.metrics.deviations >= 1
    assert any(e.type == "deviation_detected" for e in state.events)
    # A recovery action was inserted rather than the plan being rebuilt from scratch.
    assert any(a.is_recovery for a in state.actions.values())
    # Crucially, the whole graph was NOT reset.
    assert len(state.actions) >= len(verified_before)
