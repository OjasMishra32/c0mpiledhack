"""Validator tests. Repairs must be silent-but-surfaced; rejections must be readable."""

from __future__ import annotations

from app.models import Action, ActionType, Predicate, PredicateType
from app.planner.base import PlanContext, PlanResult
from app.planner.validator import topo_layers, validate_and_repair
from planner_fixtures import make_object, make_scene, make_workers


def _ctx(scene=None, workers=None) -> PlanContext:
    scene = scene or make_scene(
        [make_object("obj_1", "red", 0.35, 0.45), make_object("obj_2", "blue", 0.55, 0.55)]
    )
    return PlanContext(workers=workers if workers is not None else make_workers(), scene=scene)


def _plan(actions, success=None) -> PlanResult:
    return PlanResult(actions=actions, success_predicates=success or [])


def _move(aid, object_id="obj_1", zone="zone_2", **kw) -> Action:
    return Action(
        id=aid,
        type=ActionType.place_in_zone.value,
        description="Move it there.",
        object_id=object_id,
        target_zone=zone,
        **kw,
    )


def test_cycle_detection():
    plan = _plan([_move("a5", dependencies=["a7"]), _move("a7", object_id="obj_2", dependencies=["a5"])])
    report = validate_and_repair(plan, _ctx())
    assert not report.ok
    assert "a5" in report.errors[0] and "a7" in report.errors[0]
    assert "each wait on the other" in report.errors[0]


def test_unknown_object_repaired():
    plan = _plan(
        [
            _move("a1"),
            _move("a2", object_id="obj_99"),
            Action(
                id="a3",
                type=ActionType.inspect.value,
                description="Check the station.",
                target_zone="zone_2",
                dependencies=["a1", "a2"],
            ),
        ]
    )
    report = validate_and_repair(plan, _ctx())
    assert report.ok
    assert [a.id for a in plan.actions] == ["a1", "a3"]
    assert plan.actions[-1].dependencies == ["a1"]
    assert any("obj_99" in r for r in report.repairs)


def test_unknown_zone_repaired():
    plan = _plan([_move("a1"), _move("a2", object_id="obj_2", zone="zone_77")])
    report = validate_and_repair(plan, _ctx())
    assert report.ok
    assert [a.id for a in plan.actions][0] == "a1"
    assert any("zone_77" in r for r in report.repairs)


def test_duplicate_ids_are_resuffixed():
    plan = _plan([_move("a1"), _move("a1", object_id="obj_2")])
    report = validate_and_repair(plan, _ctx())
    assert report.ok
    assert len({a.id for a in plan.actions}) == len(plan.actions)  # a terminal step is appended too
    assert {a.id for a in plan.actions} >= {"a1", "a1_2"}
    assert any("both called a1" in r for r in report.repairs)


def test_missing_predicates_and_locks_are_synthesized():
    plan = _plan([_move("a1")])
    report = validate_and_repair(plan, _ctx())
    assert report.ok
    action = plan.actions[0]
    assert action.expected_predicates[0].type == PredicateType.object_in_zone.value
    assert action.lock_targets == ["object:obj_1"]
    assert plan.success_predicates  # derived from the plan's own steps


def test_preassignment_to_an_unavailable_worker_is_dropped():
    workers = make_workers()
    workers[0].available = False
    plan = _plan([_move("a1", assigned_worker_id="worker_a"), _move("a2", object_id="obj_2")])
    report = validate_and_repair(plan, _ctx(workers=workers))
    assert report.ok
    assert plan.actions[0].assigned_worker_id is None
    assert any("unavailable worker" in r for r in report.repairs)


def test_unsupported_action_type_is_fatal():
    plan = _plan([Action(id="a1", type="teleport", description="Teleport it.", object_id="obj_1")])
    report = validate_and_repair(plan, _ctx())
    assert not report.ok
    assert "teleport" in report.errors[0]


def test_unreachable_zone_is_fatal():
    workers = make_workers(reachable={w: ["zone_1", "field"] for w in ("worker_a", "worker_b", "worker_c", "worker_d", "worker_e")})
    plan = _plan([_move("a1", zone="zone_2")])
    report = validate_and_repair(plan, _ctx(workers=workers))
    assert not report.ok
    assert "Pack Station" in report.errors[0]  # the label, not the id


def test_two_sinks_get_one_terminal_action():
    plan = _plan([_move("a1"), _move("a2", object_id="obj_2", zone="zone_4")])
    report = validate_and_repair(plan, _ctx())
    assert report.ok
    terminal = plan.actions[-1]
    assert terminal.type == ActionType.inspect.value
    assert set(terminal.dependencies) == {"a1", "a2"}
    assert any("finish line" in r for r in report.repairs)


def test_empty_plan_is_rejected():
    report = validate_and_repair(_plan([]), _ctx())
    assert not report.ok
    assert "no actions" in report.errors[0]


def test_self_dependency_is_stripped():
    plan = _plan([_move("a1", dependencies=["a1"])])
    report = validate_and_repair(plan, _ctx())
    assert report.ok
    assert plan.actions[0].dependencies == []


def test_topo_layers_are_columns():
    actions = [
        _move("a1"),
        _move("a2", object_id="obj_2"),
        Action(id="a3", type=ActionType.inspect.value, description="Check.", dependencies=["a1", "a2"]),
    ]
    assert topo_layers(actions) == [["a1", "a2"], ["a3"]]


def test_messages_are_written_for_a_person():
    plan = _plan([_move("a5", dependencies=["a7"]), _move("a7", object_id="obj_2", dependencies=["a5"])])
    report = validate_and_repair(plan, _ctx())
    for message in report.errors + report.repairs:
        assert message[0].isupper() and message.endswith(".")
        for jargon in ("Traceback", "ValidationError", "None", "NetworkX", "DiGraph"):
            assert jargon not in message


def test_success_predicates_survive_a_repair_pass():
    given = [Predicate(type=PredicateType.object_in_zone.value, subject="obj_1", object="zone_2")]
    plan = _plan([_move("a1")], success=given)
    validate_and_repair(plan, _ctx())
    assert plan.success_predicates == given
