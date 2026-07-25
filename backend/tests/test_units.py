"""Unit tests for the pieces most likely to break the demo."""

from __future__ import annotations

import pytest

from app import scheduler, verifier
from app.host_commands import HOST_HANDLERS as H
from app.key_pool import KeyPool
from app.models import Action, Evidence, Predicate
from app.planner.grounding import resolve, resolve_all
from app.planner.validator import topo_layers
from app.websocket_manager import ws

from app.orchestrator import _SchedulerView as _view

from .conftest import complete, run_ticks, send

pytestmark = pytest.mark.asyncio


# ── perception is genuinely promptable, not a manifest ──────────────────────


async def test_scene_is_discovered_not_configured(state):
    """Ids are synthetic; colours are measured. Nothing is preregistered."""
    assert len(state.scene.objects) >= 4
    for o in state.scene.objects:
        assert o.id.startswith("obj_")
        assert o.descriptor.color_hex.startswith("#")
        assert o.role is None  # meaning arrives from grounding, never from config


async def test_grounding_binds_language_to_observed_objects(state):
    target = state.scene.objects[0]
    color = target.descriptor.color_name
    b = resolve(f"the {color} item", state.scene)
    assert b.object_id is not None
    assert state.scene.by_id(b.object_id).descriptor.color_name == color


async def test_grounding_ambiguity_is_not_silently_guessed(state):
    """Two objects of the same colour must produce a question, not a coin flip."""
    a, b = state.scene.objects[0], state.scene.objects[1]
    b.descriptor.color_name = a.descriptor.color_name
    b.descriptor.dominant_hsv = a.descriptor.dominant_hsv
    g = resolve_all(f"move the {a.descriptor.color_name} item to the Pack Station", state.scene)
    assert g.ambiguous, "near-tie must be flagged for the operator"


async def test_narrower_goal_yields_smaller_graph(state):
    one = state.scene.objects[0].descriptor.color_name
    await H["host_compile_goal"]({"text": f"Move the {one} item to the Pack Station."})
    small = len(state.actions)
    colors = [o.descriptor.color_name for o in state.scene.objects[:3]]
    await H["host_compile_goal"](
        {"text": f"Move the {colors[0]} item to the Pack Station, "
                 f"the {colors[1]} item to Pick Aisle A, "
                 f"and the {colors[2]} item to Pick Aisle B."}
    )
    assert len(state.actions) > small, "a broader objective must produce a bigger plan"


# ── graph ───────────────────────────────────────────────────────────────────


async def test_topo_layers_expose_parallelism(state):
    acts = [
        Action(id="a1", type="inspect", description=""),
        Action(id="a2", type="inspect", description=""),
        Action(id="a3", type="inspect", description="", dependencies=["a1", "a2"]),
    ]
    layers = topo_layers(acts)
    assert layers[0] == ["a1", "a2"]
    assert layers[1] == ["a3"]


# ── scheduler ───────────────────────────────────────────────────────────────


async def test_unreachable_worker_is_never_viable(state):
    obj = state.scene.objects[0]
    a = Action(id="a1", type="place_in_zone", description="", object_id=obj.id, target_zone="zone_2")
    for w in state.workers.values():
        w.reachable_zones = ["zone_3"]
    cands = scheduler.score_workers(a, _view(state))
    assert all(not c.viable for c in cands)
    assert "reach" in scheduler.describe_block(a, _view(state))


async def test_lock_conflict_prevents_same_object_twice(state):
    obj = state.scene.objects[0]
    a1 = Action(id="a1", type="place_in_zone", description="", object_id=obj.id,
                target_zone="zone_1", status="available", lock_targets=[f"object:{obj.id}"])
    a2 = Action(id="a2", type="place_in_zone", description="", object_id=obj.id,
                target_zone="zone_2", status="available", lock_targets=[f"object:{obj.id}"])
    state.actions = {"a1": a1, "a2": a2}
    batch = scheduler.select_batch(_view(state))
    assert len(batch) == 1, "the same object may never be manipulated by two actions at once"


async def test_assignment_always_explains_itself(state):
    """An empty explanation is a hole in the middle of the demo."""
    await H["host_compile_goal"]({"text": state.scenario.build_goal(state.scene)})
    await H["host_start_execution"]({})
    await run_ticks(3)
    assigned = [a for a in state.actions.values() if a.assigned_worker_id]
    assert assigned
    for a in assigned:
        assert len(a.assignment_reason.split()) >= 3


