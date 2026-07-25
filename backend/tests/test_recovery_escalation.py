"""Recovery escalation: the model recompiles what the deterministic floor handles clumsily.

The floor always ships. What these tests pin is the one property that must never drift — the
model is allowed to *improve* a recovery and is never allowed to *gate* one. Every failure
mode of the escalation (no key, no answer, an exception, a plan that drops work, a bug in the
escalation itself) has to land on the deterministic plan without disturbing the tick.

Nothing here touches the network: the NIM client is stubbed the same way test_planner.py
stubs it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app import orchestrator
from app import recovery as recovery_engine
from app.config import settings
from app.models import Action, Goal, Predicate
from app.planner import base as planner_base
from app.planner import llm_planner
from app.recovery import DeviationTrigger
from app.vision import bridge as world_model

from .conftest import drive_to_completion, run_ticks

pytestmark = pytest.mark.asyncio


# ── stubs ───────────────────────────────────────────────────────────────────


def _fake_client(responses):
    """Stand-in for the NIM client. Each response is a tool payload (dict), prose (str),
    an exception to raise, or a number of seconds to hang for."""
    calls = {"n": 0}

    async def create(**kwargs):
        payload = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        if isinstance(payload, BaseException):
            raise payload
        if isinstance(payload, (int, float)):
            await asyncio.sleep(payload)  # the planner's own window closes over this
            payload = ""
        if isinstance(payload, dict):
            call = SimpleNamespace(
                function=SimpleNamespace(name="emit_plan", arguments=json.dumps(payload))
            )
            message = SimpleNamespace(tool_calls=[call], content=None)
        else:
            message = SimpleNamespace(tool_calls=None, content=payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def _count_replans(monkeypatch):
    """Wrap replan_from_state so a test can assert the model was never even considered."""
    seen = {"n": 0}
    real = planner_base.replan_from_state

    async def counting(*args, **kwargs):
        seen["n"] += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(planner_base, "replan_from_state", counting)
    return seen


# ── the world these tests describe ──────────────────────────────────────────


def _setup(state, *, displaced: int = 2):
    """Two deliveries HIVE verified, then an operator walked off with `displaced` of them.

    Two at once is the case the deterministic strategy admits it handles clumsily: it repairs
    one object, leaves a duplicate action behind for it, and says nothing about the order the
    rest of the objective should now run in.
    """
    reachable = {z for w in state.workers.values() for z in w.reachable_zones}
    target = next(z.id for z in state.scene.zones if z.id in reachable)
    elsewhere = next(z.id for z in state.scene.zones if z.id in reachable and z.id != target)
    first, second = state.scene.objects[0], state.scene.objects[1]

    actions: dict[str, Action] = {}
    for aid, obj in (("a1", first), ("a2", second)):
        actions[aid] = Action(
            id=aid,
            type="place_in_zone",
            description=f"Move the {obj.display_label()} to {state.zone_label(target)}.",
            object_id=obj.id,
            target_zone=target,
            status="verified",
            lock_targets=[f"object:{obj.id}"],
            expected_predicates=[Predicate(type="object_in_zone", subject=obj.id, object=target)],
        )
    actions["a3"] = Action(
        id="a3",
        type="inspect",
        description=f"Confirm {state.zone_label(target)} is complete.",
        target_zone=target,
        dependencies=["a1", "a2"],
        status="queued",
        expected_predicates=[Predicate(type="all_objects_in_zone", subject=target, object=target)],
    )
    state.actions = actions
    state.goal = Goal(
        raw_text="Move both items to the staging area.",
        status="executing",
        success_predicates=[
            Predicate(type="object_in_zone", subject=first.id, object=target),
            Predicate(type="object_in_zone", subject=second.id, object=target),
        ],
    )
    state.execution_status = "executing"

    for obj in (first, second):  # verified means they really were there
        world_model.set_object_zone(state, obj.id, target)
    for obj in (first, second)[:displaced]:  # …and then they weren't
        world_model.set_object_zone(state, obj.id, elsewhere)

    return SimpleNamespace(first=first, second=second, target=target, elsewhere=elsewhere)


def _trigger(w) -> DeviationTrigger:
    """The regression HIVE's own detector would have raised for the first object."""
    return DeviationTrigger(
        kind="verification_regressed",
        message=f"{w.first.display_label()} left the staging area after verification.",
        action_ids=["a1"],
        object_id=w.first.id,
    )


