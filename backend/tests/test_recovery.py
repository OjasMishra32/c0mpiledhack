import pytest

from app import recovery
from app.models import (
    Action,
    ActionStatus,
    ActionType,
    ObservedObject,
    Predicate,
    PredicateType,
    WorkerStatus,
)


def make_object(obj_id="obj_1", zone="zone_1", confidence=0.9, **kw):
    return ObservedObject(id=obj_id, zone=zone, confidence=confidence, **kw)


def make_action(**kwargs):
    defaults = dict(id="a1", type=ActionType.place_in_zone, description="move obj_1 to zone_2",
                     object_id="obj_1", target_zone="zone_2", status=ActionStatus.verified)
    defaults.update(kwargs)
    return Action(**defaults)


def test_deviation_debounce(hive_state):
    hive_state.execution_status = "executing"
    obj = make_object(zone="zone_1")
    hive_state.world.objects.append(obj)
    recovery._LAST_ZONE["obj_1"] = "zone_2"  # simulate the object was in zone_2 last tick

    first = recovery.detect_all(hive_state)
    assert not any(t.kind == "wrong_object_moved" for t in first)

    second = recovery.detect_all(hive_state)
    assert any(t.kind == "wrong_object_moved" for t in second)


def test_regression_detected(hive_state):
    obj = make_object(zone="zone_1")  # left its verified zone_2
    hive_state.world.objects.append(obj)
    action = make_action(
        status=ActionStatus.verified,
        expected_predicates=[Predicate(type=PredicateType.object_in_zone, subject="obj_1", object="zone_2")],
    )
    hive_state.actions[action.id] = action

    triggers = recovery.detect_verification_regressed(hive_state)
    assert any(t.action_id == action.id for t in triggers)


def test_no_regression_when_still_in_zone(hive_state):
    obj = make_object(zone="zone_2")
    hive_state.world.objects.append(obj)
    action = make_action(
        status=ActionStatus.verified,
        expected_predicates=[Predicate(type=PredicateType.object_in_zone, subject="obj_1", object="zone_2")],
    )
    hive_state.actions[action.id] = action
    assert recovery.detect_verification_regressed(hive_state) == []


def test_recovery_isolates(hive_state):
    """Only dependents of the affected object are paused; siblings keep running."""
    packing = make_action(id="a_pack", object_id="scanner", status=ActionStatus.dispatched,
                           dependencies=[])
    dependent = make_action(id="a_pack_dep", object_id="materials", status=ActionStatus.available,
                             dependencies=["a_pack"])
    sibling = make_action(id="a_restock", object_id="bulk_item", status=ActionStatus.dispatched,
                           dependencies=[])
    for a in (packing, dependent, sibling):
        hive_state.actions[a.id] = a

    trigger = recovery.DeviationTrigger(kind="wrong_object_moved", object_id="scanner")
    plan = recovery.plan_recovery(trigger, hive_state)
    import asyncio
    asyncio.run(recovery.apply_recovery_plan(plan, hive_state))

    assert dependent.status == ActionStatus.blocked
    assert sibling.status == ActionStatus.dispatched  # untouched — picking/restock never stopped


@pytest.mark.asyncio
async def test_timeout_ladder(hive_state):
    worker = hive_state.workers["worker_a"]
    worker.connected = True
    action = make_action(id="a_timeout", assigned_worker_id="worker_a", status=ActionStatus.dispatched,
                          retry_count=0)
    hive_state.actions[action.id] = action

    # attempts 1 and 2: reissue to the same worker, no reassignment
    for _ in range(2):
        trigger = recovery.DeviationTrigger(kind="worker_timeout", action_id=action.id, worker_id="worker_a")
        plan = recovery.plan_recovery(trigger, hive_state)
        await recovery.apply_recovery_plan(plan, hive_state)
        assert action.assigned_worker_id == "worker_a"

    # attempt 3: reassigns and drops worker confidence
    trigger = recovery.DeviationTrigger(kind="worker_timeout", action_id=action.id, worker_id="worker_a")
    plan = recovery.plan_recovery(trigger, hive_state)
    await recovery.apply_recovery_plan(plan, hive_state)
    assert action.assigned_worker_id is None
    assert action.status == ActionStatus.available
    assert worker.confidence <= 0.6


@pytest.mark.asyncio
async def test_disconnect_releases_locks(hive_state):
    worker = hive_state.workers["worker_b"]
    worker.connected = False
    action = make_action(id="a_disc", assigned_worker_id="worker_b", status=ActionStatus.dispatched)
    hive_state.actions[action.id] = action
    hive_state.locks["object:obj_1"] = action.id

    trigger = recovery.DeviationTrigger(kind="worker_disconnected", action_id=action.id, worker_id="worker_b")
    plan = recovery.plan_recovery(trigger, hive_state)
    await recovery.apply_recovery_plan(plan, hive_state)

    assert "object:obj_1" not in hive_state.locks
    assert action.assigned_worker_id is None
    assert worker.status == WorkerStatus.ready


def test_conflicting_manipulation_detected(hive_state):
    a = make_action(id="a1", object_id="shared_obj", status=ActionStatus.dispatched)
    b = make_action(id="a2", object_id="shared_obj", status=ActionStatus.dispatched)
    hive_state.actions[a.id] = a
    hive_state.actions[b.id] = b
    triggers = recovery.detect_conflicting_manipulation(hive_state)
    assert len(triggers) == 1
    assert triggers[0].object_id == "shared_obj"


@pytest.mark.asyncio
async def test_recovery_deadlock_unblocks_stuck_actions(hive_state):
    hive_state.execution_status = "executing"
    stuck = make_action(id="a_stuck", status=ActionStatus.blocked)
    hive_state.actions[stuck.id] = stuck

    trigger = recovery.DeviationTrigger(kind="scheduler_deadlock")
    plan = recovery.plan_recovery(trigger, hive_state)
    await recovery.apply_recovery_plan(plan, hive_state)
    assert stuck.status == ActionStatus.available
