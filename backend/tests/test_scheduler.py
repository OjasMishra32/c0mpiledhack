"""Scheduler tests.

The explanation string is on the projector, so it gets tested like a feature, not a log line.
"""

from __future__ import annotations

from collections import Counter

from app.models import Action, ActionStatus, ActionType
from app.planner.base import compile_goal
from app.scheduler import (
    Scheduler,
    assign_actions,
    best_worker,
    describe_block,
    score_workers,
    select_batch,
    unlock_dependents,
)
from planner_fixtures import (
    FLAGSHIP_GOAL,
    flagship_scene,
    make_object,
    make_scene,
    make_state,
    make_workers,
)


def _move(aid, object_id, zone, priority=70, **kw) -> Action:
    return Action(
        id=aid,
        type=ActionType.place_in_zone.value,
        description="Move it there.",
        object_id=object_id,
        target_zone=zone,
        priority=priority,
        status=ActionStatus.available.value,
        lock_targets=[f"object:{object_id}"],
        **kw,
    )


def _counterfactual(reason: str) -> str:
    """The "why not the other one" half of an explanation: everything after the first sentence."""
    return reason.partition(". ")[2]


def _others(state, callsign: str) -> list[str]:
    return [w.callsign for w in state.workers if w.callsign != callsign]


def _simple_state(actions=None):
    scene = make_scene(
        [
            make_object("obj_1", "red", 0.30, 0.45),
            make_object("obj_2", "blue", 0.55, 0.60),
            make_object("obj_3", "green", 0.60, 0.20),
        ]
    )
    return make_state(scene, make_workers(), actions or [])


# ── hard filters ────────────────────────────────────────────────────────────────


def test_scheduler_reachability():
    """A worker whose reachable_zones excludes the target is never viable. No score overrides it."""
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    alpha = state.worker_by_id("worker_a")
    alpha.reachable_zones = ["zone_1", "zone_3", "field"]  # aisle-side only
    alpha.position.x, alpha.position.y = 0.31, 0.45  # and standing right next to the object
    candidates = {c.worker_id: c for c in score_workers(state.actions[0], state)}
    assert candidates["worker_a"].viable is False  # proximity cannot buy reachability
    assert "cannot reach Pack Station" in candidates["worker_a"].reason_short
    assert candidates["worker_c"].viable is True  # Runner C reaches everything


def test_capability_filter():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    for w in state.workers:
        w.supported_actions = [ActionType.inspect.value]
    assert not any(c.viable for c in score_workers(state.actions[0], state))
    assert "can perform place in zone" in describe_block(state.actions[0], state)


def test_a_worker_holding_something_else_is_not_viable():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    state.scene.by_id("obj_2").held_by = "worker_c"
    candidates = {c.worker_id: c for c in score_workers(state.actions[0], state)}
    assert candidates["worker_c"].viable is False
    assert candidates["worker_c"].reason_short == "already holding something else"


def test_scheduler_lock_conflict():
    """Two actions locking object:obj_1 are never batched in one tick."""
    state = _simple_state([_move("a1", "obj_1", "zone_2"), _move("a2", "obj_1", "zone_4")])
    batch = select_batch(state)
    assert len(batch) == 1
    blocked = next(a for a in state.actions if a.id not in {b[0].id for b in batch})
    assert blocked.blocked_reason


def test_one_worker_gets_at_most_one_action_per_tick():
    state = _simple_state(
        [_move("a1", "obj_1", "zone_2"), _move("a2", "obj_2", "zone_2"), _move("a3", "obj_3", "zone_2")]
    )
    batch = select_batch(state)
    assigned = [assignment.worker_id for _, assignment in batch]
    assert len(assigned) == len(set(assigned))


# ── scoring behaviour ───────────────────────────────────────────────────────────


def test_scheduler_reassign_on_unavailable():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    action = state.actions[0]
    first = best_worker(action, state)
    assert first is not None
    state.worker_by_id(first.worker_id).available = False
    second = best_worker(action, state)
    assert second is not None and second.worker_id != first.worker_id


def test_a_worker_who_just_failed_is_avoided():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    action = state.actions[0]
    winner = best_worker(action, state)
    state.worker_by_id(winner.worker_id).confidence = 0.4  # dropped after a failed step
    assert best_worker(action, state).worker_id != winner.worker_id


def test_busy_workers_lose_to_idle_ones():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    action = state.actions[0]
    winner = best_worker(action, state)
    state.worker_by_id(winner.worker_id).current_action_id = "a9"
    assert best_worker(action, state).worker_id != winner.worker_id


def test_scheduler_fairness():
    """Over 20 assignments, no worker exceeds twice the mean."""
    state = _simple_state()
    counts: Counter[str] = Counter()
    for i in range(20):
        object_id = f"obj_{(i % 3) + 1}"
        action = _move(f"a{i}", object_id, "zone_2" if i % 2 else "zone_4")
        state.actions = [action]
        winner = best_worker(action, state)
        assert winner is not None
        counts[winner.worker_id] += 1
        state.worker_by_id(winner.worker_id).assignment_count += 1
    mean = 20 / len(state.workers)
    assert max(counts.values()) <= 2 * mean, counts
    assert len(counts) >= 3, counts  # work visibly spreads