def _replan_payload(w, *, objects=None):
    """A recompiled remaining objective: the named items back, then one confirmation."""
    objs = objects if objects is not None else [w.first, w.second]
    actions = [
        {
            "id": f"n{i}",
            "type": "place_in_zone",
            "description": f"Move the {o.display_label()} to the staging area.",
            "object_id": o.id,
            "target_zone": w.target,
            "dependencies": [],
            "priority": 105 if i == 1 else 90,
        }
        for i, o in enumerate(objs, start=1)
    ]
    actions.append(
        {
            "id": "nz",
            "type": "inspect",
            "description": "Confirm the staging area is complete.",
            "object_id": None,
            "target_zone": w.target,
            "dependencies": [a["id"] for a in actions],
            "priority": 60,
        }
    )
    return {
        "normalized_intent": "restore_and_finish",
        "actions": actions,
        "success_predicates": [
            {"type": "object_in_zone", "subject": o.id, "object": w.target} for o in objs
        ],
        "notes": f"{len(actions)} actions",
    }


# ── the escalation fires and is used ────────────────────────────────────────


async def test_low_confidence_escalates_and_the_model_plan_is_used(state, monkeypatch):
    w = _setup(state)
    client, calls = _fake_client([_replan_payload(w)])
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(llm_planner, "_client", lambda: client)

    floor = recovery_engine.plan_recovery(_trigger(w), state)
    assert floor.confidence < recovery_engine.ESCALATION_CONFIDENCE, "two moved at once"

    plan = await recovery_engine.plan_recovery_async(_trigger(w), state)

    assert calls["n"] >= 1, "the model really was asked"
    assert plan.source == "llm"
    assert plan.confidence >= recovery_engine.ESCALATION_CONFIDENCE
    assert len(plan.insert_actions) == 3
    assert all(a.is_recovery for a in plan.insert_actions), "escalated work is recovery work"
    assert {a.object_id for a in plan.insert_actions if a.object_id} == {w.first.id, w.second.id}
    assert any(e.type == "replanning" for e in state.events), "the escalation is visible"
    assert any(e.type == "plan_replanned" for e in state.events)


async def test_the_escalated_plan_replaces_the_old_one_rather_than_racing_it(state, monkeypatch):
    w = _setup(state)
    client, _ = _fake_client([_replan_payload(w)])
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(llm_planner, "_client", lambda: client)

    plan = await recovery_engine.plan_recovery_async(_trigger(w), state)
    new_ids = [a.id for a in plan.insert_actions]
    orchestrator._apply_recovery(plan)

    # Ids never collide with the graph they replace, and ordering survived the rename.
    assert not set(new_ids) & {"a1", "a2", "a3"}
    assert set(new_ids) <= set(state.actions)
    assert state.actions[new_ids[-1]].dependencies == new_ids[:-1]
    assert state.actions[new_ids[0]].status == "available"  # no dependencies → open now
    assert state.actions[new_ids[-1]].status == "queued"  # waits on the two moves

    # The graph it replaced is terminal, including the two verified deliveries the world undid.
    assert [state.actions[i].status for i in ("a1", "a2", "a3")] == ["cancelled"] * 3

    # One live action per object. Two would each hold object:<id> and neither would ever run.
    live = [a for a in state.actions.values() if not a.terminal and a.object_id]
    assert len(live) == len({a.object_id for a in live})
    assert all(f"object:{a.object_id}" in a.lock_targets for a in live)


async def test_an_adopted_replan_still_runs_to_completion(state, monkeypatch):
    """The real loop end to end. A recompiled graph that cannot finish is worse than a blunt
    one that can — two live actions over one object would deadlock on `object:<id>`."""
    w = _setup(state)
    client, _ = _fake_client([_replan_payload(w)])
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(llm_planner, "_client", lambda: client)

    for _ in range(6):  # detect debounces for two ticks, then the escalation lands
        await run_ticks(1)
        if any(e.type == "plan_replanned" for e in state.events):
            break
    assert any(e.type == "plan_replanned" for e in state.events), "the escalation was adopted"

    assert await drive_to_completion(), "an adopted replan must still be able to finish"
    assert not [a for a in state.actions.values() if a.status not in ("verified", "cancelled")]
    assert state.metrics.recoveries >= 1


# ── every failure mode lands on the deterministic plan ──────────────────────


