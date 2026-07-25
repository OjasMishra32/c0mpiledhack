"""Grounding tests — the promptability claim.

A judge will put objects HIVE has never seen on the table and type a sentence nobody wrote.
These tests are that judge.
"""

from __future__ import annotations

import random

from app.planner.grounding import color_name_from_hsv, resolve, resolve_all
from planner_fixtures import HUES, make_object, make_scene


def test_grounding_color():
    scene = make_scene(
        [
            make_object("obj_1", "red", 0.3, 0.4),
            make_object("obj_2", "blue", 0.6, 0.4),
            make_object("obj_3", "green", 0.5, 0.8),
        ]
    )
    assert resolve("the red one", scene).object_id == "obj_1"
    assert resolve("the blue one", scene).object_id == "obj_2"


def test_grounding_color_synonym_and_adjacency():
    scene = make_scene([make_object("obj_1", "cyan", 0.3, 0.3), make_object("obj_2", "red", 0.7, 0.7)])
    assert resolve("the teal object", scene).object_id == "obj_1"


def test_grounding_semantic():
    """Two objects of the same colour: the label has to break the tie."""
    scene = make_scene(
        [
            make_object("obj_1", "red", 0.3, 0.4, semantic_label="red plastic cup"),
            make_object("obj_2", "red", 0.7, 0.4, semantic_label="red folder"),
        ]
    )
    assert resolve("the cup", scene).object_id == "obj_1"
    assert resolve("the folder", scene).object_id == "obj_2"


def test_grounding_spatial():
    scene = make_scene(
        [
            make_object("obj_1", "red", 0.80, 0.4, semantic_label="red box"),
            make_object("obj_2", "blue", 0.50, 0.4, semantic_label="blue cup"),
            make_object("obj_3", "green", 0.15, 0.4, semantic_label="green box"),
        ]
    )
    assert resolve("the leftmost box", scene).object_id == "obj_3"
    assert resolve("the rightmost box", scene).object_id == "obj_1"


def test_grounding_size_superlative():
    scene = make_scene(
        [
            make_object("obj_1", "red", 0.3, 0.3, area=0.005),
            make_object("obj_2", "red", 0.7, 0.3, area=0.060),
        ]
    )
    assert resolve("the biggest red one", scene).object_id == "obj_2"
    assert resolve("the smallest red one", scene).object_id == "obj_1"


def test_grounding_ambiguous():
    """Two red objects, one red phrase. Never guess — ask."""
    scene = make_scene([make_object("obj_1", "red", 0.3, 0.4), make_object("obj_2", "red", 0.7, 0.4)])
    binding = resolve("the red item", scene)
    assert binding.object_id is None
    assert set(binding.alternatives) == {"obj_1", "obj_2"}

    result = resolve_all("Move the red item to the Pack Station", scene)
    assert result.ambiguous, "a near-tie must surface as grounding_ambiguous"
    payload = result.ambiguous_payload()
    assert payload["candidates"] and payload["message"]


def test_host_can_resolve_an_ambiguity():
    scene = make_scene([make_object("obj_1", "red", 0.3, 0.4), make_object("obj_2", "red", 0.7, 0.4)])
    result = resolve_all("Move the red item to the Pack Station", scene)
    phrase = result.ambiguous[0].phrase
    result.bind_manually(phrase, "obj_2")
    assert not result.ambiguous
    assert result.bound_object_ids == ["obj_2"]


def test_grounding_unknown_zone():
    """A place HIVE has never seen becomes an unbound chip, not a crash."""
    scene = make_scene([make_object("obj_1", "red", 0.3, 0.4)])
    result = resolve_all("Move the red item to the loading bay", scene)
    assert any("loading bay" in p.lower() for p in result.unbound_places)
    assert result.bound_object_ids == ["obj_1"]


def test_grounding_zones_and_deliveries():
    scene = make_scene(
        [make_object("obj_1", "red", 0.3, 0.4), make_object("obj_2", "orange", 0.6, 0.7)]
    )
    result = resolve_all(
        "Move the red item to the Pack Station, and restock the orange item to Pick Aisle B", scene
    )
    assert ("obj_1", "zone_2") in result.deliveries
    assert ("obj_2", "zone_4") in result.deliveries
    assert result.distinct_destinations == 2


def test_grounding_quantifier_and_remainder():
    scene = make_scene(
        [
            make_object("obj_1", "red", 0.2, 0.3),
            make_object("obj_2", "blue", 0.5, 0.3),
            make_object("obj_3", "green", 0.8, 0.3),
        ]
    )
    everything = resolve_all("Bring everything to the Inbound Dock", scene)
    assert set(everything.bound_object_ids) == {"obj_1", "obj_2", "obj_3"}

    remainder = resolve_all("Put the red one in the Pack Station and stack the other two", scene)
    assert set(remainder.bound_object_ids) == {"obj_1", "obj_2", "obj_3"}


def test_urgency_is_scoped_to_its_own_clause():
    """"restock" must not drag the expedited item down to background priority."""
    scene = make_scene(
        [make_object("obj_1", "red", 0.3, 0.4), make_object("obj_2", "orange", 0.6, 0.7)]
    )
    result = resolve_all(
        "Move the expedited red item to the Pack Station, and restock the orange item to Pick Aisle B",
        scene,
    )
    assert result.priority_for("obj_1") == 100
    assert result.priority_for("obj_2") == 50


def test_gate_language_becomes_structure():
    scene = make_scene(
        [make_object("obj_1", "green", 0.3, 0.4), make_object("obj_2", "yellow", 0.6, 0.7, semantic_label="yellow scanner")]
    )
    result = resolve_all(
        "Move the green item to the Pack Station. Packing cannot start until the yellow scanner is docked.",
        scene,
    )
    assert result.gates, "a stated prerequisite must be parsed as a gate"
    gate = result.gates[0]
    assert gate.gate_object_ids == ["obj_2"]
    assert gate.gated_zone_id == "zone_2"
    assert ("obj_2", "zone_2") in result.deliveries  # the gate object needs somewhere to be


def test_roles_replace_ids_in_language():
    scene = make_scene([make_object("obj_1", "red", 0.3, 0.4)])
    resolve_all("Move the priority red item to the Pack Station", scene)
    obj = scene.by_id("obj_1")
    assert obj.role and "red" in obj.role
    assert obj.display_label() == obj.role  # every instruction downstream speaks the role


def test_color_naming_covers_the_hue_wheel():
    for name, (hue, _hex) in HUES.items():
        assert color_name_from_hsv(hue, 210, 190) == name
    assert color_name_from_hsv(0, 5, 250) == "white"
    assert color_name_from_hsv(0, 5, 10) == "black"


def test_arbitrary_scene_binds_to_whatever_is_there():
    """Random objects, a goal naming two of them, bound to real observed ids. No manifest."""
    rng = random.Random(11)
    for _ in range(25):
        colors = rng.sample(list(HUES), 3)
        objects = [
            make_object(f"obj_{i + 1}", c, rng.uniform(0.1, 0.9), rng.uniform(0.1, 0.9))
            for i, c in enumerate(colors)
        ]
        scene = make_scene(objects)
        first, second = colors[0], colors[1]
        result = resolve_all(
            f"Move the {first} item to the Pack Station and the {second} item to Pick Aisle B", scene
        )
        assert len(result.bound_object_ids) == 2, result.unresolved_phrases
        assert result.bound_object_ids == ["obj_1", "obj_2"]
        assert ("obj_1", "zone_2") in result.deliveries
        assert ("obj_2", "zone_4") in result.deliveries