async def test_opening_wave_uses_several_different_workers():
    """If two workers get everything, judges read it as a script."""
    scene, workers = flagship_scene(), make_workers()
    plan = await compile_goal(FLAGSHIP_GOAL, scene, workers, use_llm=False)
    state = make_state(scene, workers, plan.actions)
    batch = select_batch(state)
    assert len(batch) >= 4, [(a.id, a.blocked_reason) for a in state.actions]
    assert len({assignment.worker_id for _, assignment in batch}) == len(batch)


# ── explanations ────────────────────────────────────────────────────────────────


def test_explanation_nonempty():
    """An empty explanation on the projector is a hole in the middle of the demo."""
    state = _simple_state(
        [_move("a1", "obj_1", "zone_2"), _move("a2", "obj_2", "zone_4"), _move("a3", "obj_3", "zone_1")]
    )
    batch = select_batch(state)
    assert batch
    for action, assignment in batch:
        assert len(assignment.reason.split()) >= 3
        assert assignment.reason.startswith(assignment.callsign)
        assert assignment.factors  # the receipts, shown on hover


def test_explanation_names_the_runner_up():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    winner = best_worker(state.actions[0], state)
    others = [c for c in score_workers(state.actions[0], state) if c.viable]
    assert len(others) > 1  # the fixture has five workers; a vacuous pass would hide a regression
    assert others[1].callsign in winner.reason
    assert winner.reason.rstrip().endswith(".")


def test_single_viable_worker_still_gets_a_counterfactual():
    """A zone only one person can reach is the case that most needs explaining, so it is exactly
    the wrong moment for the explanation to go quiet."""
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    for w in state.workers:
        w.reachable_zones = ["zone_1", "zone_3", "field"]  # nobody reaches the Pack Station...
    state.worker_by_id("worker_d").reachable_zones = ["zone_2", "zone_4", "field"]  # ...except DELTA
    candidates = score_workers(state.actions[0], state)
    viable = [c for c in candidates if c.viable]
    assert [c.callsign for c in viable] == ["DELTA"]  # genuinely one option

    tail = _counterfactual(viable[0].reason)
    assert tail, viable[0].reason  # a counterfactual is not optional
    assert any(cs in tail for cs in _others(state, "DELTA")), tail
    assert "Pack Station" in tail  # and it says what excluded them


def test_counterfactual_falls_back_to_why_a_blocked_worker_was_excluded():
    """With no viable runner-up, borrow the strongest blocked candidate's own exclusion reason."""
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    for w in state.workers:
        w.reachable_zones = ["zone_1", "zone_3", "field"]
    for wid in ("worker_c", "worker_d"):
        state.worker_by_id(wid).reachable_zones = ["zone_1", "zone_2", "zone_3", "zone_4", "field"]
    state.scene.by_id("obj_2").held_by = "worker_d"  # DELTA could reach it, but has full hands

    winner = best_worker(state.actions[0], state)
    assert winner.callsign == "CHARLIE"
    # A momentary blocker outranks the four who structurally cannot reach the zone.
    assert _counterfactual(winner.reason) == "DELTA was already holding something else."


def test_every_assignment_in_a_batch_ends_by_naming_a_different_worker():
    state = _simple_state(
        [_move("a1", "obj_1", "zone_2"), _move("a2", "obj_2", "zone_4"), _move("a3", "obj_3", "zone_1")]
    )
    batch = select_batch(state)
    assert len(batch) == 3
    for _, assignment in batch:
        tail = _counterfactual(assignment.reason)
        assert tail.endswith("."), assignment.reason
        named = [cs for cs in _others(state, assignment.callsign) if cs in tail]
        assert named, assignment.reason  # somebody else, by name
        assert assignment.callsign not in tail  # and never the assignee talking about themselves


def test_every_viable_candidate_carries_its_own_counterfactual():
    """orchestrator._apply_attribution can promote any viable candidate and keep its reason
    string, so a counterfactual on the top-scorer alone is not enough."""
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    viable = [c for c in score_workers(state.actions[0], state) if c.viable]
    assert len(viable) == 5
    for cand in viable:
        tail = _counterfactual(cand.reason)
        assert tail.endswith("."), cand.reason
        assert any(cs in tail for cs in _others(state, cand.callsign)), cand.reason


def test_a_lone_responder_is_not_given_an_imaginary_rival():
    """Nobody else on the floor is an honest answer. Inventing a runner-up is a lie on stage."""
    scene = make_scene([make_object("obj_1", "red", 0.30, 0.45)])
    state = make_state(scene, make_workers(1), [_move("a1", "obj_1", "zone_2")])
    winner = best_worker(state.actions[0], state)
    assert _counterfactual(winner.reason) == "No other responder was available."


