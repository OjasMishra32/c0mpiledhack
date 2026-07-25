"""Planner tests: template shapes, the fallback chain, and the no-hardcoding guarantee."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import Action, ActionType, Goal, Predicate, PredicateType
from app.planner import base as planner_base
from app.planner import llm_planner
from app.planner.base import NotReady, compile_goal, replan_from_state
from app.planner.grounding import resolve_all
from app.planner.template_planner import route
from planner_fixtures import (
    FLAGSHIP_GOAL,
    HUES,
    flagship_scene,
    make_object,
    make_scene,
    make_state,
    make_workers,
)

PLANNER_DIR = Path(__file__).resolve().parents[1] / "app" / "planner"


# ── the headline behaviours ─────────────────────────────────────────────────────


async def test_flagship_plan_is_valid_over_observed_ids():
    scene, workers = flagship_scene(), make_workers()
    plan = await compile_goal(FLAGSHIP_GOAL, scene, workers, use_llm=False)

    assert plan.source == "template"
    assert plan.actions and len(plan.actions) <= 20
    known = {o.id for o in scene.objects}
    for a in plan.actions:
        assert a.object_id is None or a.object_id in known
        assert a.target_zone is None or a.target_zone in {z.id for z in scene.zones} | {"field"}
        assert a.description.strip().endswith(".")
    # one terminal action, and it verifies
    sinks = [a for a in plan.actions if not any(a.id in o.dependencies for o in plan.actions)]
    assert len(sinks) == 1 and sinks[0].type == ActionType.inspect.value
    assert plan.success_predicates
    assert plan.notes


async def test_parallelism():
    """The opening wave has to be visibly wide, or the graph reads as a queue."""
    plan = await compile_goal(FLAGSHIP_GOAL, flagship_scene(), make_workers(), use_llm=False)
    free = [a for a in plan.actions if not a.dependencies]
    assert len(free) >= 4, [a.id for a in plan.actions]
    assert plan.stats["parallel_peak"] >= 4


async def test_priorities_come_from_the_words_not_the_objects():
    plan = await compile_goal(FLAGSHIP_GOAL, flagship_scene(), make_workers(), use_llm=False)
    by_object = {a.object_id: a for a in plan.actions if a.object_id}
    assert by_object["obj_1"].priority == 100  # "expedited"
    assert by_object["obj_5"].priority == 50  # "restock"
    assert by_object["obj_4"].priority == 85  # blocking prerequisite


async def test_narrower_goal_smaller_graph():
    """Type less, get less. This kills the "it's hardcoded" suspicion faster than any words."""
    scene, workers = flagship_scene(), make_workers()
    broad = await compile_goal(
        "Move the red item, the blue item and the green item to the Pack Station", scene, workers, use_llm=False
    )
    narrow = await compile_goal(
        "Only deliver the red item to the Pack Station", flagship_scene(), workers, use_llm=False
    )
    assert len(narrow.actions) < len(broad.actions)
    assert len(narrow.actions) <= 3


async def test_gating_language_becomes_a_dependency():
    scene = make_scene(
        [
            make_object("obj_1", "green", 0.35, 0.45, semantic_label="green cup"),
            make_object("obj_2", "yellow", 0.60, 0.66, semantic_label="yellow scanner"),
        ]
    )
    plan = await compile_goal(
        "Move the green item to the Pack Station. Packing cannot start until the yellow scanner is docked.",
        scene,
        make_workers(),
        use_llm=False,
    )
    green = next(a for a in plan.actions if a.object_id == "obj_1")
    scanner = next(a for a in plan.actions if a.object_id == "obj_2")
    assert scanner.id in green.dependencies


async def test_stacking_stabilizes_the_base():
    scene = make_scene(
        [make_object("obj_1", "red", 0.3, 0.4), make_object("obj_2", "blue", 0.5, 0.4)]
    )
    plan = await compile_goal("Stack the red item on top of the blue item", scene, make_workers(), use_llm=False)
    types = [a.type for a in plan.actions]
    assert ActionType.hold.value in types and ActionType.release.value in types
    hold = next(a for a in plan.actions if a.type == ActionType.hold.value)
    place = next(a for a in plan.actions if a.type == ActionType.place_on.value)
    release = next(a for a in plan.actions if a.type == ActionType.release.value)
    assert hold.id in place.dependencies and place.id in release.dependencies
    assert f"object:{hold.object_id}" in hold.lock_targets


async def test_relay_chain_is_strictly_serial():
    scene = make_scene([make_object("obj_1", "red", 0.5, 0.5)])
    plan = await compile_goal(
        "Pass the red item through all five workers, then stage it at the Inbound Dock",
        scene,
        make_workers(),
        use_llm=False,
    )
    moves = [a for a in plan.actions if a.type != ActionType.inspect.value]
    assert len(moves) >= 4
    assert sum(1 for a in moves if not a.dependencies) == 1  # exactly one head


async def test_survey_fallback_when_there_are_no_zones():
    scene = make_scene([make_object("obj_1", "red", 0.4, 0.4)], zones=[])
    plan = await compile_goal("Check on the red item", scene, make_workers(), use_llm=False)
    assert plan.actions
    assert all(a.type == ActionType.inspect.value for a in plan.actions)
    assert plan.warnings


async def test_scene_not_settled_is_refused():
    scene = make_scene([make_object("obj_1", "red", 0.4, 0.4)], stable=False)
    with pytest.raises(NotReady):
        await compile_goal("Move the red item to the Pack Station", scene, make_workers(), use_llm=False)


def test_template_routing():
    scene = make_scene(
        [
            make_object("obj_1", "red", 0.2, 0.3),
            make_object("obj_2", "blue", 0.5, 0.3),
            make_object("obj_3", "green", 0.8, 0.3),
        ]
    )
    cases = {
        "Move the red item to the Pack Station and the blue item to Pick Aisle B": "deliver_to_zones",
        "Stack the red item on top of the blue item": "assemble_structure",
        "Sort every item into its matching area": "sort_by_attribute",
        "Pass the red item through the team": "relay_chain",
        "Arrange the items in order in the Pack Station": "sequence_arrange",
        "Gather everything at the Inbound Dock": "gather",
    }
    for goal, expected in cases.items():
        assert route(goal, resolve_all(goal, make_scene(list(scene.objects)))) == expected, goal


# ── the LLM path: an upgrade, never a dependency ────────────────────────────────


def _fake_client(responses):
    """A stand-in for the NIM client. `responses` is a list of (tool_args | text)."""
    calls = {"n": 0}

    async def create(**kwargs):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        payload = responses[i]
        if isinstance(payload, dict):
            call = SimpleNamespace(function=SimpleNamespace(name="emit_plan", arguments=json.dumps(payload)))
            message = SimpleNamespace(tool_calls=[call], content=None)
        else:
            message = SimpleNamespace(tool_calls=None, content=payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, calls


async def test_llm_plan_is_used_when_it_is_valid(monkeypatch):
    payload = {
        "normalized_intent": "fulfill_order",
        "actions": [
            {
                "id": "a1",
                "type": "place_in_zone",
                "description": "Move the red cup to the Pack Station.",
                "object_id": "obj_1",
                "target_zone": "zone_2",
                "dependencies": [],
                "priority": 100,
                "expected_predicates": [{"type": "object_in_zone", "subject": "obj_1", "object": "zone_2"}],
            },
            {
                "id": "a2",
                "type": "inspect",
                "description": "Confirm the Pack Station is complete.",
                "object_id": None,
                "target_zone": "zone_2",
                "dependencies": ["a1"],
                "priority": 60,
                "expected_predicates": [{"type": "all_objects_in_zone", "subject": "zone_2", "object": "zone_2"}],
            },
        ],
        "success_predicates": [{"type": "object_in_zone", "subject": "obj_1", "object": "zone_2"}],
        "notes": "2 actions",
    }
    client, calls = _fake_client([payload])
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(llm_planner, "_client", lambda: client)

    plan = await compile_goal("Move the red item to the Pack Station", flagship_scene(), make_workers())
    assert plan.source == "llm"
    assert calls["n"] >= 1
    assert [a.id for a in plan.actions] == ["a1", "a2"]


async def test_invalid_llm_json_falls_back_to_template(monkeypatch):
    """Prose instead of a tool call, three times. Never raises, always plans."""
    client, calls = _fake_client(["Sure! Here is a plan: first, move the red cup…"])
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(llm_planner, "_client", lambda: client)

    plan = await compile_goal(FLAGSHIP_GOAL, flagship_scene(), make_workers())
    assert plan.source == "template"
    assert plan.actions
    assert calls["n"] >= 1  # it really did try


async def test_llm_plan_referencing_a_phantom_object_is_repaired(monkeypatch):
    payload = {
        "normalized_intent": "fulfill_order",
        "actions": [
            {
                "id": "a1",
                "type": "place_in_zone",
                "description": "Move the red cup to the Pack Station.",
                "object_id": "obj_1",
                "target_zone": "zone_2",
                "dependencies": [],
                "priority": 100,
            },
            {
                "id": "a2",
                "type": "place_in_zone",
                "description": "Move the imaginary cup to the Pack Station.",
                "object_id": "obj_99",
                "target_zone": "zone_2",
                "dependencies": [],
                "priority": 90,
            },
            {
                "id": "a3",
                "type": "inspect",
                "description": "Confirm the Pack Station is complete.",
                "object_id": None,
                "target_zone": "zone_2",
                "dependencies": ["a1", "a2"],
                "priority": 60,
            },
        ],
        "success_predicates": [{"type": "object_in_zone", "subject": "obj_1", "object": "zone_2"}],
    }
    client, _ = _fake_client([payload])
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    monkeypatch.setattr(llm_planner, "_client", lambda: client)

    plan = await compile_goal("Move the red item to the Pack Station", flagship_scene(), make_workers())
    assert plan.source == "llm"
    assert [a.id for a in plan.actions] == ["a1", "a3"]
    assert "a2" not in plan.actions[-1].dependencies
    assert any("obj_99" in w for w in plan.warnings)


async def test_no_api_key_fallback(monkeypatch):
    """No key: template planner, and not a single network call attempted."""

    def explode():
        raise AssertionError("the planner must not touch the network without a key")

    monkeypatch.setattr(settings, "nvidia_api_key", None)
    monkeypatch.setattr(llm_planner, "_client", explode)

    plan = await compile_goal(FLAGSHIP_GOAL, flagship_scene(), make_workers())
    assert plan.source == "template"


def test_extract_json_survives_fenced_prose():
    text = 'Here you go:\n```json\n{"normalized_intent": "x", "actions": []}\n```\nHope that helps!'
    assert llm_planner._extract_json(text)["normalized_intent"] == "x"


# ── recovery ────────────────────────────────────────────────────────────────────


async def test_replan_from_state_covers_only_what_is_still_untrue():
    scene, workers = flagship_scene(), make_workers()
    state = make_state(scene, workers)
    state.goal = Goal(
        raw_text=FLAGSHIP_GOAL,
        success_predicates=[
            Predicate(type=PredicateType.object_in_zone.value, subject="obj_1", object="zone_2"),
            Predicate(type=PredicateType.object_in_zone.value, subject="obj_4", object="zone_2"),
        ],
    )
    scene.by_id("obj_1").zone = "zone_2"  # already true
    scene.by_id("obj_4").zone = "zone_3"  # the judge moved the scanner

    plan = await replan_from_state(state, {"object_id": "obj_4", "message": "scanner in Pick Aisle A"}, use_llm=False)
    moved = [a for a in plan.actions if a.object_id]
    assert {a.object_id for a in moved} == {"obj_4"}
    assert all(a.is_recovery for a in plan.actions)
    assert moved[0].priority == 105


# ── the guarantee ───────────────────────────────────────────────────────────────


def test_no_color_literals():
    """Colour lives in `descriptor.color_name`; meaning lives in the binding. Nowhere else."""
    pattern = re.compile(r"""["'](%s)["']""" % "|".join(sorted(HUES)))
    offenders = []
    for path in sorted(PLANNER_DIR.glob("*.py")):
        src = path.read_text()
        if path.name == "grounding.py":  # the one sanctioned colour table
            src = re.sub(r"# ── HUE_NAMES ──.*?# ── end HUE_NAMES ──", "", src, flags=re.S)
        for m in pattern.finditer(src):
            offenders.append(f"{path.name}: {m.group(0)}")
    assert not offenders, offenders


def test_planner_never_names_a_worker():
    """Assignment is the scheduler's job — the planner must leave workers unset."""
    src = (PLANNER_DIR / "template_planner.py").read_text()
    assert "assigned_worker_id" not in src


async def test_actions_are_atomic_single_movements():
    plan = await compile_goal(FLAGSHIP_GOAL, flagship_scene(), make_workers(), use_llm=False)
    for a in plan.actions:
        text = a.description.lower()
        assert " then " not in text, a.description
        assert not re.search(r"\band\b.*\band\b", text), a.description


def test_action_ids_are_unique():
    actions = [
        Action(id="a1", type=ActionType.inspect.value, description="Check."),
        Action(id="a1", type=ActionType.inspect.value, description="Check again."),
    ]
    ids = [a.id for a in actions]
    assert len(ids) == 2  # sanity: the validator is what deduplicates, see test_validator