async def test_a_model_exception_leaves_the_deterministic_plan_standing(state, monkeypatch):
    w = _setup(state)
    client, calls = _fake_client([RuntimeError("upstream exploded")])
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(llm_planner, "_client", lambda: client)

    plan = await recovery_engine.plan_recovery_async(_trigger(w), state)

    assert calls["n"] >= 1
    assert plan.source == "deterministic"
    assert not plan.supersede_action_ids, "nothing was superseded"
    assert [a.object_id for a in plan.insert_actions] == [w.first.id], "the surgical retrieval"
    assert plan.pause_action_ids == ["a3"]
    assert any(e.type == "replan_declined" for e in state.events)


async def test_a_model_timeout_leaves_the_deterministic_plan_standing(state, monkeypatch):
    w = _setup(state)
    client, calls = _fake_client([30.0])  # hangs; replan_from_state owns the window
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(settings, "replan_timeout_seconds", 0.05)
    monkeypatch.setattr(llm_planner, "_client", lambda: client)

    plan = await recovery_engine.plan_recovery_async(_trigger(w), state)

    assert calls["n"] == 1
    assert plan.source == "deterministic"
    assert [a.object_id for a in plan.insert_actions] == [w.first.id]
    assert any(e.type == "replan_declined" for e in state.events)


async def test_a_replan_that_drops_work_is_declined(state, monkeypatch):
    """The model may reshape the remaining objective. It may not quietly shrink it."""
    w = _setup(state)
    client, _ = _fake_client([_replan_payload(w, objects=[w.first])])  # forgot the second item
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(llm_planner, "_client", lambda: client)

    plan = await recovery_engine.plan_recovery_async(_trigger(w), state)

    assert plan.source == "deterministic"
    declined = [e for e in state.events if e.type == "replan_declined"]
    assert declined and w.second.id in declined[-1].message


async def test_a_bug_in_the_escalation_is_contained(state, monkeypatch):
    """Not just the await — the whole escalation is wrapped. It runs inside the tick."""
    w = _setup(state)
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")

    def boom(_state):
        raise ValueError("escalation is broken")

    monkeypatch.setattr(recovery_engine, "_owed_objects", boom)

    plan = await recovery_engine.plan_recovery_async(_trigger(w), state)

    assert plan.source == "deterministic"
    assert [a.object_id for a in plan.insert_actions] == [w.first.id]
    assert any(e.type == "replan_declined" for e in state.events)


async def test_a_failed_escalation_never_breaks_the_tick(state, monkeypatch):
    """The real loop, not the unit: deviation → escalation → failure → keep ticking."""
    w = _setup(state)
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")

    async def explode(*args, **kwargs):
        raise RuntimeError("planner is down")

    monkeypatch.setattr(planner_base, "replan_from_state", explode)

    await run_ticks(4)  # detect debounces for two ticks before a trigger fires

    assert state.execution_status == "executing", "the loop survived"
    assert any(e.type == "deviation_detected" for e in state.events)
    assert any(e.type == "replan_declined" for e in state.events)
    assert any(a.is_recovery for a in state.actions.values()), "the floor still recovered"
    assert any(e.type == "recovery_started" for e in state.events)


# ── when the model must not be consulted at all ─────────────────────────────


async def test_a_confident_recovery_never_calls_the_model(state, monkeypatch):
    """One object out of place is exactly what the deterministic strategy is for."""
    w = _setup(state, displaced=1)
    client, calls = _fake_client([_replan_payload(w)])
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(llm_planner, "_client", lambda: client)
    replans = _count_replans(monkeypatch)

    plan = await recovery_engine.plan_recovery_async(_trigger(w), state)

    assert plan.confidence >= recovery_engine.ESCALATION_CONFIDENCE
    assert plan.source == "deterministic"
    assert replans["n"] == 0 and calls["n"] == 0, "the model was never consulted"
    assert not [e for e in state.events if e.type in ("replanning", "plan_replanned", "replan_declined")]


async def test_no_api_key_never_calls_the_model(state, monkeypatch):
    w = _setup(state)  # low confidence: the escalation path is reachable

    def explode():
        raise AssertionError("recovery must not touch the network without a key")

    monkeypatch.setattr(settings, "nvidia_api_key", None)
    monkeypatch.setattr(llm_planner, "_client", explode)
    replans = _count_replans(monkeypatch)

    plan = await recovery_engine.plan_recovery_async(_trigger(w), state)

    assert plan.confidence < recovery_engine.ESCALATION_CONFIDENCE, "it would have escalated"
    assert plan.source == "deterministic"
    assert replans["n"] == 0
    assert not any(e.type == "replanning" for e in state.events)
