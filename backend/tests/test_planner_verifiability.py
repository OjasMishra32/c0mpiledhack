"""Every action a template emits must be *verifiable*, or the run stops on it.

`verifier.evaluate` refuses to verify an action while any of its predicates is unsatisfied, so
a predicate nothing in the pipeline can ever report does not weaken the action — it strands it,
and every dependent action behind it. That failure mode is invisible in a unit test of the
planner (the graph looks perfect) and fatal on stage, so it is tested here against the real
verifier and the real orchestrator loop.

Goals are built from the colours the scene actually discovered, never from literals — same
rule as the planner itself.
"""

from __future__ import annotations

import pytest

from app.host_commands import HOST_HANDLERS as H
from app.verifier import check_predicate

from .conftest import drive_to_completion, run_ticks

pytestmark = pytest.mark.asyncio

UNVERIFIABLE = {
    # the holder is unknown at plan time and nothing reports a human's hands
    "object_held_by",
    # satisfied only by evidence already on the action, never by the world
    "worker_acknowledged",
    "manually_verified",
}


def _colors(state) -> list[str]:
    return [o.descriptor.color_name for o in state.scene.objects]


async def _compile(goal: str) -> None:
    await H["host_compile_goal"]({"text": goal})


def _assert_predicates_are_reportable(state) -> None:
    for action in state.actions.values():
        assert action.expected_predicates, f"{action.id} has nothing to verify"
        for p in action.expected_predicates:
            assert p.type not in UNVERIFIABLE, f"{action.id} asserts {p.type}, which nothing can report"
            if p.type == "sequence_completed":
                # naming its own id here would make the predicate wait on itself forever
                assert p.subject not in state.actions, f"{action.id} waits on itself"
            holds, _ = check_predicate(p, state)
            assert isinstance(holds, bool)  # the verifier has a branch for this type at all


async def test_gated_delivery_plan_verifies_every_action(state):
    await _compile(state.scenario.build_goal(state.scene))
    _assert_predicates_are_reportable(state)

    await H["host_start_execution"]({})
    assert await drive_to_completion(), [
        (a.id, a.type, a.status, a.blocked_reason) for a in state.actions.values()
    ]
    stalled = [a.id for a in state.actions.values() if a.status not in ("verified", "cancelled")]
    assert not stalled, stalled


async def test_stack_plan_verifies_every_action(state):
    """hold / place_on / release used to strand: the stack asserted facts nothing reports."""
    colors = _colors(state)
    await _compile(f"Stack the {colors[0]} item on top of the {colors[1]} item")
    _assert_predicates_are_reportable(state)

    await H["host_start_execution"]({})
    assert await drive_to_completion(), [
        (a.id, a.type, a.status, a.blocked_reason) for a in state.actions.values()
    ]
    assert any(a.type == "hold" for a in state.actions.values())
    assert all(a.status in ("verified", "cancelled") for a in state.actions.values())


async def test_relay_plan_verifies_every_action(state):
    """A relay hop asserted `worker_acknowledged`, which check_predicate can never satisfy."""
    colors = _colors(state)
    await _compile(f"Pass the {colors[0]} item through the team, then stage it at the Pack Station")
    _assert_predicates_are_reportable(state)

    await H["host_start_execution"]({})
    assert await drive_to_completion(), [
        (a.id, a.type, a.status, a.blocked_reason) for a in state.actions.values()
    ]


async def test_a_plan_never_stalls_on_its_own_first_tick(state):
    """Whatever the objective, something must be dispatchable immediately."""
    colors = _colors(state)
    for goal in (
        state.scenario.build_goal(state.scene),
        f"Gather everything at the Pack Station, starting with the {colors[0]} item",
        f"Sort every item into its matching area",
        f"Only move the {colors[-1]} item to Pick Aisle B",
    ):
        await state.reset("warehouse_fulfillment")
        for w in state.workers.values():
            w.connected, w.status, w.available = True, "ready", True
        await _compile(goal)
        await H["host_start_execution"]({})
        await run_ticks(2)
        live = [a for a in state.actions.values() if a.status != "queued"]
        assert live, f"nothing could start for: {goal}"
