import pytest

from app import verifier
from app.models import (
    Action,
    ActionStatus,
    Evidence,
    EvidenceKind,
    ObservedObject,
    Predicate,
    PredicateType,
)


def make_object(obj_id="obj_1", zone="zone_2", confidence=0.85, source="vision", **kw):
    return ObservedObject(id=obj_id, zone=zone, confidence=confidence, source=source, **kw)


def make_action(**kwargs):
    defaults = dict(
        id="a1", type="place_in_zone", description="place obj_1 in zone_2",
        object_id="obj_1", target_zone="zone_2", status=ActionStatus.awaiting_verification.value,
        expected_predicates=[Predicate(type=PredicateType.object_in_zone.value, subject="obj_1", object="zone_2")],
    )
    defaults.update(kwargs)
    return Action(**defaults)


def test_weighted_verification(hive_state):
    hive_state.scene.objects.append(make_object(confidence=0.85))
    action = make_action()
    hive_state.actions.append(action)

    result = verifier.evaluate(action, hive_state)
    assert result.score == pytest.approx(0.51, abs=0.01)
    assert result.verified is False

    hive_state.evidence[action.id] = [Evidence(kind=EvidenceKind.worker_report.value, confidence=1.0, weight=0.30)]
    result2 = verifier.evaluate(action, hive_state)
    assert result2.score == pytest.approx(0.81, abs=0.01)
    assert result2.verified is True


def test_host_override_always_verifies(hive_state):
    action = make_action(expected_predicates=[])
    hive_state.evidence[action.id] = [Evidence(kind=EvidenceKind.host_override.value, confidence=1.0, weight=1.0)]
    result = verifier.evaluate(action, hive_state)
    assert result.verified is True
    assert result.score == 1.0


def test_predicate_object_in_zone_true(hive_state):
    hive_state.scene.objects.append(make_object(zone="zone_2"))
    pred = Predicate(type=PredicateType.object_in_zone.value, subject="obj_1", object="zone_2")
    assert verifier.check_predicate(pred, hive_state) is not None


def test_predicate_object_in_zone_false(hive_state):
    hive_state.scene.objects.append(make_object(zone="zone_1"))
    pred = Predicate(type=PredicateType.object_in_zone.value, subject="obj_1", object="zone_2")
    assert verifier.check_predicate(pred, hive_state) is None


def test_predicate_object_in_zone_missing_object(hive_state):
    pred = Predicate(type=PredicateType.object_in_zone.value, subject="obj_ghost", object="zone_2")
    assert verifier.check_predicate(pred, hive_state) is None


def test_simulation_evidence_clears_bar_alone(hive_state):
    hive_state.scene.objects.append(make_object(confidence=0.95, source="simulation"))
    action = make_action()
    result = verifier.evaluate(action, hive_state)
    assert result.verified is True


def test_narrate_mentions_each_source(hive_state):
    evidence = [
        Evidence(kind=EvidenceKind.vision.value, confidence=0.84, weight=0.60),
        Evidence(kind=EvidenceKind.worker_report.value, confidence=1.0, weight=0.30),
    ]
    summary = verifier.narrate(evidence, 1.0)
    assert "tracker 84%" in summary
    assert "worker confirmed" in summary