def test_explanation_names_the_zone_not_a_vague_area():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    batch = select_batch(state)
    assert batch
    for _, assignment in batch:
        assert "Pack Station" in assignment.reason
        assert "that zone" not in assignment.reason
        assert "that area" not in assignment.reason
        assert "zone_" not in assignment.reason  # labels, never ids
        if "activity there" in assignment.reason:
            # "there" is only allowed once the zone has already been named out loud, and the
            # three-factor trim must never drop the clause that named it.
            assert "already standing in" in assignment.reason.split("activity there")[0], assignment.reason


def test_a_forced_pick_still_explains_itself_specifically():
    """When every soft factor is against the only available worker, the explanation falls back to
    the hard filters they *did* clear — never a bare "selected"."""
    busy = _move("a0", "obj_2", "zone_2")
    busy.status = ActionStatus.dispatched.value  # so the Pack Station counts as contended
    state = _simple_state([busy, _move("a1", "obj_1", "zone_2")])
    for w in state.workers:
        w.reachable_zones = ["zone_1", "zone_3", "field"]
    only = state.worker_by_id("worker_d")
    only.reachable_zones = ["zone_2", "zone_4", "field"]
    only.position.x, only.position.y = 0.05, 0.95  # far away
    only.current_action_id = "a9"  # busy
    only.confidence = 0.5  # shaky
    only.assignment_count = 9  # and carrying every prior assignment

    winner = best_worker(state.actions[1], state)
    assert winner.callsign == "DELTA"
    head, _, tail = winner.reason.partition(". ")
    assert "Pack Station" in head, winner.reason  # specific, not "selected because reasons"
    assert len(head.split()) >= 5, winner.reason
    assert any(cs in tail for cs in _others(state, "DELTA")), winner.reason


def test_explanation_stays_quotable():
    """One sentence of factors, at most three of them, then exactly one counterfactual sentence."""
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    for cand in score_workers(state.actions[0], state):
        if not cand.viable:
            continue
        head, _, tail = cand.reason.partition(". ")
        assert head.count(",") <= 2, cand.reason
        assert tail.count(".") == 1, cand.reason


def test_blocked_reason_explains_the_wait_in_plain_language():
    dep = _move("a1", "obj_1", "zone_2")
    dep.status = ActionStatus.dispatched.value
    waiting = _move("a2", "obj_2", "zone_2", dependencies=["a1"])
    state = _simple_state([dep, waiting])
    reason = describe_block(waiting, state)
    assert reason.startswith("waiting:")
    assert "obj_" not in reason  # people, not ids


def test_no_worker_can_reach_is_said_out_loud():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    for w in state.workers:
        w.reachable_zones = ["zone_1", "field"]
    assert describe_block(state.actions[0], state) == "waiting: no worker can reach Pack Station"


# ── the loop's safety nets ──────────────────────────────────────────────────────


def test_deadlock_guard_fires_after_three_dry_ticks():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    for w in state.workers:
        w.reachable_zones = ["zone_1", "field"]
    scheduler = Scheduler()
    assert scheduler.tick(state).deadlock is None
    assert scheduler.tick(state).deadlock is None
    tick = scheduler.tick(state)
    assert tick.deadlock and "Pack Station" in tick.deadlock
    assert "Rebuilding the plan." in tick.deadlock


def test_no_deadlock_while_work_is_in_flight():
    running = _move("a1", "obj_1", "zone_2")
    running.status = ActionStatus.executing.value
    state = _simple_state([running])
    scheduler = Scheduler()
    for _ in range(5):
        assert scheduler.tick(state).deadlock is None


def test_completed_plan_is_not_a_deadlock():
    done = _move("a1", "obj_1", "zone_2")
    done.status = ActionStatus.verified.value
    state = _simple_state([done])
    scheduler = Scheduler()
    for _ in range(5):
        assert scheduler.tick(state).deadlock is None


def test_unlock_dependents_returns_newly_ready_actions():
    first = _move("a1", "obj_1", "zone_2")
    first.status = ActionStatus.verified.value
    second = _move("a2", "obj_2", "zone_2", dependencies=["a1"])
    second.status = ActionStatus.queued.value
    third = _move("a3", "obj_3", "zone_2", dependencies=["a2"])
    third.status = ActionStatus.queued.value
    state = _simple_state([first, second, third])
    assert [a.id for a in unlock_dependents(state, [])] == ["a2"]


def test_assign_actions_is_the_orchestrator_entry_point():
    state = _simple_state([_move("a1", "obj_1", "zone_2")])
    tick = assign_actions(state)
    assert tick.batch and tick.deadlock is None
    action, assignment = tick.batch[0]
    assert action.status == ActionStatus.available.value  # only the orchestrator mutates status
    assert assignment.viable