async def test_fairness_spreads_work(state):
    await H["host_compile_goal"]({"text": state.scenario.build_goal(state.scene)})
    await H["host_start_execution"]({})
    for _ in range(12):
        for a in list(state.actions.values()):
            if a.status in ("dispatched", "acknowledged", "executing"):
                complete(a.id)
        await run_ticks(2)
    used = {w.id for w in state.workers.values() if w.assignment_count > 0}
    assert len(used) >= 3, "work must visibly spread across the collective"


# ── verification ────────────────────────────────────────────────────────────


async def test_weighted_evidence_needs_corroboration(state):
    """Vision alone must not clear the bar; vision + a worker report must."""
    a = Action(id="a1", type="inspect", description="")
    a.evidence = [Evidence(kind="vision", confidence=0.85)]
    assert not verifier.evaluate(a, state).verified
    a.evidence.append(Evidence(kind="worker_report", confidence=1.0))
    r = verifier.evaluate(a, state)
    assert r.verified and r.score >= 0.7


async def test_host_override_alone_verifies(state):
    a = Action(id="a1", type="inspect", description="")
    a.evidence = [Evidence(kind="host_override", confidence=1.0)]
    assert verifier.evaluate(a, state).verified


async def test_unsatisfied_predicate_blocks_verification(state):
    """A worker saying "done" cannot verify an action the world contradicts."""
    obj = state.scene.objects[0]
    wrong = next(z.id for z in state.scene.zones if z.id != obj.zone)
    a = Action(id="a1", type="place_in_zone", description="", object_id=obj.id, target_zone=wrong,
               expected_predicates=[Predicate(type="object_in_zone", subject=obj.id, object=wrong)])
    a.evidence = [Evidence(kind="worker_report", confidence=1.0)]
    r = verifier.evaluate(a, state)
    assert not r.verified
    assert r.failed


async def test_duplicate_completion_counted_once(state):
    await H["host_compile_goal"]({"text": state.scenario.build_goal(state.scene)})
    await H["host_start_execution"]({})
    await run_ticks(2)
    a = next(x for x in state.actions.values() if x.status == "dispatched")
    for _ in range(4):
        send(a.assigned_worker_id, "worker_completed", action_id=a.id)
    await run_ticks(1)
    assert sum(1 for e in a.evidence if e.kind == "worker_report") == 1


async def test_worker_cannot_complete_another_workers_action(state):
    await H["host_compile_goal"]({"text": state.scenario.build_goal(state.scene)})
    await H["host_start_execution"]({})
    await run_ticks(2)
    a = next(x for x in state.actions.values() if x.status == "dispatched")
    imposter = next(w.id for w in state.workers.values() if w.id != a.assigned_worker_id)
    send(imposter, "worker_completed", action_id=a.id)
    await run_ticks(1)
    assert not any(e.kind == "worker_report" for e in a.evidence)


# ── transport ───────────────────────────────────────────────────────────────


async def test_worker_slots_are_claimed_not_created(state):
    assert len(state.workers) == 5
    ws.token_map.clear()


async def test_public_dict_never_leaks_token(state):
    w = state.workers["worker_a"]
    w.session_token = "secret-token"
    assert "session_token" not in w.public_dict()


async def test_event_sequence_is_gapless(state):
    await state.emit("t", "one")
    await state.emit("t", "two")
    await state.emit("t", "three")
    seqs = [e.seq for e in state.events]
    assert seqs == sorted(seqs)
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


# ── key pool ────────────────────────────────────────────────────────────────


async def test_key_pool_dedupes_and_spreads():
    pool = KeyPool(keys=["k1", " k1 ", "", "k2"], per_key_rpm=2)
    assert pool.count == 2, "duplicate keys share one real server-side limit"
    a = await pool.lease("stream-a")
    b = await pool.lease("stream-b")
    assert {a.index, b.index} == {0, 1}, "concurrent streams fan out to least-loaded"


async def test_key_pool_sticky_affinity_keeps_cache_warm():
    pool = KeyPool(keys=["k1", "k2"], per_key_rpm=10)
    first = await pool.lease("planner")
    for _ in range(4):
        again = await pool.lease("planner")
        assert again.index == first.index, "one stream stays on one key while it has headroom"


async def test_key_pool_cools_a_rate_limited_key():
    pool = KeyPool(keys=["k1", "k2"], per_key_rpm=10)
    await pool.penalize(0)
    lease = await pool.lease("x")
    assert lease.index == 1, "a hot key is skipped rather than re-hammered"
    await pool.note_success(1)
    assert pool.stats()["cooling"][1] == 0.0


async def test_key_pool_never_hangs_when_saturated():
    pool = KeyPool(keys=["k1"], per_key_rpm=1)
    await pool.lease("x")
    lease = await pool.lease("x", max_wait=0.2)  # saturated
    assert lease.key == "k1", "degrade to a slow request rather than stalling the demo"
